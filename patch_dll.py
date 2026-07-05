#!/usr/bin/env python3
"""
Patch first.dll from CSV.
Offset = write position, Length = max bytes.
Standard entries padded with \x01; embedded raw strings padded with \x00.
"""
import csv, os, sys

BASE = os.path.dirname(__file__)
DLL_IN = os.path.join(BASE, 'input', 'first.dll')
CSV_IN = os.path.join(BASE, 'translated.csv')
DLL_OUT = sys.argv[1] if len(sys.argv) >= 2 else os.path.join(BASE, 'output', 'first.dll')

with open(DLL_IN, 'rb') as f:
    data = bytearray(f.read())

# Embedded raw string offsets (no FF FF FF FF marker, null-terminated, pad \x00)
MANUAL_OFFSETS = {0x6F57C}

rows = []
with open(CSV_IN, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        rows.append(row)

ok = trunc = skip = 0
for row in rows:
    text = row['Text']
    try:
        gbk = text.encode('gbk')
    except UnicodeEncodeError:
        skip += 1; continue

    off = int(row['Offset'].lstrip('0x'), 16)
    length = int(row['Length'])

    if len(gbk) <= length:
        data[off : off + len(gbk)] = gbk
        if off in MANUAL_OFFSETS:
            pad = b'\x00' * (length - len(gbk))
        else:
            pad = b'\x01' * (length - len(gbk))
        data[off + len(gbk) : off + length] = pad
        ok += 1
    else:
        data[off : off + length] = gbk[:length]
        trunc += 1

os.makedirs(os.path.dirname(DLL_OUT), exist_ok=True)
with open(DLL_OUT, 'wb') as f:
    f.write(data)

print(f'写入完成 → {DLL_OUT}')
print(f'  写入: {ok}  截断: {trunc}  跳过: {skip}')
