#!/usr/bin/env python3
"""Extract SJIS strings from first.dll to CSV.
Marker entries (FF FF FF FF header) → CODE section.
DFM entries (TPF0 resource strings) → from .rsrc section.
"""
import struct, csv, os

DLL_IN = os.path.join(os.path.dirname(__file__), 'input', 'first.dll')
CSV_OUT = os.path.join(os.path.dirname(__file__), 'extract.csv')

# FFFF entries to include even without Japanese characters
MANUAL_OFFSETS = [
    0x6F57C,  # URL: Geocities -> CompJapan Wikipedia
    0x7E020,  # URL: Search pt1: Google -> Bing
    0x7E05C,  # URL: Search pt2
]

def extract_marker_strings(data, code_start, code_end, force_offsets=None):
    """Extract standard strings preceded by FF FF FF FF marker.
    force_offsets: set of data offsets to include even without Japanese text."""
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
                    off += 4; continue
                text = raw.decode('shift_jis', errors='replace').rstrip('\x00')
                if text:
                    rows.append((data_off, length, '01', text))
            off += 4
        else:
            off += 1
    return rows


def extract_dfm_strings(data):
    """Extract Japanese strings from TPF0 (Delphi DFM) resources in .rsrc.

    In TPF0, string property values are stored as:
      \x06 <1B len> <SJIS_text>
    We extract the text position (after the len byte) with its original SJIS length.
    """
    # Find TPF0 headers in .rsrc (last section)
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
                            results.append((pos + 2, slen, text))
                    except:
                        pass
            pos += 1
    
    return results


with open(DLL_IN, 'rb') as f:
    data = f.read()

# Find CODE section range
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

# 1. Extract marker-based strings (CODE section)
all_rows = extract_marker_strings(data, code_start, code_end, force_offsets=set(MANUAL_OFFSETS))

# 2. Append DFM entries from .rsrc
dfm_rows = extract_dfm_strings(data)
all_rows += [(off, slen, '00', text) for off, slen, text in dfm_rows]

# 3. Append MS P Gothic font name entries (all .rsrc & CODE), keep original text
# Skip offsets already covered by DFM extraction to avoid duplicates
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
        font_rows.append((i, 15, '00', text))
    pos = i + 1
all_rows += font_rows

# Write CSV
os.makedirs(os.path.dirname(CSV_OUT), exist_ok=True)
with open(CSV_OUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['Offset', 'Length', 'Pad', 'Text'])
    for off, length, pad, text in all_rows:
        w.writerow([f'0x{off:X}', length, pad, text])

marker_count = len(all_rows) - len(dfm_rows) - len(font_rows)
print(f'导出 {len(all_rows)} 条 → {CSV_OUT}')
print(f'  标记: {marker_count}  DFM: {len(dfm_rows)}  字体: {len(font_rows)}')
