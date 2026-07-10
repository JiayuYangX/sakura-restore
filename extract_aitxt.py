"""
从 SSP 进程内存中提取 AITXT 条目到 CSV。

    用法:
    python extract_aitxt.py [--region2]

    不带参数: 仅提取第一区域（日文对话 + 单词 + 控制符）
    --region2:  同时提取第二区域
"""

import ctypes, struct, os, csv, sys

EXTRACT_REGION2 = '--region2' in sys.argv


def find_aitxt():
    """查找 SSP 进程，搜索 \\ms,\\female 特征签名，返回(主区域基址, 第二区域基址, PID)。"""
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    class PE32(ctypes.Structure):
        _fields_ = [('dwSize', ctypes.c_uint), ('cntUsage', ctypes.c_uint),
                    ('th32ProcessID', ctypes.c_uint), ('th32DefaultHeapID', ctypes.c_void_p),
                    ('th32ModuleID', ctypes.c_uint), ('cntThreads', ctypes.c_uint),
                    ('th32ParentProcessID', ctypes.c_uint), ('pcPriClassBase', ctypes.c_long),
                    ('dwFlags', ctypes.c_uint), ('szExeFile', ctypes.c_char * 260)]

    pid = None
    s = k.CreateToolhelp32Snapshot(2, 0)
    if s > 0:
        p = PE32(); p.dwSize = ctypes.sizeof(PE32)
        if k.Process32First(s, ctypes.byref(p)):
            while True:
                if p.szExeFile.lower() in (b'ssp.exe', b'materia.exe'):
                    pid = p.th32ProcessID; break
                if not k.Process32Next(s, ctypes.byref(p)): break
        k.CloseHandle(s)
    if not pid: raise RuntimeError('SSP 未运行')

    hdr = bytes([0x1A, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x0B, 0x00, 0x00, 0x00])
    class MBI(ctypes.Structure):
        _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                    ('AllocationProtect', ctypes.c_uint), ('RegionSize', ctypes.c_size_t),
                    ('State', ctypes.c_uint), ('Protect', ctypes.c_uint), ('Type', ctypes.c_uint)]

    h = k.OpenProcess(0x10 | 0x400, False, pid)
    if not h: raise RuntimeError(f'无法打开 PID {pid}')

    alloc_hits = {}
    addr = 0x01000000
    while addr < 0x7FFFFFFF:
        m = MBI()
        if not k.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(m), ctypes.sizeof(MBI)): break
        if m.State == 0x1000 and m.RegionSize >= len(hdr):
            chunk = min(m.RegionSize, 0x10000)
            buf = ctypes.create_string_buffer(chunk)
            br = ctypes.c_size_t(0)
            if k.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, chunk, ctypes.byref(br)) and br.value >= len(hdr):
                d = buf.raw[:br.value]; pos = 0
                while True:
                    idx = d.find(hdr, pos)
                    if idx == -1: break
                    alloc_hits.setdefault(m.AllocationBase, []).append(addr + idx)
                    pos = idx + 1
        addr += m.RegionSize
    k.CloseHandle(h)
    if not alloc_hits: raise RuntimeError('内存中未找到 AITXT')
    sorted_ab = sorted(alloc_hits.items(), key=lambda x: -len(x[1]))
    return sorted_ab[0][0], sorted_ab[1][0] if len(sorted_ab) > 1 else None, pid


def try_parse(data, off):
    if off + 12 > len(data): return None
    o = struct.unpack('<I', data[off:off+4])[0]
    i = struct.unpack('<I', data[off+4:off+8])[0]
    kl = struct.unpack('<I', data[off+8:off+12])[0]
    if not (1 <= o <= 500 and 1 <= i <= 10 and 1 <= kl <= 5000): return None
    if off + 12 + kl > len(data): return None
    return (o, i, kl)



# ---- 主流程 ----
k = ctypes.WinDLL('kernel32', use_last_error=True)
VirtualQueryEx = k.VirtualQueryEx
ReadProcessMemory = k.ReadProcessMemory

main_ab, second_ab, pid = find_aitxt()
s_ab = f'0x{second_ab:08X}' if second_ab else 'N/A'
print(f'PID={pid}  主区域=0x{main_ab:08X}  第二区域={s_ab}')

h = k.OpenProcess(0x10 | 0x400, False, pid)
if not h: raise RuntimeError('无法打开进程')

all_rows = []


# ---- 第一区域（分段读取 + VA 映射） ----
class MBI(ctypes.Structure):
    _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                ('AllocationProtect', ctypes.c_uint), ('RegionSize', ctypes.c_size_t),
                ('State', ctypes.c_uint), ('Protect', ctypes.c_uint), ('Type', ctypes.c_uint)]

# 分段读取主分配的已提交页
chunks_r1 = []
addr = main_ab
while addr < main_ab + 0x100000:
    m = MBI()
    if not VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(m), ctypes.sizeof(MBI)): break
    if m.AllocationBase != main_ab: break
    if m.State == 0x1000 and addr >= main_ab + 0x10000:  # 从条目区开始
        buf = ctypes.create_string_buffer(m.RegionSize)
        br = ctypes.c_size_t(0)
        if ReadProcessMemory(h, ctypes.c_void_p(addr), buf, m.RegionSize, ctypes.byref(br)) and br.value > 0:
            chunks_r1.append((addr, buf.raw[:br.value]))
    addr += m.RegionSize

r1_base = chunks_r1[0][0] if chunks_r1 else 0  # Region 1 条目基址

# 展平 + VA 映射
flat_r1 = bytearray()
for _, c in chunks_r1: flat_r1.extend(c)
flat_r1 = bytes(flat_r1)

def flat_to_va_r1(fo):
    acc = 0
    for base, data in chunks_r1:
        if fo < acc + len(data): return base + (fo - acc)
        acc += len(data)
    return 0

print(f'第一区域: {len(flat_r1)} 字节, {len(chunks_r1)} 段')

# 解析条目
entries1 = []
off = 0
while off < len(flat_r1) - 12:
    e = try_parse(flat_r1, off)
    if e is None: off += 1; continue
    o, i, kl = e
    raw = flat_r1[off+12:off+12+kl]
    try: txt = raw.decode('cp932', errors='replace').rstrip('\x00')
    except: txt = ''
    entries1.append((off, o, i, kl, txt, raw))
    found = False
    for pad in range(0, 9):
        no = off + 12 + kl + pad
        if try_parse(flat_r1, no) is not None: off = no; found = True; break
    if not found: off += 1

# 分类
pending_ctrl = False
for off, outer, inner, kl, txt, raw in entries1:
    txt_clean = txt.rstrip('\x00').rstrip('\x01')
    va = flat_to_va_r1(off)
    offset = va - r1_base
    if inner == 2:
        pending_ctrl = txt_clean.startswith('\\')
        if not pending_ctrl:
            all_rows.append((1, f'0x{offset:X}', outer, inner, kl, txt_clean))
    elif inner == 1 and pending_ctrl:
        pending_ctrl = False
        all_rows.append((1, f'0x{offset:X}', outer, inner, kl, txt_clean))
    elif inner == 1:
        pending_ctrl = False

r1 = sum(1 for r in all_rows if r[0] == 1)
print(f'第一区域: 提取 {r1} 条')

# ---- 第二区域（可选） ----
if second_ab and EXTRACT_REGION2:
    chunks_r2 = []
    addr = second_ab
    while addr < second_ab + 0x100000:
        m = MBI()
        if not VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(m), ctypes.sizeof(MBI)): break
        if m.AllocationBase != second_ab: break
        if m.State == 0x1000:
            buf = ctypes.create_string_buffer(m.RegionSize)
            br = ctypes.c_size_t(0)
            if ReadProcessMemory(h, ctypes.c_void_p(addr), buf, m.RegionSize, ctypes.byref(br)) and br.value > 0:
                chunks_r2.append((addr, buf.raw[:br.value]))
        addr += m.RegionSize

    flat_r2 = bytearray()
    for _, c in chunks_r2: flat_r2.extend(c)
    flat_r2 = bytes(flat_r2)

    def va_to_flat(va, chunks):
        acc = 0
        for base, data in chunks:
            if base <= va < base + len(data):
                return acc + (va - base)
            acc += len(data)
        return None

    def flat_to_va_r2(fo):
        acc = 0
        for base, data in chunks_r2:
            if fo < acc + len(data): return base + (fo - acc)
            acc += len(data)
        return 0

    print(f'第二区域: {len(flat_r2)} 字节, {len(chunks_r2)} 段')
    r2_base = chunks_r2[0][0] + 0x308 if chunks_r2 else 0  # Region 2 条目基址

    # 线性扫描条目区（指针表之后）
    entries2 = []
    off = 0
    if chunks_r2:
        first_va = chunks_r2[0][0] + 0x308
        acc = 0
        for base, data in chunks_r2:
            if base <= first_va < base + len(data):
                off = acc + (first_va - base); break
            acc += len(data)

    while off < len(flat_r2) - 12:
        e = try_parse(flat_r2, off)
        if e is None: off += 1; continue
        o, i, kl = e
        raw = flat_r2[off+12:off+12+kl]
        try: txt = raw.decode('cp932', errors='replace').rstrip('\x00')
        except: txt = ''
        entries2.append((flat_to_va_r2(off), o, i, kl, txt, raw))
        found = False
        for pad in range(0, 9):
            no = off + 12 + kl + pad
            if try_parse(flat_r2, no) is not None: off = no; found = True; break
        if not found: off += 1

    print(f'第二区域: {len(entries2)} 原始条目')
    skipped = 0
    for va, outer, inner, kl, txt, raw in entries2:
        txt_clean = txt.rstrip('\x00\x01')
        # 1. contains binary garbage (null bytes or replacement chars)
        if '\x00' in txt_clean or '\ufffd' in txt_clean:
            skipped += 1; continue
        # 2. halfwidth katakana → rejected (GBK data decoded as cp932)
        if any('\uff65' <= c <= '\uff9f' for c in txt_clean):
            skipped += 1; continue
        # 3. no kana or kanji at all → rejected (process artifact)
        has_jp = any('\u3040' <= c <= '\u30ff' or '\u4e00' <= c <= '\u9fff' for c in txt_clean)
        if not has_jp:
            skipped += 1; continue
        offset = va - r2_base
        all_rows.append((2, f'0x{offset:X}', outer, inner, kl, txt_clean))
    print(f'第二区域: 提取 {len(entries2) - skipped} 条, 过滤 {skipped} 条')

k.CloseHandle(h)

# ---- 写 CSV ----
outpath = os.path.join(os.path.dirname(__file__) or '.', 'aitxt_extract.csv')
with open(outpath, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['Region', 'Offset', 'Outer', 'Inner', 'KeyLen', 'Text'])
    for row in all_rows:
        w.writerow(row)

r2 = sum(1 for r in all_rows if r[0] == 2)
print(f'\n合计: {len(all_rows)} 条 (R1={r1}, R2={r2})')
print(f'Region 1 基址: 0x{r1_base:08X}')
if EXTRACT_REGION2 and second_ab:
    print(f'Region 2 基址: 0x{r2_base:08X}')
print(f'保存至: {outpath}')
