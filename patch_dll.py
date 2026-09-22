#!/usr/bin/env python3
"""
一次完成 first.dll 的三类补丁：文本翻译（CSV）+ 兼容补丁 + AITXT 词库。
输出到 output/first.dll（可选：命令行第一个参数 = 额外复制到的部署路径）。

文本翻译（CSV）：
  Offset = 写入位置，Length = 最大字节数，Type = code|rsrc|font。
  - code：off-4 处为 4 字节小端长度，写入后更新长度并清零剩余
  - rsrc：off-1 处为 1 字节长度，写入后更新长度并清零剩余
  - font：无长度前缀，用 \\x00 补齐

兼容补丁（写入前逐字节校验原值）：
  1. NOTIFY -> 按 GET 分发（把 0x719E9 处的 jne 填成 NOP）
     first.dll 只实现了 GET；SSP 2.5.33+ 在 cantalk=0 时会把后台事件
     以 NOTIFY 发来，之前会收到 400 并卡死状态机。
  2. r"\\![enter,inductionmode]" 字符串长度 23 -> 0（0x79E08）
     诱导模式会让 cantalk 永远保持 false，导致后台事件走 NOTIFY
     （响应被忽略）、泡澡结束的对话不可见。

AITXT（词库）：
  aitxt_translated.txt（UTF-8）-> GBK -> 加密数据块，覆盖 PE 资源目录
  中定位到的 AITXT 资源，并更新资源数据项的 Size 字段。
  写入前做 round-trip 校验。

链接化补丁（海原雄山）：
  「自动加链接」名单由 7 段固定序列注册（push ebp / mov eax,<常量> / call
  0x4AA838 / pop ecx），名单区后紧接代码，没有空位。这里把最后一段（木野さん）
  的 call 重定向到新增的可执行节 .cave 中的小桩：桩内先补完原调用，再对
  「海原雄山」常量（命中处理表里已有，翻译表会把它改写成 GBK）调用一次注册。
  桩为位置无关代码（不依赖镜像基址）。

RSS 链接补丁（OnAnchorSelect 打开浏览器）：
  SSP 把头条展开成 \\_a[URL]title\\_a 锚点，点击后发 OnAnchorSelect，Ref0=URL；
  first.dll 对该 ID 匹配名字失败后落到兜底「……ん？」，SSP 便不再打开链接。
  这里把兜底分支入口重定向到 .cave 中的第二个桩：若 Ref0 以 "http" 开头，
  在节内缓冲里拼出 "\\![open,browser,<URL>]" 作为响应脚本返回；否则走原兜底。
"""
import csv, hashlib, os, sys, struct, shutil, unicodedata

SHIFTJIS_OFFSETS = {
    0xD6BB0, 0xD6BF0, 0xD6C4B, 0xD7E4E, 0xD7E8C, 0xD7ED1,
    0xD7F12, 0xD7F53, 0xD7F99, 0xD7FE3, 0xD802B, 0xD806F,
}

# （偏移, 原始字节, 替换字节）
PATCHES = [
    # 1. NOTIFY -> 按 GET 分发
    (0x719E9, bytes.fromhex('0F 85 AF 7E 00 00'), b'\x90' * 6),
    # 2. r"\![enter,inductionmode]" 字符串长度 23 -> 0
    (0x79E08, bytes.fromhex('17 00 00 00'), bytes.fromhex('00 00 00 00')),
]


def apply_patches(data: bytes) -> bytes:
    out = data
    for off, orig, repl in PATCHES:
        if out[off:off + len(orig)] != orig:
            raise RuntimeError(
                f'offset 0x{off:X} mismatch: expected {orig.hex()}, '
                f'got {out[off:off + len(orig)].hex()}'
            )
        out = out[:off] + repl + out[off + len(repl):]
    return out


# ------------------------------------------------- 链接化补丁（海原雄山）

LINKIFY_CALL_OFF = 0xA9E37          # 最后一段（木野さん）的 call 0x4AA838
LINKIFY_CALL_ORIG = bytes.fromhex('E8 FC FD FF FF')
LINKIFY_CALL_NEXT_VA = 0x4AAA3C     # 该 call 的下一条指令（pop ecx）
LINKIFY_ADD_FUNC = 0x4AA838         # 名单注册函数（EAX=常量指针）
LINKIFY_EXTRA_STR = 0x4875E0        # 「海原雄山」常量数据指针（翻译后为 GBK）


def _build_linkify_stub(rva):
    """26 字节位置无关桩：
       进入时 EAX=木野さん（原调用方已设好），先按原逻辑注册；
       再用 call/pop/add 取得「海原雄山」常量指针并注册；最后返回。
    """
    b = bytearray()
    va = lambda i: rva + i

    b += b'\x55'                                              # push ebp
    b += b'\xE8' + struct.pack('<i', LINKIFY_ADD_FUNC - va(6))   # call add
    b += b'\x59'                                              # pop ecx
    b += b'\xE8\x00\x00\x00\x00'                              # call $+5
    b += b'\x58'                                              # pop eax (= va(12))
    b += b'\x05' + struct.pack('<i', LINKIFY_EXTRA_STR - va(12))  # add eax, delta
    b += b'\x55'                                              # push ebp
    b += b'\xE8' + struct.pack('<i', LINKIFY_ADD_FUNC - va(24))  # call add
    b += b'\x59'                                              # pop ecx
    b += b'\xC3'                                              # ret
    assert len(b) == 26
    return bytes(b)


def add_cave_section(data: bytearray, code: bytes) -> int:
    """在文件末尾追加只读写+可执行的 .cave 节，返回其 RVA。"""
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt_size = _u16(data, e + 20)
    opt = e + 24
    sec_tab = opt + opt_size
    first_raw = min(_u32(data, sec_tab + 40 * i + 20) for i in range(nsec))
    if sec_tab + (nsec + 1) * 40 > first_raw:
        raise RuntimeError('PE 头空间不足，无法追加节')
    max_end = max(_u32(data, sec_tab + 40 * i + 12) + _u32(data, sec_tab + 40 * i + 8)
                  for i in range(nsec))
    new_rva = (max_end + 0xFFF) & ~0xFFF
    raw = (len(data) + 0x1FF) & ~0x1FF
    vsize = len(code)
    rawsize = (vsize + 0x1FF) & ~0x1FF
    if raw > len(data):
        data.extend(b'\x00' * (raw - len(data)))
    data.extend(code)
    data.extend(b'\x00' * (rawsize - vsize))
    hdr = sec_tab + nsec * 40
    data[hdr:hdr + 8] = b'.cave\x00\x00\x00'
    struct.pack_into('<I', data, hdr + 8, vsize)
    struct.pack_into('<I', data, hdr + 12, new_rva)
    struct.pack_into('<I', data, hdr + 16, rawsize)
    struct.pack_into('<I', data, hdr + 20, raw)
    struct.pack_into('<I', data, hdr + 24, 0)
    struct.pack_into('<I', data, hdr + 28, 0)
    struct.pack_into('<H', data, hdr + 32, 0)
    struct.pack_into('<H', data, hdr + 34, 0)
    struct.pack_into('<I', data, hdr + 36, 0xE0000020)  # CODE|EXECUTE|READ|WRITE
    struct.pack_into('<H', data, e + 6, nsec + 1)
    new_soi = new_rva + ((vsize + 0xFFF) & ~0xFFF)
    old_soi = _u32(data, opt + 56)
    struct.pack_into('<I', data, opt + 56, max(old_soi, new_soi))
    return new_rva


# OnAnchorSelect 兜底分支：文件 0x795CE 处
#   lea eax,[ebp-0x1c] / mov edx,0x48769C
# 替换为 jmp .cave 第二桩（原逻辑在桩里复刻）
URL_HOOK_OFF = 0x795CE
URL_HOOK_ORIG = bytes.fromhex('8D 45 E4 BA 9C 76 48 00')
URL_HOOK_NEXT_VA = 0x47A1D3
URL_RESP_CONT_VA = 0x47A399        # 命中/兜底后共同的继续点
LSTRASG_FUNC = 0x403C58            # Delphi 字符串赋值
FALLBACK_STR = 0x48769C            # 兜底常量「\0\s0……\w8\w8\s4ん？」（翻译表改写）

CAVE2_OFF = 0x20                   # 第二桩在 .cave 内的偏移
PREFIX_OFF = 0x100                 # "\![open,browser," 常量
BUF_DATA_OFF = 0x200               # 响应缓冲（数据指针）


def _build_url_stub(rva):
    """OnAnchorSelect 桩：Ref0=[ebp-0x1c]。http 开头 ->
    拼接 "\\![open,browser," + Ref0 + "]" 到节内缓冲并返回；否则原兜底。"""
    b = bytearray()
    va = lambda i: rva + i
    fb_target = va(112)          # .fb 标签（见下面的偏移注释）

    def rel32(target, at):
        return struct.pack('<i', target - va(at + 4))

    b += b'\x53\x56\x57'                              # 0: push ebx/esi/edi
    b += b'\xE8\x00\x00\x00\x00'                      # 3: call $+5
    b += b'\x5B'                                      # 8: pop ebx (= va(8))
    b += b'\x8B\x7D\xE4'                              # 9: mov edi,[ebp-0x1c]
    b += b'\x85\xFF'                                  # 12: test edi,edi
    b += b'\x0F\x84' + rel32(fb_target, 16)           # 14: jz .fb
    b += b'\x81\x3F\x68\x74\x74\x70'                  # 20: cmp dword [edi],'http'
    b += b'\x0F\x85' + rel32(fb_target, 28)           # 26: jne .fb
    b += b'\x8B\x4F\xFC'                              # 32: mov ecx,[edi-4]
    b += b'\x81\xF9\x00\x01\x00\x00'                  # 35: cmp ecx,0x100
    b += b'\x0F\x87' + rel32(fb_target, 43)           # 41: ja .fb
    # 注意：ebx = 本桩内 pop 的地址 = 桩VA+8 = 节VA + (CAVE2_OFF+8)，
    # 所以指向节内偏移时要减去 (CAVE2_OFF + 8)。
    b += b'\x8D\x93' + struct.pack('<i', BUF_DATA_OFF - CAVE2_OFF - 16)  # 47: lea edx,[ebx+buf-8]
    b += b'\xC7\x02\xFF\xFF\xFF\xFF'                  # 53: mov dword [edx],-1
    b += b'\x8D\x41\x11'                              # 59: lea eax,[ecx+17]
    b += b'\x89\x42\x04'                              # 62: mov [edx+4],eax
    b += b'\x8D\xB3' + struct.pack('<i', PREFIX_OFF - CAVE2_OFF - 8)  # 65: lea esi,[ebx+prefix]
    b += b'\x8D\xBA\x08\x00\x00\x00'                  # 71: lea edi,[edx+8]
    b += b'\x51'                                      # 77: push ecx
    b += b'\xB9\x10\x00\x00\x00'                      # 78: mov ecx,16
    b += b'\xF3\xA4'                                  # 83: rep movsb
    b += b'\x59'                                      # 85: pop ecx
    b += b'\x8B\x75\xE4'                              # 86: mov esi,[ebp-0x1c]
    b += b'\xF3\xA4'                                  # 89: rep movsb
    b += b'\xC6\x07\x5D'                              # 91: mov byte [edi],']'
    b += b'\xC6\x47\x01\x00'                          # 94: mov byte [edi+1],0
    b += b'\x83\xC2\x08'                              # 98: add edx,8
    b += b'\x89\x55\xE4'                              # 101: mov [ebp-0x1c],edx
    b += b'\x5F\x5E\x5B'                              # 104: pop edi/esi/ebx
    b += b'\xE9' + rel32(URL_RESP_CONT_VA, 108)       # 107: jmp cont
    assert len(b) == 112, len(b)
    # .fb（offset 112）
    b += b'\x8D\x45\xE4'                              # 112: lea eax,[ebp-0x1c]
    b += b'\x8D\x93' + struct.pack('<i', FALLBACK_STR - va(8))   # 115: lea edx,[ebx+fb]
    b += b'\xE8' + rel32(LSTRASG_FUNC, 122)           # 121: call LStrAsg
    b += b'\x5F\x5E\x5B'                              # 126: pop edi/esi/ebx
    b += b'\xE9' + rel32(URL_RESP_CONT_VA, 130)       # 129: jmp cont
    return bytes(b)


def patch_extra_link(data: bytearray) -> bytearray:
    """海原雄山 链接化 + OnAnchorSelect 打开 http 链接。"""
    blob = bytearray(0x800)
    rva = add_cave_section(data, bytes(blob))
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    sec_tab = e + 24 + _u16(data, e + 20)
    raw = _u32(data, sec_tab + (nsec - 1) * 40 + 20)      # .cave 的原始偏移

    # 统一用首选 VA（镜像基址 0x400000 + RVA）做 rel32 计算；
    # 运行时无论是否重定位，桩与目标同基址平移，相对量保持不变。
    cave_va = 0x400000 + rva

    # 桩 1：海原雄山
    data[raw:raw + 26] = _build_linkify_stub(cave_va)
    if bytes(data[LINKIFY_CALL_OFF:LINKIFY_CALL_OFF + 5]) != LINKIFY_CALL_ORIG:
        raise RuntimeError('海原雄山补丁：重定向点原始字节不匹配')
    data[LINKIFY_CALL_OFF:LINKIFY_CALL_OFF + 5] = (
        b'\xE8' + struct.pack('<i', cave_va - LINKIFY_CALL_NEXT_VA))

    # 桩 2：OnAnchorSelect http
    stub2 = _build_url_stub(cave_va + CAVE2_OFF)
    data[raw + CAVE2_OFF:raw + CAVE2_OFF + len(stub2)] = stub2
    data[raw + PREFIX_OFF:raw + PREFIX_OFF + 16] = b'\\![open,browser,'
    if bytes(data[URL_HOOK_OFF:URL_HOOK_OFF + 8]) != URL_HOOK_ORIG:
        raise RuntimeError('RSS 链接补丁：重定向点原始字节不匹配')
    data[URL_HOOK_OFF:URL_HOOK_OFF + 5] = (
        b'\xE9' + struct.pack('<i', cave_va + CAVE2_OFF - URL_HOOK_NEXT_VA))
    data[URL_HOOK_OFF + 5:URL_HOOK_OFF + 8] = b'\x90' * 3

    print(f'链接化补丁已应用: 海原雄山 + OnAnchorSelect(http) @ RVA 0x{rva:X}')
    return data


# ------------------------------------------------------------- AITXT 加密
# 算法：1) 整块反转
#       2) 与密钥流异或：keystream = MT19937(seed2) rand(0x7FFFFFFF) & 0xFF
#       seed2 = 以 9821 为种子的 MT19937 取 Random(0x7FFFFFFF) 后，
#               对其十进制字符串做 MD5，取十六进制结果中前 9 个数字字符

class _MT:
    def __init__(self, seed):
        self.mt = [0] * 624
        self.mt[0] = seed & 0x7FFFFFFF
        for i in range(1, 624):
            self.mt[i] = (self.mt[i - 1] * 0x10DCD) & 0xFFFFFFFF
        self.idx = 624

    def _twist(self):
        mt = self.mt
        for i in range(227):
            y = (mt[i] & 0x80000000) | (mt[i + 1] & 0x7FFFFFFF)
            mt[i] = mt[i + 397] ^ (y >> 1) ^ (0x9908B0DF if (y & 1) else 0)
        for i in range(227, 623):
            y = (mt[i] & 0x80000000) | (mt[i + 1] & 0x7FFFFFFF)
            mt[i] = mt[i - 227] ^ (y >> 1) ^ (0x9908B0DF if (y & 1) else 0)
        y = (mt[623] & 0x80000000) | (mt[0] & 0x7FFFFFFF)
        mt[623] = mt[396] ^ (y >> 1) ^ (0x9908B0DF if (y & 1) else 0)
        self.idx = 0

    def u32(self):
        if self.idx >= 624:
            self._twist()
        y = self.mt[self.idx]
        self.idx += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        return y & 0xFFFFFFFF

    def rand(self, rng):
        prod = self.u32() * (rng - 1)
        q, r = divmod(prod, 1 << 32)
        return q + 1 if 2 * r >= (1 << 32) else q


def _seed2():
    mt = _MT(0x265D)
    r1 = mt.rand(0x7FFFFFFF)
    digits = ''.join(c for c in hashlib.md5(
        str(r1).encode('ascii')).hexdigest() if c.isdigit())
    if not digits:
        return mt.rand(0x109A0)
    return int(digits[:9] if len(digits) >= 10 else digits)


def _keystream(n):
    mt = _MT(_seed2())
    return bytes(mt.rand(0x7FFFFFFF) & 0xFF for _ in range(n))


def aitxt_encrypt(plain):
    return bytes(b ^ s for b, s in zip(plain, _keystream(len(plain))))[::-1]


def aitxt_decrypt(res):
    return bytes(b ^ s for b, s in zip(res[::-1], _keystream(len(res))))


def gbk_bytes(text):
    """UTF-8 字符串 -> GBK 字节；GBK 装不下的字符先做 NFKC 再试。"""
    out = bytearray()
    bad = 0
    for ch in text:
        try:
            out += ch.encode('gbk')
            continue
        except UnicodeEncodeError:
            pass
        alt = unicodedata.normalize('NFKC', ch)
        if len(alt) == 1:
            try:
                out += alt.encode('gbk')
                continue
            except UnicodeEncodeError:
                pass
        out += b'?'
        bad += 1
    if bad:
        print(f'警告: {bad} 个字符无法编码为 GBK，已替换为 ?')
    return bytes(out)


# ------------------------------------------------------------- PE 定位

def _u16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def find_aitxt(data):
    """返回 (数据块文件偏移, 大小, Size 字段文件偏移)。"""
    e_lfanew = _u32(data, 0x3C)
    coff = e_lfanew + 4
    nsec = _u16(data, coff + 2)
    opt_size = _u16(data, coff + 16)
    opt = coff + 20
    res_rva = _u32(data, opt + 96 + 2 * 8)
    sec = opt + opt_size
    sections = [(_u32(data, sec + 40 * i + 12), _u32(data, sec + 40 * i + 8),
                 _u32(data, sec + 40 * i + 20), _u32(data, sec + 40 * i + 16))
                for i in range(nsec)]

    def rva_to_off(rva):
        for va, vsize, raw, rawsize in sections:
            if va <= rva < va + max(vsize, rawsize):
                return raw + (rva - va)
        raise RuntimeError(f'RVA 0x{rva:X} not mapped')

    base = rva_to_off(res_rva)

    def entries(dir_off):
        total = _u16(data, base + dir_off + 12) + _u16(data, base + dir_off + 14)
        for i in range(total):
            e = base + dir_off + 16 + 8 * i
            yield _u32(data, e), _u32(data, e + 4)

    def name_of(field):
        if field & 0x80000000:
            p = base + (field & 0x7FFFFFFF)
            n = _u16(data, p)
            return data[p + 2:p + 2 + n * 2].decode('utf-16-le', 'replace')
        return field

    for t_name, t_sub in entries(0):
        if name_of(t_name) != 'AITXT' or not (t_sub & 0x80000000):
            continue
        for i_name, i_sub in entries(t_sub & 0x7FFFFFFF):
            if name_of(i_name) != 101 or not (i_sub & 0x80000000):
                continue
            for _l_name, l_sub in entries(i_sub & 0x7FFFFFFF):
                data_rva, size = _u32(data, base + l_sub), _u32(data, base + l_sub + 4)
                return rva_to_off(data_rva), size, base + l_sub + 4
    raise RuntimeError('AITXT resource not found')


def patch_aitxt(data: bytearray) -> bytearray:
    text_path = os.path.join(BASE, 'aitxt_translated.txt')
    if not os.path.exists(text_path):
        raise RuntimeError(f'{text_path} 不存在，请先运行 build_from_csv.py')
    text = open(text_path, encoding='utf-8', newline='').read()
    raw = gbk_bytes(text)

    blob_off, slot_size, size_field = find_aitxt(bytes(data))
    blob = aitxt_encrypt(raw)
    if len(blob) > slot_size:
        raise RuntimeError(
            f'AITXT 超出资源槽位: {len(blob)} > {slot_size} 字节（需要 PE 手术）')
    if aitxt_decrypt(blob) != raw:
        raise RuntimeError('AITXT round-trip check failed')

    data[blob_off:blob_off + len(blob)] = blob
    data[blob_off + len(blob):blob_off + slot_size] = b'\x00' * (slot_size - len(blob))
    struct.pack_into('<I', data, size_field, len(blob))
    n_lines = len(raw.split(b'\n'))
    print(f'AITXT 已写入: {slot_size} -> {len(blob)} 字节 '
          f'(slot @0x{blob_off:X}, {n_lines} 行, round-trip OK)')
    return data


BASE = os.path.dirname(os.path.abspath(__file__))
DLL_IN = os.path.join(BASE, 'input', 'first.dll')
CSV_IN = os.path.join(BASE, 'translated.csv')
DLL_OUT = os.path.join(BASE, 'output', 'first.dll')

with open(DLL_IN, 'rb') as f:
    data = bytearray(f.read())

rows = []
with open(CSV_IN, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

ok = trunc = skip = 0
for row in rows:
    text = row['Text']
    off = int(row['Offset'].lstrip('0x'), 16)
    length = int(row['Length'])
    typ = row['Type']
    enc = 'shift-jis' if off in SHIFTJIS_OFFSETS else 'gbk'
    try:
        raw = text.encode(enc)
    except UnicodeEncodeError:
        print(f'跳过: off=0x{off:X} len={length} {enc} text={repr(text)}')
        skip += 1; continue

    if len(raw) > length:
        data[off : off + length] = raw[:length]
        if typ == 'code':
            data[off - 4 : off] = struct.pack('<I', length)
        trunc += 1
        print(f'截断: off=0x{off:X} len={length} {enc}={len(raw)} text={repr(text)}')
        continue

    data[off : off + len(raw)] = raw
    rest = length - len(raw)

    if typ == 'code':
        data[off - 4 : off] = struct.pack('<I', len(raw))
    # rsrc / font：不更新长度字段，直接补 \x00
    if rest:
        data[off + len(raw) : off + length] = b'\x00' * rest
    ok += 1

data = bytearray(apply_patches(bytes(data)))
print('兼容补丁已应用: NOTIFY 分发 (0x719E9) + 诱导模式字符串清零 (0x79E08)')

data = patch_extra_link(data)

data = patch_aitxt(data)

os.makedirs(os.path.dirname(DLL_OUT), exist_ok=True)
with open(DLL_OUT, 'wb') as f:
    f.write(data)

print(f'写入完成 → {DLL_OUT}')
if len(sys.argv) >= 2:
    dst = sys.argv[1]
    shutil.copy2(DLL_OUT, dst)
    print(f'已复制 → {dst}')

print(f'  写入: {ok}  截断: {trunc}  跳过: {skip}')
