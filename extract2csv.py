#!/usr/bin/env python3
import struct, csv, os

DLL_IN = os.path.join(os.path.dirname(__file__), 'input', 'first.dll')
CSV_OUT = os.path.join(os.path.dirname(__file__), 'extract.csv')

with open(DLL_IN, 'rb') as f:
    data = f.read()

e_lfanew = struct.unpack_from('<I', data, 0x3C)[0]
num_sec = struct.unpack_from('<H', data, e_lfanew + 6)[0]
sec_off = e_lfanew + 0xF8
code_off = code_size = None
for i in range(num_sec):
    name = bytes(data[sec_off + i*40 : sec_off + i*40 + 8]).rstrip(b'\x00').decode('ascii')
    if name == 'CODE':
        code_off = struct.unpack_from('<I', data, sec_off + i*40 + 20)[0]
        code_size = struct.unpack_from('<I', data, sec_off + i*40 + 16)[0]
        break
assert code_off is not None, 'CODE section not found'

rows = []
off = code_off
end = code_off + code_size
while off < end - 12:
    if data[off:off+4] == b'\xff\xff\xff\xff':
        length = struct.unpack_from('<I', data, off + 4)[0]
        if 4 <= length <= 800 and off + 8 + length <= end:
            raw = bytes(data[off+8 : off+8+length])
            has_jp = any(
                (0x81 <= raw[j] <= 0x9F or 0xE0 <= raw[j] <= 0xEF)
                and (0x40 <= raw[j+1] <= 0x7E or 0x80 <= raw[j+1] <= 0xFC)
                for j in range(len(raw) - 1)
            )
            if not has_jp:
                off += 4; continue
            text = raw.decode('shift_jis', errors='replace').rstrip('\x00')
            if text:
                rows.append((off, length, text))
        off += 4
    else:
        off += 1

os.makedirs(os.path.dirname(CSV_OUT), exist_ok=True)
with open(CSV_OUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f)
    w.writerow(['ID', 'Offset', 'Length', 'Text'])
    for i, (off, length, text) in enumerate(rows, 1):
        w.writerow([i, f'0x{off:X}', length, text])

print(f'导出 {len(rows)} 条 → {CSV_OUT}')
