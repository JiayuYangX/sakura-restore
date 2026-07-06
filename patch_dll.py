#!/usr/bin/env python3
"""
Patch first.dll from CSV.
Offset = write position, Length = max bytes, Pad = padding byte (hex).
Writes GBK text at offset (Shift-JIS for special IDs), pads rest with Pad byte if shorter.
Also auto-pads \\q[] label text with \\x01 when the label matches another entry's
full text, ensuring tooltip entries match their corresponding labels byte-for-byte.
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
    fieldnames = reader.fieldnames
    has_pad = 'Pad' in fieldnames
    for row in reader:
        rows.append(row)

# Index: full text -> list of (offset, length) for entries that are pure text (no \q[])
text_index = {}
for row in rows:
    txt = row['Text']
    if '\\q[' in txt:
        continue
    off = int(row['Offset'].lstrip('0x'), 16)
    length = int(row['Length'])
    text_index.setdefault(txt, []).append((off, length))

# Scan entries with \q[] and pad labels that match a pure-text entry
total_matched = 0
for row in rows:
    text = row['Text']
    if '\\q[' not in text:
        continue

    off = int(row['Offset'].lstrip('0x'), 16)
    if off in SHIFTJIS_OFFSETS:
        continue

    # Find all \q[label,  (position of label start, position of comma)
    q_positions = []
    idx = 0
    while True:
        idx = text.find('\\q[', idx)
        if idx < 0:
            break
        cm = text.find(',', idx + 3)
        if cm >= 0:
            q_positions.append((idx + 3, cm))
            idx = cm + 1
        else:
            idx += 1

    if not q_positions:
        continue

    # Process in reverse so insertions don't shift earlier positions
    result = text
    for start, cm in reversed(q_positions):
        label = result[start:cm]
        if label not in text_index:
            continue

        # Found a match: this \q label is a tooltip entry's full text
        total_matched += 1

        # The tooltip entry has Length = original SJIS byte count
        # Use the shortest Length when multiple entries match (tooltip entry is the base)
        tip_len = min(l for _, l in text_index[label])
        label_gbk_len = len(label.encode('gbk'))
        pad = tip_len - label_gbk_len
        if pad > 0:
            result = result[:cm] + '\x01' * pad + result[cm:]

    row['Text'] = result

print(f'有提示的选项文本数: {total_matched}')

ok = trunc = skip = 0
for row in rows:
    text = row['Text']
    off = int(row['Offset'].lstrip('0x'), 16)
    length = int(row['Length'])
    enc = 'shift-jis' if off in SHIFTJIS_OFFSETS else 'gbk'
    try:
        raw = text.encode(enc)
    except UnicodeEncodeError:
        print(f'跳过: off=0x{off:X} len={length} {enc} text={repr(text)}')
        skip += 1; continue

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
        print(f'截断: off=0x{off:X} len={length} {enc}={len(raw)} text={repr(text)}')

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
