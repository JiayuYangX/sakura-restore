# AITXT 分析文档

## 1. 背景

SSP ghost "first"（さくら）的人物对话数据存储在 `first.dll` 的 `.rsrc` 节中，资源类型名为 **AITXT**（AI Text，自定义 PE 资源类型）。

- Ghost 路径: `SSP\ghost\first\ghost\master\`
- 主 DLL: `first.dll` (890880 bytes)
- 其他 DLL: `misaki.dll` (482KB, 不含 AITXT), `sayuri.dll`/`mai.dll` (运行时释放)

## 2. .rsrc 节中的 AITXT 资源

```
.rsrc 节: 文件偏移 0xBB000, 大小 0x1E800 (124928 字节)
          虚拟地址 0xC3000

资源目录条目:
[NAME] AITXT ──── 自定义资源类型名
  [ID ] 101 ───── 资源标识符
    [DATA] 1041 ── 语言代码=日本語
      RVA:        0x000C4450 (相对虚拟地址)
      File offset:0xBC450
      Size:       0xDCC4 (56516 字节)
      First 16B:  C8 65 FB 3A E6 E2 53 AA A1 42 69 7B C8 60 36 24
```

AITXT 是 first.dll 中**唯一的一个 AITXT 类型资源**。.rsrc 中其他资源类型包括 LINKPNG、PNG、WAVE、Cursor、Bitmap、Icon、Menu、Form 等，全部条目首尾相连覆盖整个 .rsrc 节，没有隐藏数据。

## 3. 内存布局

SSP 运行时将 AITXT 资源解压到两个相邻的内存分配中：

```
0x0D2C0000 ─── 第一分配 (0xEC000 = 966KB, 完全提交)
  ├─ 0x0D2C0000-0x0D2C3FFF (16KB): 文本缓存 (CRLF 分行, 引用 first.txt 路径)
  └─ 0x0D2D0000-0x0D3BFFF6 (966KB): AITXT 条目主体 (3681 条)
                                          
0x0D3C0000 ─── 第二分配 (0x100000 = 1MB, 提交 0x4C000 = 311KB)
  ├─ 0x0D3C0000-0x0D3C0307 (0x308 字节): 指针表/哈希表
  │   指向第一 AITXT 分配 (0x0D3Axxxx 范围)
  └─ 0x0D3C0308-0x0D4BFEC: AITXT 条目子集 (655 条)
      ├─ Chunk1: 0x0D3C0000-0x0D3DFFFF (128KB, 提交)
      ├─ [GAP]:  0x0D3E0000-0x0D3E3FFF (16KB, 保留)
      ├─ Chunk2: 0x0D3E4000-0x0D3FBFFF (96KB, 提交)
      ├─ [GAP]:  0x0D3FC000-0x0D3FFFFF (16KB, 保留)
      └─ Chunk3: 0x0D400000-0x0D413FFF (80KB, 提交)

第一分配和第二分配在虚拟地址空间相邻。
```

两个区域**都来自同一个 56516 字节的压缩 AITXT 资源**。压缩率约 23x（1.27MB ÷ 56KB），对对话文本可行。

## 4. AITXT 条目格式

每条 AITXT 条目的固定结构：

```
[outer:4][inner:4][key_len:4][key_data:key_len]
```

- `outer` (uint32): 分类编号（1-500），表示对话/单词的类别
- `inner` (uint32): 子类型（1-10），1=对话词表，2=单词/控制字符
- `key_len` (uint32): key_data 的字节长度
- `key_data`: Shift-JIS 编码的文本数据

条目之间紧邻排列（第一区域），或偶尔有 1-7 字节的 padding（第二区域）。

## 5. 第一区域条目分析 (0x0D2D0000)

| 指标 | 数值 |
|------|------|
| 总条目数 | 3708 |
| inner=1 (对话词表) | 869 |
| inner=2 (单词/控制字符) | 2839 |
| ├─ 控制字符 (`\` 开头) | 1359 |
| └─ 独立单词 | 1480 |

### outer 分布 (日语 inner=1，有前驱 inner=2)

| outer | 数量 | outer | 数量 | outer | 数量 |
|-------|------|-------|------|-------|------|
| 22 | 98 | 26 | 116 | 30 | 63 |
| 34 | 30 | 38 | 30 | 42 | 24 |
| 46 | 29 | 50 | 19 | 54 | 10 |
| 58 | 5 | 62 | 7 | 66 | 11 |
| 70 | 6 | 74 | 6 | 78 | 1 |
| 82 | 4 | 86 | 2 | 90 | 2 |
| 94 | 4 | 98 | 3 | 102 | 2 |
| 106 | 1 | 110 | 4 | 114 | 2 |
| 118 | 2 | 122 | 2 | 126 | 3 |
| 130 | 1 | 134 | 1 | 138 | 1 |
| 146 | 2 | 154 | 1 | 158 | 1 |
| 162 | 1 | 174 | 2 | 1 | 1 |
| 23 | 1 | 27 | 1 | 31 | 2 |
| 39 | 2 | 47 | 1 | 59 | 1 |
| 83 | 1 | | | | |

### 条目关系规则（日语条目，100% 成立）

```
inner=2
  ├─ 以 \ 开头 (控制字符) → 下一条必是 inner=1 日语对话
  └─ 非 \ 开头 (独立单词) → 单独日语单词，无后续 inner=1

inner=1
  ├─ 有前驱 inner=2 控制字符 → 日语对话列表 (466 条)
  └─ 无前驱 inner=2 控制字符 → 中文翻译 或 SSP 运行时数据 (404 条)
```

**日语 inner=1 全部有前驱控制字符，0 条孤立。**

### 无前驱 inner=2 的条目 (404 条)

这两类混合在一起，按结构区分：

| 类别 | 数量 | 特征 | 地址范围 |
|------|------|------|----------|
| 中文翻译 | ~90 | inner=1, GBK 编码, 含 `\x01` 填充 | 0x0D2D0000-0x0D2D1800 |
| SSP 运行时数据 | ~314 | inner=1 但非对话：控制字符、变量、路径等 | 0x0D2D1800+ 及全区域 |

**中文翻译条目的识别方法：**
- 地址在 0x0D2D0000-0x0D2D1800 范围内
- inner=1 且无前驱 inner=2
- GBK 解码出中文字符（即使有 `\x01` 填充干扰）
- 已经全部找到对应翻译 (translated.csv)

**SSP 运行时数据** 包括：
- outer=18/19 的控制字符 `\ms`/`\mz`/`\me` (293 条)
- SSP 变量 `batteryrestvisible,1`, `energy,45`, `lastbathdate,...` 等 (11 条)
- 文件路径 (2 条)
- 纯数字 (5 条)

## 6. 第二区域条目分析 (0x0D3C0000)

| 指标 | 数值 |
|------|------|
| 总条目数 | 655 |
| inner=1 | 141 |
| inner=2 | 257 |
| inner=4 | 4 |
| inner=7 | 2 |

### 与第一区域的区别

| 特性 | 第一区域 | 第二区域 |
|------|----------|----------|
| 前缀 | 无 | 0x308 字节指针表 |
| 条目连接 | 紧密排列 | 有 1-7 字节 padding |
| inner 语义 | 1=对话 2=单词 | 不一致 |
| 内容重叠 | — | 53% 条目与第一区域相同 |
| outer 分布 | 22/26/30 为主 | 18/19 为主 |

### 第二区域 inner 含义不同

第二区域的 inner 值语义与第一区域不同：
- inner=1 中包含大量控制字符 (`\ms`, `\mz`, `\me`) 和变量名（`timer`, `alpha,255`, `analogclockform` 等）
- inner=2 中包含完整的日语句子（不全是单词）
- inner=4: 配置块（22 字节结构）
- inner=7: 窗口相关数据

**结论：第二区域不是独立的 AITXT，而是 SSP 运行时构建的索引/缓存结构。**

## 7. 关键发现汇总

1. **AITXT** 是 first.dll 的 PE 资源类型名称，不是人为命名
2. **56516 字节压缩数据** 解压后产生两个内存区域（共约 1.27MB）
3. **条目格式** [outer:4][inner:4][key_len:4][key_data] 通用
4. **区分规则** 日语条目：inner=2 控制字符 → inner=1 日语对话，严格配对（0 例外）
5. **中文条目 ~90 条**，全部在 0x0D2D0000-0x0D2D1800 范围，无前驱 inner=2
6. **无前驱 inner=2 的条目共 404 条**，其中 ~90 条中文，~314 条 SSP 运行时数据
7. **第二区域** 是运行时生成的辅助结构，不需要单独汉化
8. **`first.dll` 内的字符串表** "First"-"materia.exe"-"first.txt"-"AITXT"-"misaki.dll" 只是程序使用的名字列表，非文件映射
9. **translated.csv** (2910 条) 记录了对 first.dll 的十六进制修改，全部中文条目可在其中找到对应

## 8. 内存定位方法

重启 SSP 后 ASLR 会改变所有地址，但可通过特征签名重新定位。

### 定位步骤

1. **找到 SSP 进程**: 用 `CreateToolhelp32Snapshot` 匹配 `ssp.exe` / `materia.exe`
2. **搜索特征签名**: 在进程内存中搜索 `\ms,\female` 条目的 12 字节头部：
   ```
   1A 00 00 00   02 00 00 00   0B 00 00 00
   (outer=26)    (inner=2)     (key_len=11)
   ```
3. **识别分配区域**: 按 `AllocationBase` 分组命中数
   - 命中多的（~36 次）→ 主 AITXT
   - 命中少的（~1-3 次）→ 第二区域

### 地址映射注意事项

主分配内存布局不连续，有保留页间隙：
```
0x0E030000-0x0E033FFF (16KB)  已提交 - 文本缓存
0x0E034000-0x0E04FFFF (48KB)  保留     ← 间隙！
0x0E050000-0x0E13BFFF (0xEC000) 已提交 - AITXT 条目主体
0x0E13C000-0x0E13FFFF (16KB)  保留     ← 间隙！
```

因此导出 CSV 中**使用相对偏移**而非绝对 VA。偏移量从条目起始基址计算，跨重启保持稳定。

### 验证结果

```
SSP 重启前              重启后
PID:  4732              6416              16248
Main: 0x0D2C0000        0x0A940000        0x0E030000
Base: 0x0D2D0000        0x0A950000        0x0E040000
First offset: 0x2E50    0x2E50            0x2E50      ✓ 稳定
```

完整的定位脚本见 `extract_aitxt.py`。

## 9. extract_aitxt.py 导出脚本

位于 `D:\JiaYu\Downloads\sakura-restore\extract_aitxt.py`。

```python
用法:
    python extract_aitxt.py                    # 仅 Region 1
    python extract_aitxt.py --region2           # + Region 2
    python extract_aitxt.py --region2 --filter-chinese  # + 过滤中文
```

输出 CSV 格式：
```
Region | Offset | Outer | Inner | KeyLen | Type  | Text
1      | 0x2E50 | 26    | 2     | 10     | word  | ピカチュウ
1      | 0x2E68 | 18    | 2     | 3      | ctrl  | \ms
1      | 0x2E78 | 62    | 1     | 46     | dialogue | 大谷育江...
```

Type 含义（Region 1）：
- `ctrl`：控制字符，以 `\` 开头（不需翻译）
- `word`：独立单词（需翻译）
- `dialogue`：对话词表，有前驱控制字符（需翻译）

统计（不带参数默认导出）：
```
Region 1: ~3274 条 (dialogue ~447, word ~1480, ctrl ~1359)
```

CSV 文件位于 `D:\JiaYu\Downloads\sakura-restore\translated.csv`，格式：
```
Offset,Length,Pad,Text
```

- **Offset**: first.dll 内的文件偏移
- **Length**: 替换数据的字节长度
- **Pad**: 填充字节 (01 或 00)
- **Text**: UTF-8 编码的中文译文

所有在 AITXT 内存中发现的中文条目均可在 CSV 中找到对应。CSV 中两种条目类型：
- **精确替换**: offset 在 0xA8650-0xA94BC 区间，长度与 AITXT 中原文一致
- **完整台词**: offset 更分散，包含 SSP 控制代码 (`\w8`, `\s0` 等) 的完整上下文

## 10. 附: find_aitxt.py 定位脚本

```python
import ctypes, struct

def find_aitxt():
    """Locate AITXT regions in SSP process memory."""
    k = ctypes.WinDLL('kernel32', use_last_error=True)

    class PE32(ctypes.Structure):
        _fields_ = [('dwSize', ctypes.c_uint), ('cntUsage', ctypes.c_uint),
                    ('th32ProcessID', ctypes.c_uint), ('th32DefaultHeapID', ctypes.c_void_p),
                    ('th32ModuleID', ctypes.c_uint), ('cntThreads', ctypes.c_uint),
                    ('th32ParentProcessID', ctypes.c_uint), ('pcPriClassBase', ctypes.c_long),
                    ('dwFlags', ctypes.c_uint), ('szExeFile', ctypes.c_char * 260)]

    pid = None
    s = k.CreateToolhelp32Snapshot(2, 0)
    if s > 0:
        p = PE32(); p.dwSize = ctypes.sizeof(PE32)
        if k.Process32First(s, ctypes.byref(p)):
            while True:
                if p.szExeFile.lower() in (b'ssp.exe', b'materia.exe'):
                    pid = p.th32ProcessID; break
                if not k.Process32Next(s, ctypes.byref(p)): break
        k.CloseHandle(s)
    if not pid: raise RuntimeError('SSP not running')

    hdr = bytes([0x1A, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x0B, 0x00, 0x00, 0x00])

    class MBI(ctypes.Structure):
        _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                    ('AllocationProtect', ctypes.c_uint), ('RegionSize', ctypes.c_size_t),
                    ('State', ctypes.c_uint), ('Protect', ctypes.c_uint), ('Type', ctypes.c_uint)]

    h = k.OpenProcess(0x10 | 0x400, False, pid)
    if not h: raise RuntimeError(f'Cannot open PID {pid}')

    alloc_hits = {}
    addr = 0x01000000
    while addr < 0x7FFFFFFF:
        m = MBI()
        if not k.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(m), ctypes.sizeof(MBI)): break
        if m.State == 0x1000 and m.RegionSize >= len(hdr):
            chunk = min(m.RegionSize, 0x10000)
            buf = ctypes.create_string_buffer(chunk)
            br = ctypes.c_size_t(0)
            if k.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, chunk, ctypes.byref(br)) and br.value >= len(hdr):
                d = buf.raw[:br.value]; pos = 0
                while True:
                    idx = d.find(hdr, pos)
                    if idx == -1: break
                    alloc_hits.setdefault(m.AllocationBase, []).append(addr + idx)
                    pos = idx + 1
        addr += m.RegionSize
    k.CloseHandle(h)

    if not alloc_hits: raise RuntimeError('No AITXT regions found')
    sorted_ab = sorted(alloc_hits.items(), key=lambda x: -len(x[1]))
    main_ab = sorted_ab[0][0]
    second_ab = sorted_ab[1][0] if len(sorted_ab) > 1 else None
    return main_ab, second_ab, pid
```

## 11. 未解决的问题

- 压缩算法未知（无法从 first.dll 解压比对）
- 第一区域和第二区域条目在内存中的具体拆分逻辑不清
- 第二区域的指针表具体功能未完全确定
