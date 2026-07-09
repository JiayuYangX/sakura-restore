#!/usr/bin/env python3
"""
替换 CSV 中 GBK 不支持的字符。

自动处理存在的 CSV 文件:
  extract.csv        → replaced.csv
  aitxt_extract.csv  → aitxt_replaced.csv
"""
import csv, os

BASE = os.path.dirname(__file__)

JOBS = [
    ('extract.csv', 'replaced.csv'),
    ('aitxt_extract.csv', 'aitxt_replaced.csv'),
]

HALF_KANA_MAP = {
    'ｦ': 'o', 'ｧ': 'a', 'ｨ': 'i', 'ｩ': 'u',
    'ｪ': 'e', 'ｫ': 'o', 'ｬ': 'y', 'ｭ': 'y',
    'ｮ': 'y', 'ｯ': 't', 'ｰ': '-', 'ｱ': 'a',
    'ｲ': 'i', 'ｳ': 'u', 'ｴ': 'e', 'ｵ': 'o',
    'ｶ': 'k', 'ｷ': 'k', 'ｸ': 'k', 'ｹ': 'k',
    'ｺ': 'k', 'ｻ': 's', 'ｼ': 's', 'ｽ': 's',
    'ｾ': 's', 'ｿ': 's', 'ﾀ': 't', 'ﾁ': 't',
    'ﾂ': 't', 'ﾃ': 't', 'ﾄ': 't', 'ﾅ': 'n',
    'ﾆ': 'n', 'ﾇ': 'n', 'ﾈ': 'n', 'ﾉ': 'n',
    'ﾊ': 'h', 'ﾋ': 'h', 'ﾌ': 'h', 'ﾍ': 'h',
    'ﾎ': 'h', 'ﾏ': 'm', 'ﾐ': 'm', 'ﾑ': 'm',
    'ﾒ': 'm', 'ﾓ': 'm', 'ﾔ': 'y', 'ﾕ': 'y',
    'ﾖ': 'y', 'ﾗ': 'r', 'ﾘ': 'r', 'ﾙ': 'r',
    'ﾚ': 'r', 'ﾛ': 'r', 'ﾜ': 'w', 'ﾝ': 'n',
    'ﾞ': '"', 'ﾟ': "'",
}

SPECIAL_MAP = {
    '〜': '～',
    '・': '·',
    '´': '\'',
    '♪': '~',
    '∀': 'A',
    '　': '  ',
    '♯': '#',
}

SQUARE_KANA_MAP = {
    '㌦': 'トン',  # ㌦ → トン
    '㌧': 'ドル',  # ㌧ → ドル
}

for CSV_IN_NAME, CSV_OUT_NAME in JOBS:
    CSV_IN = os.path.join(BASE, CSV_IN_NAME)
    CSV_OUT = os.path.join(BASE, CSV_OUT_NAME)

    if not os.path.exists(CSV_IN):
        continue

    rows = []
    with open(CSV_IN, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        for row in reader:
            rows.append(row)

    stats = {'half': 0, 'special': 0, 'square': 0, 'gbk_fail': 0}
    for row in rows:
        text = row['Text']
        cleaned = []
        for ch in text:
            if ch in HALF_KANA_MAP:
                cleaned.append(HALF_KANA_MAP[ch])
                stats['half'] += 1
            elif ch in SPECIAL_MAP:
                cleaned.append(SPECIAL_MAP[ch])
                stats['special'] += 1
            elif ch in SQUARE_KANA_MAP:
                cleaned.append(SQUARE_KANA_MAP[ch])
                stats['square'] += 1
            else:
                try:
                    ch.encode('gbk')
                    cleaned.append(ch)
                except UnicodeEncodeError:
                    stats['gbk_fail'] += 1
                    cleaned.append('?')
        row['Text'] = ''.join(cleaned)

    with open(CSV_OUT, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f'{CSV_IN_NAME} → {CSV_OUT_NAME}')
    print(f'  半角假名: {stats["half"]} 字符')
    print(f'  特殊符号: {stats["special"]} 字符')
    print(f'  方块假名: {stats["square"]} 字符')
    print(f'  GBK 失败: {stats["gbk_fail"]} 字符')
    print()
