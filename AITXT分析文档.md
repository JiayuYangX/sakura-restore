# AITXT 分析文档

> 重写版（2026-09-23）。本版整合了资源本体的完整破解结论：**AITXT 不是压缩数据，而是加密数据**；旧版文档基于运行时内存观察得出的"压缩/解压"表述已修正。
>
> 分析对象：`input/first.dll`
> 大小 890,880 字节，MD5 `A380E8E5AC1BCE5BE28E067D19749DDE`
> （与 MATERIA 安装包自带的 first.dll 完全相同）

---

## 1. 概述

AITXT 是 first.dll 的一个**自定义 PE 资源类型**（"AI Text"），存放人物的随机话题/单词词库。

- 对应老 SHIORI 协议：`GET Word`（配合剧本中的 `\a` 标签 / `OnAITalk` 事件）
- 数据内容：**「单词 / 类型 / 关联词列表」三元组**，共 1,615 组
- 资源本体：56,516 字节的 **Shift_JIS 文本**（4,846 行，CRLF）
- 加密方式：整块反转 + MT19937 密钥流异或（非压缩；见 §3）
- 当前状态：解密、翻译、重新加密写回的**完整工具链已打通**（见 §7）

---

## 2. 资源定位（PE 结构）

```
.rsrc 节：文件偏移 0xBB000，大小 0x1E800（124,928 字节），RVA 0xC3000
          换算关系：文件偏移 = RVA − 0x8000

资源目录：
[AITXT]  ← 类型（字符串型）：first.dll 中唯一的 AITXT 类型资源
  [101]  ← ID（整数型）
    [1041]  ← 语言（0x0411 = 日语）
      数据 RVA：0xC4450  →  文件偏移 0xBC450
      大小：    0xDCC4（56,516 字节）
      密文前 16 字节：C8 65 FB 3A E6 E2 53 AA A1 42 69 7B C8 60 36 24
```

- .rsrc 中其他资源为 LINKPNG / PNG / WAVE / Cursor / Bitmap / Icon / Menu / Form 等，条目首尾相连，无隐藏数据。
- 提取与写回均通过 **PE 资源目录动态定位**（不依赖硬编码偏移）；写回时同步更新数据项的 Size 字段。
- 数据项 Size 字段 = 逻辑大小；实际可用槽位 = 从 0xBC450 到下一个资源为止的 56,516 字节。

---

## 3. 加密算法（完全破解，逐字节验证）

### 3.1 解密

```text
plain = xor_stream(reverse(cipher))
keystream[i] = MT19937(seed2).rand(0x7FFFFFFF) & 0xFF
```

### 3.2 seed2 的推导

```text
MT19937(9821) → r1 = rand(0x7FFFFFFF) = 1435897944
MD5("1435897944") = 266150ead4fad1491a2e46d623ea9e00
过滤出数字字符        = "26615041491246623900"
取前 9 位             = 266150414   ← seed2
```

### 3.3 实现细节（与 Delphi / x87 行为对齐）

- MT19937 初始化：`mt[0] = seed & 0x7FFFFFFF`，`mt[i] = (mt[i-1] * 69069) mod 2^32`
- 标准 twist（N=624, M=397, mag01 = {0, 0x9908B0DF}）+ 标准 tempering
- `rand(n)` = 就近取整 `u32 / 2^32 * (n-1)`；恰好 0.5 时进位（常数含 `+2^-58` 的 epsilon）
- 若 MD5 结果无数字字符：`seed2 = rand(0x109A0)`（实际未触发）
- **加密 = 逆操作**：`reverse(xor_stream(plain))`，round-trip `encrypt(decrypt(x)) == x` 已验证

### 3.4 first.dll 内的实现位置

见 §10 附录 A（装载 0x45F390、反转 0x45F64C、异或 0x45F5AC、MD5 0x45EF44/0x45EFB8、MT 状态 0x4AF9A8 …）。

---

## 4. 明文格式

### 4.1 总体结构

- 编码 Shift_JIS，行分隔 CRLF，共 **4,846 行 = 1,615 组 × 3 行 + 1 行尾空行**
- 文件首个词条为 `ピカチュウ`（解密后可立即核对）
- 除 CR/LF 外无控制字符

### 4.2 三元组

```text
第 1 行  词条（全部非空）
第 2 行  类型 / 关系：
          \... 标记 1,555 条（如 \ms、\k、\dg、\mz…）
          =别名     57 条（如 =あゆ、=ブス、=さくら）
          %符号      3 条（%ms ×2、%mh ×1，见 §9）
第 3 行  关联词列表（逗号分隔，`-` 为占位符；1,171 条为空）
```

### 4.3 统计

| 项目 | 数值 |
|---|---|
| 组数 | 1,615 |
| 类型行 | `\` 标记 1,555 / `=别名` 57 / `%` 3 |
| 关联词行 | 非空 444 / 空 1,171 |
| 重复词条 | 14 |
| 常见标记（前几名） | `\k` 276、`\dg` 233、`\ms` 212、`\mz` 201、`\me` 69、`\mc` 57、`\ml` 54、`\ml,\k` 50、`\mp` 50、`\dn,\dg` 49 |

### 4.4 别名语义

第 2 行的 `=X` 表示该词条是 X 的别名（引擎按**字符串**查找 X 的词条）。
因此翻译时必须保证 **X 的译文与词条 X 的译文一致**，否则别名失效。

已知悬空别名（原库就没有目标词条）：

- `ケロちゃん` → `=ケルベロス`（`ケルベロス` 只出现在关联词里，无词条）
- `2ちゃん` → `=2ch`（同上）

---

## 5. 与「GET Word」协议的关系

### 5.1 老规范（usada 版 SHIORI 文档）

- 请求：`GET Word SHIORI/2.0`，`Type:` 头取值：
  `\ms` 名詞-人、`\mz` 名詞-無機物、`\ml` 名詞-集合、`\mc` 名詞-社名、`\mh` 名詞-店名、
  `\mt` 名詞-技、`\me` 名詞-食物、`\mp` 名詞-地名、`\m?` 非限定、`\dms`「～に～する～」
- 应答：`Word: さくら`（无合适词时返回空）
- 剧本 `\a` 标签 = 老随机话题 → `OnAITalk` 事件

### 5.2 基座显示侧：`%ms` 元字符

UKADOC「メタ文字列」规定的 `%ms` / `%mz` / `%ml` / `%mc` / `%mh` / `%mt` / `%me` / `%mp` / `%m?` / `%dms`，
是**台词里的显示期替换标记**；基座取随机词的实际来源就是本词库。
注意区分：**台词用 `%`，词库类型行用 `\`**。

### 5.3 SSP 现状

- ssp.exe 保留了旧接口的兼容实现（字符串 `GET Word SHIORI/2.0`、`Type:`、`\dms`、`OnAITalk` 等）
- 但实测（SHIORI 日志）SSP **不会给本 ghost 发 GET Word 请求** → 该通道在 SSP 默认配置下休眠
- 词库匹配机制：消费者代码按 **「类型串包含查询串」的子串匹配**（0x40412C = Pos），
  例如以 `\ms` 查询会命中类型为 `\ms`、`\ms,\male`、`\ms,\female` 等的条目

---

## 6. 运行时机制（first.dll）

### 6.1 装载

```text
load()（0xA9774）
  └─ 0x45F390：FindResourceA("#101","AITXT") → LoadResource → SizeofResource → 复制
       ├─ 0x45F64C：整块反转
       └─ 0x45F5AC：MT19937 密钥流异或
  └─ 结果按 CRLF 拆入 TStringList（0x4B2B18）
       ├─ i % 3 == 0 → 词条表（0x4B2B14）
       └─ i % 3 == 1 → 类型表（0x4B2B10）
```

### 6.2 消费

- `0x86BE4`：GET Word 分发器（0x795E0）调用；`Type == \dms` 等分支用查询串
  `\ms,\mz`、`\dn`、`\dw`、`\ms` 检索词库
- `0xA4B14`：请求解析器（0x71AB0）调用

---

## 7. 翻译工作流（当前工具链）

### 7.1 流程

```text
aitxt_extract.csv / aitxt_translated.csv   ← 译文表（源头，2,107 行）
        │  build_from_csv.py
        ▼
aitxt_translated.txt（UTF-8，审查/对照）＋ GBK 文本（供写回）
        │  patch_dll.py
        ▼
output/first.dll   AITXT 加密写回 + 字符串汉化 + 兼容补丁
```

### 7.2 脚本一览

| 脚本 | 作用 | 输入 → 输出 |
|---|---|---|
| `extract_aitxt.py` | 零参数，从 `input/first.dll` 提取解密 AITXT | → `aitxt_extract.txt`（UTF-8，日文） |
| `build_from_csv.py` | 从译文 CSV 生成资源文本（不含任何修改规则） | CSV → `aitxt_translated.txt` + GBK 版 |
| `fixes_common.py` | 5 条资源里存在、CSV 里没有的补充行 | 被测脚本引用 |
| `patch_dll.py` | 全量补丁：字符串（translated.csv）+ 兼容补丁 + AITXT | `output/first.dll`（可带参数自动复制到部署路径） |
| `aitxt_crypto.py` | 旧辅助：dump / build / verify（硬编码偏移版，保留备查） | — |
| `extract2csv.py` | 注意：这是 **first.dll 字符串**提取（另一条汉化线，与 AITXT 无关） | → `extract.csv` |

### 7.3 尺寸与编码约束

- 资源槽位上限：**56,516 字节**；当前译文约 46.5KB（随译稿变动），余量充足
- 写回流程：新数据块写入 0xBC450 起，**更新数据项 Size 字段**，槽位剩余部分清零
- loader 只读 SizeofResource 给定的长度 → 缩小无副作用；**增大超过槽位需要 PE 手术**（追加数据 + 扩 .rsrc 或新节 + 改数据项 RVA），当前未实现
- 译文以 **GBK** 写入 DLL；GBK 无法表示的字符自动做 NFKC 回退（如半角片假名 → 全角）
- 当前有意保留的假名：`ギコペ`、引号里的 `ー`（实例：`音引き…`）、`水爆ヲィコラ`

### 7.4 翻译规则

1. 只翻译第 1、3 行；第 2 行的 `\...` 标记原样保留
2. `=别名` 的目标必须与对应词条译文一致（§4.4）
3. `%ms`/`%mh` 三条按原样保留（§9）
4. CSV 是唯一源头；`aitxt_translated.txt` 由 CSV 生成，不要只改 txt（会被下次生成覆盖）

### 7.5 验证

- 写回前：`encrypt(decrypt(blob)) == blob`（round-trip）
- 写回后：解密出的资源内容与 `aitxt_translated.txt` 逐字节一致（抽取脚本可复核）

---

## 8. 旧内存提取方法与 CSV 来源（历史）

> 现 CSV（`aitxt_extract.csv` / `aitxt_translated.csv`）骨架来自早期的**运行时内存扫描**，本版保留记录供追溯。

- 方法：在运行中的 SSP/MATERIA 进程里搜索特征签名（如 `\ms,\female` 条目的头部
  `1A 00 00 00 02 00 00 00 0B 00 00 00`），按 `AllocationBase` 归类命中，ASLR 下用相对偏移保证稳定
- 内存记录结构：`[outer:4][inner:4][keylen:4][text]`
  - `inner = 2`：词条 / 控制行（单词、`\...`、`=...`）
  - `inner = 1`：关联词列表行
  - `outer`：类别/索引字段（确切语义仍未完全确认）
- 内存顺序 ≠ 文件顺序；CSV 与资源行的对应是后来按**文本匹配**建立的
  （覆盖：词条 1,609/1,615、关联词 441；缺失行由 §9 补充）
- 早期内存样本中出现的约 90 条 GBK 中文，来自当时运行中的替换（makoto / 字符串补丁），
  **资源本体始终是纯 SJIS**，不含中文
- 该内存提取法已被资源级提取（`extract_aitxt.py`）取代；CSV 现仅作为译文表保留

---

## 9. 已知问题 / 待办

| 项 | 说明 | 建议 |
|---|---|---|
| `%ms`×2、`%mh`×1 | 词库类型行应为 `\`；这类条目不会被查询命中（休眠） | 判定为笔误；要利用这 3 个词就改成 `\ms` / `\mh`（バイソン将軍、キャプテンサワダ、NERV） |
| 悬空别名 | `=ケルベロス`（ケロちゃん）、`=2ch`（2ちゃん）在原库就无目标词条 | 保留原样，或补词条 |
| 5 个保留词条 | `SYNTAX ERROR`、`水爆ヲィコラ`、`NumberFormatException`、`NullPointerException`、`APTX4869` | 有意不译 |
| 5 条补充行 | 资源行 41 / 146 / 150 / 623 / 661 不在 CSV 中 | 由 `fixes_common.py` 维护 |
| 增大不支持 | 译文超槽位需 PE 手术 | 需时再实现 |
| `outer` 语义 | 运行时记录的类别字段含义未定 | 需要时进一步逆向 |
| SSP 不触发 | 默认配置下 GET Word 通道休眠，译文无可见效果 | 保留为老基座/兼容模式可用与数据完整 |

---

## 10. 附录

### A. 关键地址表（first.dll；VA = 文件偏移 + 0x400C00）

| 地址 | 用途 |
|---|---|
| 0xA9774 | load() 内调用装载 |
| 0x45F390 | 资源装载（FindResourceA/LoadResource/SizeofResource/复制） |
| 0x45F64C | 整块反转 |
| 0x45F5AC | MT19937 密钥流异或 |
| 0x45EF44 / 0x45EFB8 | MD5 相关 |
| 0x4ADC54 | MD5 十六进制字符表 |
| 0x4AF9A8 | MT 状态数组；索引 0x4ADC08；mag01 0x4ADC0C |
| 0x45E430 / 0x45E4C8 / 0x45E2E8 | 种子设置 / Random / random float |
| 0x4B2B18 / 0x4B2B14 / 0x4B2B10 | 行表 / 词条表 / 类型表（TStringList） |
| 0x795E0 | GET Word 分发器 |
| 0x86BE4 | 词库查询（关联匹配） |
| 0x71AB0 / 0xA4B14 | 请求解析器 / 词库查询 |
| 0x40412C | 子串匹配（Pos） |

### B. 命令速查

```text
python extract_aitxt.py                     # input/first.dll → aitxt_extract.txt（日文）
python build_from_csv.py                    # CSV → aitxt_translated.txt（中文，UTF-8）
python patch_dll.py                         # 全量补丁 → output/first.dll
python patch_dll.py "C:\...\SSP\ghost\first\ghost\master\first.dll"   # 构建并复制部署
python aitxt_crypto.py verify               # （旧工具）round-trip 自检
```

### C. 文件对照

| 文件 | 说明 |
|---|---|
| `aitxt_extract.csv` / `aitxt_translated.csv` | 词库译文表（日/中，2,107 行，行对齐） |
| `aitxt_extract.txt` / `aitxt_translated.txt` | 资源全文（UTF-8，4,846 行，可 diff 对照） |
| `translated.csv` | **first.dll 字符串**译文表（与 AITXT 无关，勿混淆） |
| `fixes_common.py` | 5 条补充行 |
| `input/first.dll` | 原版 |
| `output/first.dll` | 补丁产物（字符串 + 兼容补丁 + AITXT） |

### D. 验证记录

- `encrypt(decrypt(resource)) == resource`（原版/补丁版均通过）
- 补丁版解密切片与 `aitxt_translated.txt` 逐字节一致
- `aitxt_extract.txt` 与旧工具 dump 逐字节一致
- 早期补丁实验：`ピカチュウ → 皮卡丘`（GBK）回写后解密读回一致
