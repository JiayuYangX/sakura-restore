# -*- coding: utf-8 -*-
"""
从 input/first.dll 提取并解密 AITXT 资源，导出为脚本目录下的 aitxt_extract.txt。

- 直接解析 PE 资源目录定位 AITXT（无硬编码偏移）
- 解密算法：整块反转 + MT19937 密钥流异或（自带 round-trip 校验）
- 输出：UTF-8 文本（CRLF 保持原样）

直接运行即可，无参数：
    python extract_aitxt.py
"""
import hashlib
import struct
import io
import os
import sys


# ---------------------------------------------------------------- PE parsing

def _u16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def parse_pe(data):
    if data[:2] != b'MZ':
        raise RuntimeError('not an MZ file')
    e_lfanew = _u32(data, 0x3C)
    if data[e_lfanew:e_lfanew + 4] != b'PE\x00\x00':
        raise RuntimeError('not a PE file')
    coff = e_lfanew + 4
    nsec = _u16(data, coff + 2)
    opt_size = _u16(data, coff + 16)
    opt = coff + 20
    if _u16(data, opt) != 0x10B:
        raise RuntimeError('not PE32')
    res_rva = _u32(data, opt + 96 + 2 * 8)
    if not res_rva:
        raise RuntimeError('no resource directory')
    sec = opt + opt_size
    sections = []
    for i in range(nsec):
        o = sec + 40 * i
        sections.append((_u32(data, o + 12), _u32(data, o + 8),
                         _u32(data, o + 20), _u32(data, o + 16)))
    return res_rva, sections


def rva_to_off(rva, sections):
    for va, vsize, raw, rawsize in sections:
        if va <= rva < va + max(vsize, rawsize):
            return raw + (rva - va)
    raise RuntimeError(f'RVA 0x{rva:X} not mapped')


def _iter_entries(data, base, dir_off):
    total = _u16(data, base + dir_off + 12) + _u16(data, base + dir_off + 14)
    for i in range(total):
        e = base + dir_off + 16 + 8 * i
        yield _u32(data, e), _u32(data, e + 4)


def _entry_name(data, base, field):
    if field & 0x80000000:
        p = base + (field & 0x7FFFFFFF)
        n = _u16(data, p)
        return data[p + 2:p + 2 + n * 2].decode('utf-16-le', 'replace')
    return field


def find_aitxt(data):
    res_rva, sections = parse_pe(data)
    base = rva_to_off(res_rva, sections)
    for t_name, t_sub in _iter_entries(data, base, 0):
        if _entry_name(data, base, t_name) != 'AITXT' or not (t_sub & 0x80000000):
            continue
        for i_name, i_sub in _iter_entries(data, base, t_sub & 0x7FFFFFFF):
            if _entry_name(data, base, i_name) != 101 or not (i_sub & 0x80000000):
                continue
            for _l_name, l_sub in _iter_entries(data, base, i_sub & 0x7FFFFFFF):
                e = base + l_sub
                data_rva, size = _u32(data, e), _u32(data, e + 4)
                return rva_to_off(data_rva, sections), size
    raise RuntimeError('AITXT resource not found')


# ------------------------------------------------------------------- cipher

class MT:
    def __init__(self, seed):
        self.mt = [0] * 624
        self.mt[0] = seed & 0x7FFFFFFF
        for i in range(1, 624):
            self.mt[i] = (self.mt[i - 1] * 0x10DCD) & 0xFFFFFFFF
        self.idx = 624

    def _twist(self):
        mt = self.mt
        for i in range(227):
            y = (mt[i] & 0x80000000) | (mt[i + 1] & 0x7FFFFFFF)
            mt[i] = mt[i + 397] ^ (y >> 1) ^ (0x9908B0DF if (y & 1) else 0)
        for i in range(227, 623):
            y = (mt[i] & 0x80000000) | (mt[i + 1] & 0x7FFFFFFF)
            mt[i] = mt[i - 227] ^ (y >> 1) ^ (0x9908B0DF if (y & 1) else 0)
        y = (mt[623] & 0x80000000) | (mt[0] & 0x7FFFFFFF)
        mt[623] = mt[396] ^ (y >> 1) ^ (0x9908B0DF if (y & 1) else 0)
        self.idx = 0

    def u32(self):
        if self.idx >= 624:
            self._twist()
        y = self.mt[self.idx]
        self.idx += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        return y & 0xFFFFFFFF

    def rand(self, rng):
        prod = self.u32() * (rng - 1)
        q, r = divmod(prod, 1 << 32)
        return q + 1 if 2 * r >= (1 << 32) else q


def seed2_value():
    mt = MT(0x265D)
    r1 = mt.rand(0x7FFFFFFF)
    h = hashlib.md5(str(r1).encode('ascii')).hexdigest()
    digits = ''.join(c for c in h if c.isdigit())
    if not digits:
        return mt.rand(0x109A0)
    return int(digits[:9] if len(digits) >= 10 else digits)


def keystream(n):
    mt = MT(seed2_value())
    return bytes(mt.rand(0x7FFFFFFF) & 0xFF for _ in range(n))


def decrypt(res):
    return bytes(b ^ s for b, s in zip(res[::-1], keystream(len(res))))


def encrypt(plain):
    return bytes(b ^ s for b, s in zip(plain, keystream(len(plain))))[::-1]


# --------------------------------------------------------------------- main

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    dll_path = os.path.join(here, 'input', 'first.dll')
    out_path = os.path.join(here, 'aitxt_extract.txt')

    data = open(dll_path, 'rb').read()
    off, size = find_aitxt(data)
    blob = data[off:off + size]
    if len(blob) != size:
        raise RuntimeError(f'short read: {len(blob)} < {size}')

    plain = decrypt(blob)
    if encrypt(plain) != blob:
        raise RuntimeError('round-trip check failed (unexpected cipher parameters)')

    io.open(out_path, 'w', encoding='utf-8', newline='').write(
        plain.decode('cp932', 'replace'))
    n_lines = len(plain.split(b'\r\n'))
    print(f'OK: {dll_path}')
    print(f'    AITXT at file offset 0x{off:X}, {size} bytes, {n_lines} lines')
    print(f'    -> {out_path}')


if __name__ == '__main__':
    sys.exit(main())
