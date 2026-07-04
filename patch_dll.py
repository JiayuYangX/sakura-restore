#!/usr/bin/env python3
import csv, struct, os, sys

BASE = os.path.dirname(__file__)
DLL_IN = os.path.join(BASE, 'first.dll')
CSV_IN = os.path.join(BASE, 'translated.csv')
DLL_OUT = sys.argv[1] if len(sys.argv) >= 2 else os.path.join(BASE, 'output', 'first.dll')
PAD_BYTE = b'\x01'

rows = []
with open(CSV_IN, 'r', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        rows.append(row)

with open(DLL_IN, 'rb') as f:
    data = bytearray(f.read())

ok = trunc = skip = 0
for row in rows:
    off = int(row['Offset'].strip().lstrip('0x'), 16)
    length = int(row['Length'])
    text = row['Text']

    try:
        gbk = text.encode('gbk')
    except UnicodeEncodeError:
        skip += 1; continue

    dst = off + 8
    if len(gbk) <= length:
        data[dst : dst + len(gbk)] = gbk
        if len(gbk) < length:
            data[dst + len(gbk) : dst + length] = PAD_BYTE * (length - len(gbk))
        ok += 1
    else:
        data[dst : dst + length] = gbk[:length]
        trunc += 1

os.makedirs(os.path.dirname(DLL_OUT), exist_ok=True)
with open(DLL_OUT, 'wb') as f:
    f.write(data)

print(f'写入完成 → {DLL_OUT}')
print(f'  写入: {ok}  截断: {trunc}  跳过: {skip}')
print(f'  填充字节: 0x{PAD_BYTE.hex()}')
