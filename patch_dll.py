#!/usr/bin/env python3
"""
Patch first.dll from CSV.
Offset = write position, Length = max bytes, Pad = padding byte (hex).
Writes GBK text at offset (Shift-JIS for special IDs), pads rest with Pad byte if shorter.
"""
import csv, os, sys

SHIFTJIS_IDS = {2357, 2358, 2359}

BASE = os.path.dirname(__file__)
DLL_IN = os.path.join(BASE, 'input', 'first.dll')
CSV_IN = os.path.join(BASE, 'translated.csv')
DLL_OUT = sys.argv[1] if len(sys.argv) >= 2 else os.path.join(BASE, 'output', 'first.dll')

with open(DLL_IN, 'rb') as f:
    data = bytearray(f.read())

rows = []
with open(CSV_IN, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    has_pad = 'Pad' in fieldnames
    for row in reader:
        rows.append(row)

ok = trunc = skip = 0
for row in rows:
    text = row['Text']
    enc = 'shift-jis' if int(row['ID']) in SHIFTJIS_IDS else 'gbk'
    try:
        raw = text.encode(enc)
    except UnicodeEncodeError:
        skip += 1; continue

    off = int(row['Offset'].lstrip('0x'), 16)
    length = int(row['Length'])

    if has_pad and row['Pad']:
        pad_byte = bytes.fromhex(row['Pad'])
    else:
        pad_byte = b'\x01'

    if len(raw) <= length:
        data[off : off + len(raw)] = raw
        if len(raw) < length:
            data[off + len(raw) : off + length] = pad_byte * (length - len(raw))
        ok += 1
    else:
        data[off : off + length] = raw[:length]
        trunc += 1
        print(f'截断: ID={row["ID"]} off=0x{off:X} len={length} {enc}={len(raw)} text={repr(text)}')

os.makedirs(os.path.dirname(DLL_OUT), exist_ok=True)
with open(DLL_OUT, 'wb') as f:
    f.write(data)

print(f'写入完成 → {DLL_OUT}')
print(f'  写入: {ok}  截断: {trunc}  跳过: {skip}')
