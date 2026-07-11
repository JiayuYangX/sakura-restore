#!/usr/bin/env python3
"""
Patch first.dll from CSV.
Offset = write position, Length = max bytes, Type = code|rsrc|font.
- code: 4-byte LE length at off-4, update + zero rest
- rsrc: 1-byte length at off-1, update + zero rest
- font: no length prefix, pad with \\x00
"""
import csv, os, sys

SHIFTJIS_OFFSETS = {
    0xD6BB0, 0xD6BF0, 0xD6C4B, 0xD7E4E, 0xD7E8C, 0xD7ED1,
    0xD7F12, 0xD7F53, 0xD7F99, 0xD7FE3, 0xD802B, 0xD806F,
}

BASE = os.path.dirname(__file__)
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

import struct
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
    # rsrc / font: no length field update, just pad with \x00
    if rest:
        data[off + len(raw) : off + length] = b'\x00' * rest
    ok += 1

os.makedirs(os.path.dirname(DLL_OUT), exist_ok=True)
with open(DLL_OUT, 'wb') as f:
    f.write(data)

print(f'写入完成 → {DLL_OUT}')
if len(sys.argv) >= 2:
    dst = sys.argv[1]
    import shutil
    shutil.copy2(DLL_OUT, dst)
    print(f'已复制 → {dst}')

print(f'  写入: {ok}  截断: {trunc}  跳过: {skip}')
