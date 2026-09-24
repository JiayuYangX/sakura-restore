#!/usr/bin/env python3
"""从 first.dll 提取 Shift_JIS 字符串到 CSV。
标记条目（FF FF FF FF 头）→ CODE 段。
DFM 条目（TPF0 资源字符串）→ .rsrc 段。
问答答案常量（反转的 hex-SJIS）→ answer 段。
"""
import struct, csv, os

DLL_IN = os.path.join(os.path.dirname(__file__), 'input', 'first.dll')
CSV_OUT = os.path.join(os.path.dirname(__file__), 'extract.csv')

# 即使没有日文字符也要收录的 FFFF 条目
MANUAL_OFFSETS = [
    0x6F57C,  # URL：Geocities -> CompJapan Wikipedia
    0x7E020,  # URL：搜索第 1 处：Google -> Bing
    0x7E05C,  # URL：搜索第 2 处
]

def decode_answer(raw):
    """问答游戏的答案常量：payload 为大写十六进制 ASCII，
    整串反转后 hex→字节 即为 SJIS 答案文本（见 patch_dll.py 的 answer 回写）。
    不符合该格式则返回 None。"""
    if len(raw) < 8 or len(raw) % 2:
        return None
    try:
        s = raw.decode('ascii')
    except UnicodeDecodeError:
        return None
    if any(c not in '0123456789ABCDEF' for c in s):
        return None
    try:
        text = bytes.fromhex(s[::-1]).decode('shift_jis')
    except (ValueError, UnicodeDecodeError):
        return None
    if not text or any(ord(c) < 0x20 for c in text):
        return None
    return text

def extract_marker_strings(data, code_start, code_end, force_offsets=None):
    """提取以 FF FF FF FF 为前缀的标准字符串。
    force_offsets：即使没有日文也强制收录的数据偏移集合。"""
    rows = []
    force = force_offsets or set()
    off = code_start
    while off < code_end - 12:
        if data[off:off+4] == b'\xff\xff\xff\xff':
            length = struct.unpack_from('<I', data, off + 4)[0]
            if 2 <= length <= 800 and off + 8 + length <= code_end:
                raw = bytes(data[off+8 : off+8+length])
                data_off = off + 8
                has_jp = any(
                    (0x81 <= raw[j] <= 0x9F or 0xE0 <= raw[j] <= 0xEF)
                    and (0x40 <= raw[j+1] <= 0x7E or 0x80 <= raw[j+1] <= 0xFC)
                    for j in range(len(raw) - 1)
                )
                if not has_jp and data_off not in force:
                    ans = decode_answer(raw)
                    if ans is not None:
                        rows.append((data_off, length, 'answer', ans))
                    off += 4; continue
                text = raw.decode('shift_jis', errors='replace').rstrip('\x00')
                if text:
                    rows.append((data_off, length, 'code', text))
            off += 4
        else:
            off += 1
    return rows


def extract_dfm_strings(data):
    """从 .rsrc 里的 TPF0（Delphi DFM）资源中提取日文字符串。

    TPF0 中字符串属性值的存放格式为：
      \x06 <1 字节长度> <SJIS 文本>
    我们取文本位置（长度字节之后）及其原始 SJIS 长度。
    """
    # 在 .rsrc（最后一个节）里找 TPF0 头
    rsrc_start = 0xBB000
    rsrc_end = 0xD9800
    
    tpf0s = []
    off = rsrc_start
    while off < rsrc_end - 4:
        if data[off:off+4] == b'TPF0':
            tpf0s.append(off)
        off += 1
    
    results = []
    for i, tpf0_off in enumerate(tpf0s):
        tpf0_end = rsrc_end
        if i + 1 < len(tpf0s):
            tpf0_end = tpf0s[i + 1]
        
        pos = tpf0_off + 4
        while pos < tpf0_end - 3:
            if data[pos] == 0x06:
                slen = data[pos + 1]
                if 2 <= slen <= 80 and pos + 2 + slen <= tpf0_end:
                    raw = data[pos + 2 : pos + 2 + slen]
                    try:
                        text = raw.decode('shift_jis')
                        has_jp = any(0x80 < ord(c) < 0x10000 for c in text)
                        is_clean = all(
                            ord(c) >= 0x20 or ord(c) in (0x0A, 0x0D, 0x09)
                            for c in text
                        )
                        if has_jp and is_clean:
                            results.append((pos + 2, slen, 'rsrc', text))
                    except:
                        pass
            pos += 1
    
    return results


with open(DLL_IN, 'rb') as f:
    data = f.read()

# 找到 CODE 段范围
e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
num_sec = struct.unpack_from('<H', data, e_lfanew + 6)[0]
sec_off = e_lfanew + 0xF8
code_start = code_end = None
for i in range(num_sec):
    name = bytes(data[sec_off + i*40 : sec_off + i*40 + 8]).rstrip(b'\x00').decode('ascii')
    if name == 'CODE':
        code_start = struct.unpack_from('<I', data, sec_off + i*40 + 20)[0]
        code_end = code_start + struct.unpack_from('<I', data, sec_off + i*40 + 16)[0]
        break
assert code_start is not None, 'CODE section not found'

# 1. 提取基于标记的字符串（CODE 段）
all_rows = extract_marker_strings(data, code_start, code_end, force_offsets=set(MANUAL_OFFSETS))

# 2. 追加 .rsrc 中的 DFM 条目
dfm_rows = extract_dfm_strings(data)
all_rows += [(off, slen, typ, text) for off, slen, typ, text in dfm_rows]

# 3. 追加 MS P Gothic 字体名条目（.rsrc 与 CODE 中全部）
# 跳过已被 DFM 提取覆盖的偏移，避免重复
existing_offsets = {off for off, _, _, _ in all_rows}
font_pat = b'\x82\x6c\x82\x72\x20\x82\x6f\x83\x53\x83\x56\x83\x62\x83\x4e'
font_rows = []
pos = 0
while True:
    i = data.find(font_pat, pos)
    if i == -1:
        break
    if i not in existing_offsets:
        raw = data[i:i+15]
        text = raw.decode('shift_jis', errors='replace')
        font_rows.append((i, 15, 'font', text))
    pos = i + 1
all_rows += font_rows

# 写出 CSV
os.makedirs(os.path.dirname(CSV_OUT), exist_ok=True)
with open(CSV_OUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['Offset', 'Length', 'Type', 'Text'])
    for off, length, typ, text in all_rows:
        w.writerow([f'0x{off:X}', length, typ, text])

answer_count = sum(1 for r in all_rows if r[2] == 'answer')
marker_count = len(all_rows) - len(dfm_rows) - len(font_rows) - answer_count
print(f'导出 {len(all_rows)} 条 → {CSV_OUT}')
print(f'  标记: {marker_count}  答案: {answer_count}  DFM: {len(dfm_rows)}  字体: {len(font_rows)}')
