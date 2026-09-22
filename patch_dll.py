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
