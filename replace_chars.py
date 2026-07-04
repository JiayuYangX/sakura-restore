#!/usr/bin/env python3
import csv, os

BASE = os.path.dirname(__file__)
CSV_IN = os.path.join(BASE, 'extract.csv')
CSV_OUT = os.path.join(BASE, 'replaced.csv')

HALF_KANA_MAP = {
    '\uFF66': 'o', '\uFF67': 'a', '\uFF68': 'i', '\uFF69': 'u',
    '\uFF6A': 'e', '\uFF6B': 'o', '\uFF6C': 'y', '\uFF6D': 'y',
    '\uFF6E': 'y', '\uFF6F': 't', '\uFF70': '-', '\uFF71': 'a',
    '\uFF72': 'i', '\uFF73': 'u', '\uFF74': 'e', '\uFF75': 'o',
    '\uFF76': 'k', '\uFF77': 'k', '\uFF78': 'k', '\uFF79': 'k',
    '\uFF7A': 'k', '\uFF7B': 's', '\uFF7C': 's', '\uFF7D': 's',
    '\uFF7E': 's', '\uFF7F': 's', '\uFF80': 't', '\uFF81': 't',
    '\uFF82': 't', '\uFF83': 't', '\uFF84': 't', '\uFF85': 'n',
    '\uFF86': 'n', '\uFF87': 'n', '\uFF88': 'n', '\uFF89': 'n',
    '\uFF8A': 'h', '\uFF8B': 'h', '\uFF8C': 'h', '\uFF8D': 'h',
    '\uFF8E': 'h', '\uFF8F': 'm', '\uFF90': 'm', '\uFF91': 'm',
    '\uFF92': 'm', '\uFF93': 'm', '\uFF94': 'y', '\uFF95': 'y',
    '\uFF96': 'y', '\uFF97': 'r', '\uFF98': 'r', '\uFF99': 'r',
    '\uFF9A': 'r', '\uFF9B': 'r', '\uFF9C': 'w', '\uFF9D': 'n',
    '\uFF9E': '"', '\uFF9F': "'",
}

SPECIAL_MAP = {
    '\u301C': '\uFF5E',  # 〜 → ～
    '\u30FB': '\u00B7',  # ・ → ·
    '\u00B4': '\u0027',  # ´ → '
    '\u266A': '\u007E',  # ♪ → ~
    '\u2200': '\u0041',  # ∀ → A
}

rows = []
with open(CSV_IN, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    for row in reader:
        rows.append(row)

stats = {'half': 0, 'special': 0}
for row in rows:
    orig = row['Text']
    cleaned = []
    for ch in orig:
        try:
            ch.encode('gbk')
            cleaned.append(ch)
        except UnicodeEncodeError:
            if ch in HALF_KANA_MAP:
                cleaned.append(HALF_KANA_MAP[ch])
                stats['half'] += 1
            elif ch in SPECIAL_MAP:
                cleaned.append(SPECIAL_MAP[ch])
                stats['special'] += 1
            else:
                cleaned.append('?')
    row['Text'] = ''.join(cleaned)

with open(CSV_OUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)

print(f'替换完成 → {CSV_OUT}')
print(f'  半角假名: {stats["half"]} 字符')
print(f'  特殊符号: {stats["special"]} 字符')
