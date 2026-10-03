#!/usr/bin/env python3
"""
一次完成 first.dll 的全部补丁，输出到 output/。
可选：命令行第一个参数 = 部署目标（ghost master 目录，或该目录下任一 dll 路径），
first.dll 会被复制过去。

first.dll：

  文本翻译（CSV）：
    Offset = 写入位置，Length = 最大字节数，Type = code|answer|rsrc|font。
    - code：off-4 处为 4 字节小端长度，写入后更新长度并清零剩余
    - answer：文本按编码规则（默认 GBK）转为「反转大写 hex」再写入
      （off-4 处长度同步更新；新字节数不得超过原长，否则报「答案超长」跳过）
    - rsrc：off-1 处为 1 字节长度，写入后更新长度并清零剩余
    - font：无长度前缀，用 \x00 补齐
    - pchar：无长度前缀的 PChar 字面量（前面不是字符串头，禁写 off-4），
      只写内容+NUL，容量 = 原长+3

  兼容补丁（写入前逐字节校验原值）：
    1. NOTIFY -> 按 GET 分发（把 0x719E9 处的 jne 填成 NOP）
       first.dll 只实现了 GET；SSP 2.5.33+ 在 cantalk=0 时会把后台事件
       以 NOTIFY 发来，之前会收到 400 并卡死状态机。
    2. r"\\![enter,inductionmode]" 字符串长度 23 -> 0（0x79E08）
       诱导模式会让 cantalk 永远保持 false，导致后台事件走 NOTIFY
       （响应被忽略）、泡澡结束的对话不可见。

  AITXT（词库）：aitxt_translated.txt（UTF-8）-> GBK -> 加密数据块，覆盖 PE 资源
    目录中定位到的 AITXT 资源，并更新资源数据项的 Size 字段。写入前 round-trip 校验。

  链接化补丁（海原雄山）：「自动加链接」名单由 7 段固定序列注册，最后一段（木野さん）
    的 call 重定向到 .cave 小桩：先补完原调用，再对「海原雄山」常量调用一次注册。

  RSS 链接补丁（OnAnchorSelect 打开浏览器）：兜底分支入口重定向到 .cave 第二桩：
    Ref0 以 "http" 开头则拼出 "\\C + 关超时 + \\j[<URL>]" 返回——\\C 追记到当前气球
    （不开始新一轮 talk → 新闻气泡不关，可连续点击；社区标准模式，见 URL_RESP_PREFIX）。

  游戏 / 双击相关补丁（.cave 内若干桩，详见各构建函数文档串）：
    - 桩A（响应监控，挂 0x47A399）：输入框标志、视力游戏状态（MARK/EYEBUSY）、
      游戏退出收尾（PENDING+GAMELEFT）、游戏菜单缓存、视力取消输入框按“空提交”。
    - 桩B（双击判定，挂 0x4782BD）：菜单/输入框/视力答题中双击吞掉；打字/问答
      双击重放该游戏菜单；视力已进入但未弹框时置 PENDING + 调关窗小段。
    - 关窗体辅助桩：FindWindowA + WM_CLOSE 关 Ttypinggameform / Teyesightform /
      Tcountdownform；CloseQuery 跳板保证 GAMELEFT 期间打字框可关。
    - 打字定向屏蔽（挂 0x47239D 的 ConvertAll 调用处）：打字游戏进行中只豁免
       "玩家输入脚本"与"要打文本"子串，提示语/结算语照常转换；退出游戏恢复原行为
      （详见 _build_typing_gate_stub 与《特殊对话分析及修复.md》§8）。
    - 重力语中文化（对话 0x418D21 / 菜单窗口 0x46FC03 两处 call 0x417048 → 表驱动变换桩）：
      原版会变的 SJIS 字符凡在 GBK 存在的逐字复刻（1044 条映射表）+ [ ] 指令区原样 +
      % 保留（不动搜索的 URL 编码调用点 0x476548）。

  高分屏缩放与拖动：
    - load / request 导出包装：请求期间线程置 UNAWARE_GDISCALED(-5)，把 DLL 自建
      窗口交给系统按屏幕缩放（含 GDI 自绘文字）；请求之外 SSP 自身界面不受影响。
    - 拖动修复：6 处 FormMouseMove 里 SC_DRAGMOVE 的 SendMessageA 调用改为经过
      .cave 包装桩（拖动模态循环期间线程 GDISCALED）。
    - 输入法修复（.cave IME 段，随 load 安装/unload 还原；两门控：MATERIA 等 unaware 宿主
      安装时自动整体 no-op，运行时仅 GDISCALED(96dpi) 窗口生效；动态 DPI 因子三重修复）：
      ① 候选框空态位置（msctf 空态分支内 prc 以窗口原点为中心 ×f）+ 感知修正；
      ② 组字窗位置（摆位锚点 ×f，拖动自动跟随）；③ 组字窗字号（SelectObject 钩）。
      详见《窗口分析及修复.md》§9。

  状态栏重影修复（原版缺陷）：TStatusBar 的窗口类缺 CS_HREDRAW，拖动改变 Todo/Notify
    宽度时系统不做整窗失效、旧像素残留（文字重影）。在 TWinControl.CreateWnd 调用虚拟
    CreateParams 的指令处（0x4352C1）挂透明桩：仅当栈帧里类名为 "TStatusBar" 时给
    Params.WindowClass.style 补 CS_HREDRAW；其余类原样通过，不碰 RegisterClassA。

first.dll 的 ULW 层（patch_ulw_hitmask，含命中掩码与跨 100% 刷新修复）：
  ① 命中掩码：监控窗为 WS_EX_LAYERED + UpdateLayeredWindow 逐像素 alpha 窗口，系统按
     图层 alpha 做鼠标命中判定（alpha==0 穿透）。100% 缩放时判定与显示一一对应；DPI
     虚拟化（≠100%）下系统读取图层的坐标被放大 2 倍，命中区与显示错位。修复 = 每次上屏
     前仅对两个监控窗的 32bpp 图层把内容按 1/2 缩半写成不可见命中标记（alpha=1/255）；
     =100% 不做任何修改。
  ② 跨 100% 缩放刷新修复（原 misaki 子类化方案已迁入本层）：上屏桩每帧核对并（重）挂
     监控窗的 WndProc 子类化；WndProc 桩在 WM_WINDOWPOSCHANGED 门控"向上穿越 100%"时
     cloak + WS_EX_LAYERED 往返重建分层表面，待解除 cloak 标记由下一次上屏桩处理
     （详见《窗口分析及修复.md》§10）。

first.dll 退出崩溃修复层（patch_exit_fix + V7 归还 patch_ctx_return_on_detach）：
  SSP 退出/重载/切人格时，模块卸载后残留的"僵尸活动"（消息派发等）会落到已卸载代码上，
  造成退出崩溃（0x1476a / 0x7474 一族）；而提前断路又会破坏收尾/存档（0x2E44 一族）。
  定稿 = 两件套：
  1) unload 导出经 EAT 重定向到存根（.cave+0x2400）：先按模块范围/监控窗类名清扫一轮
     WndProc → 线程切 UNAWARE_GDISCALED 并把旧上下文经 SetPropA 存到 SSPMAIN 的
     "dpictx" 属性 → 销毁两个注册窗体（触发原生存档）→ call 原 teardown 完好入口等其
     返回 → 跳收尾例程（只做 EnumWindows 断路：WndProc 在本模块范围或监控窗类名的窗口
     换 DefWindowProcA；不恢复 DPI 上下文，线程保持 -5 到卸载完成，保证迟到存档也读虚拟
     坐标）；
  2) 归还（V7）：DllMain 的 DLL_PROCESS_DETACH 末尾（入口 detour）从 "dpictx" 读回旧
     上下文归还——触发点绑定"本模块自己的卸载"，重载/切人格/退出全覆盖。
  全程同步：无定时器、无辅助线程、无轮询。

"""
import csv, hashlib, os, sys, struct, shutil, unicodedata

# 需要按 Shift-JIS 写入的偏移（其余一律 GBK）。
# 空白：原先 12 条窗口 label 文案必须 SJIS（所在窗体 Font.Charset=SHIFTJIS_CHARSET，
# TLabel 走 GDI 自绘、按字体 charset 的代码页 CP932 解释字节）；
# 现已把 Tfirstconfigform / Tnotifyform 的 Font.Charset 改成 GB2312_CHARSET
# （见 patch_dfm_charset），这些 label 的字节改按 ACP(936) 解释，随大流用 GBK。
SHIFTJIS_OFFSETS = set()

# （偏移, 原始字节, 替换字节）
PATCHES = [
    # 1. NOTIFY -> 按 GET 分发
    (0x719E9, bytes.fromhex('0F 85 AF 7E 00 00'), b'\x90' * 6),
    # 2. r"\![enter,inductionmode]" 字符串长度 23 -> 0
    (0x79E08, bytes.fromhex('17 00 00 00'), bytes.fromhex('00 00 00 00')),
]


IME_ENABLE = True


def apply_patches(data: bytes) -> bytes:
    out = data
    for off, orig, repl in PATCHES:
        if out[off:off + len(orig)] != orig:
            raise RuntimeError(
                f'offset 0x{off:X} mismatch: expected {orig.hex()}, '
                f'got {out[off:off + len(orig)].hex()}'
            )
        out = out[:off] + repl + out[off + len(repl):]
    return out


# ------------------------------------------------- DFM 窗体字体编码

DFM_CHARSET_OLD = b'\x07\x10SHIFTJIS_CHARSET'   # 值编码：[0x07][长度][标识符]
DFM_CHARSET_NEW = b'\x07\x0EGB2312_CHARSET'
DFM_CHARSET_FORMS = (b'Tfirstconfigform', b'Tnotifyform')


def patch_dfm_charset(data: bytearray) -> bytearray:
    """把两个窗体（Tfirstconfigform / Tnotifyform）DFM 里的 Font.Charset
    从 SHIFTJIS_CHARSET 改成 GB2312_CHARSET。

    背景：DLL 是 ANSI 程序，`TLabel` 由 VCL 用 GDI 的 ANSI 文本函数自绘，
    GDI 按「当前字体 charset 对应的代码页」解释字节——这两个窗体原设
    SHIFTJIS_CHARSET（CP932），所以窗内 label 文案必须写 Shift-JIS；
    改成 GB2312_CHARSET 后按 CP936 解释，label 文案可随大流用 GBK
    （窗内的按钮/勾选框等窗口控件本来就走 ACP，不受影响）。

    标识符 16→14 字节：把该值之后到本窗体数据块末尾的内容整体左移 2 字节、
    块尾补 0（块总长不变，后续资源位置不动）。**必须在文本翻译写入之后执行**
    （翻译按原始偏移写好后随这次移位一起平移，保持 DFM 结构自洽）。
    """
    delta = len(DFM_CHARSET_OLD) - len(DFM_CHARSET_NEW)
    pos = 0
    n = 0
    while True:
        p = data.find(b'Font.Charset' + DFM_CHARSET_OLD, pos)
        if p < 0:
            break
        vs = p + len(b'Font.Charset')               # 值起始（0x07 处）
        ts = data.rfind(b'TPF0', 0, p)              # 所属窗体数据块
        ln = data[ts + 4] if ts >= 0 else 0
        cls = bytes(data[ts + 5:ts + 5 + ln]) if ts >= 0 else b''
        if cls in DFM_CHARSET_FORMS:
            nxt = data.find(b'TPF0', ts + 4)        # 本地区块末尾（下一个窗体起点）
            if nxt < 0:
                nxt = len(data)
            data[vs:vs + len(DFM_CHARSET_NEW)] = DFM_CHARSET_NEW
            data[vs + len(DFM_CHARSET_NEW):nxt - delta] = \
                data[vs + len(DFM_CHARSET_OLD):nxt]
            data[nxt - delta:nxt] = b'\x00' * delta
            n += 1
        pos = vs + len(DFM_CHARSET_NEW)
    if n != 2:
        raise RuntimeError(f'DFM Font.Charset 修补数量异常: {n}（期望 2）')
    return data


# ------------------------------------------------- 链接化补丁（海原雄山）

LINKIFY_CALL_OFF = 0xA9E37          # 最后一段（木野さん）的 call 0x4AA838
LINKIFY_CALL_ORIG = bytes.fromhex('E8 FC FD FF FF')
LINKIFY_CALL_NEXT_VA = 0x4AAA3C     # 该 call 的下一条指令（pop ecx）
LINKIFY_ADD_FUNC = 0x4AA838         # 名单注册函数（EAX=常量指针）
LINKIFY_EXTRA_STR = 0x4875E0        # 「海原雄山」常量数据指针（翻译后为 GBK）


def _build_linkify_stub(rva):
    """26 字节位置无关桩：
       进入时 EAX=木野さん（原调用方已设好），先按原逻辑注册；
       再用 call/pop/add 取得「海原雄山」常量指针并注册；最后返回。
    """
    b = bytearray()
    va = lambda i: rva + i

    b += b'\x55'                                              # push ebp
    b += b'\xE8' + struct.pack('<i', LINKIFY_ADD_FUNC - va(6))   # call add
    b += b'\x59'                                              # pop ecx
    b += b'\xE8\x00\x00\x00\x00'                              # call $+5
    b += b'\x58'                                              # pop eax (= va(12))
    b += b'\x05' + struct.pack('<i', LINKIFY_EXTRA_STR - va(12))  # add eax, delta
    b += b'\x55'                                              # push ebp
    b += b'\xE8' + struct.pack('<i', LINKIFY_ADD_FUNC - va(24))  # call add
    b += b'\x59'                                              # pop ecx
    b += b'\xC3'                                              # ret
    assert len(b) == 26
    return bytes(b)


def add_cave_section(data: bytearray, code: bytes) -> int:
    """在文件末尾追加只读写+可执行的 .cave 节，返回其 RVA。"""
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt_size = _u16(data, e + 20)
    opt = e + 24
    sec_tab = opt + opt_size
    first_raw = min(_u32(data, sec_tab + 40 * i + 20) for i in range(nsec))
    if sec_tab + (nsec + 1) * 40 > first_raw:
        raise RuntimeError('PE 头空间不足，无法追加节')
    max_end = max(_u32(data, sec_tab + 40 * i + 12) + _u32(data, sec_tab + 40 * i + 8)
                  for i in range(nsec))
    new_rva = (max_end + 0xFFF) & ~0xFFF
    raw = (len(data) + 0x1FF) & ~0x1FF
    vsize = len(code)
    rawsize = (vsize + 0x1FF) & ~0x1FF
    if raw > len(data):
        data.extend(b'\x00' * (raw - len(data)))
    data.extend(code)
    data.extend(b'\x00' * (rawsize - vsize))
    hdr = sec_tab + nsec * 40
    data[hdr:hdr + 8] = b'.cave\x00\x00\x00'
    struct.pack_into('<I', data, hdr + 8, vsize)
    struct.pack_into('<I', data, hdr + 12, new_rva)
    struct.pack_into('<I', data, hdr + 16, rawsize)
    struct.pack_into('<I', data, hdr + 20, raw)
    struct.pack_into('<I', data, hdr + 24, 0)
    struct.pack_into('<I', data, hdr + 28, 0)
    struct.pack_into('<H', data, hdr + 32, 0)
    struct.pack_into('<H', data, hdr + 34, 0)
    struct.pack_into('<I', data, hdr + 36, 0xE0000020)  # CODE|EXECUTE|READ|WRITE
    struct.pack_into('<H', data, e + 6, nsec + 1)
    new_soi = new_rva + ((vsize + 0xFFF) & ~0xFFF)
    old_soi = _u32(data, opt + 56)
    struct.pack_into('<I', data, opt + 56, max(old_soi, new_soi))
    return new_rva


# OnAnchorSelect 兜底分支：文件 0x795CE 处
#   lea eax,[ebp-0x1c] / mov edx,0x48769C
# 替换为 jmp .cave 第二桩（原逻辑在桩里复刻）
URL_HOOK_OFF = 0x795CE
URL_HOOK_ORIG = bytes.fromhex('8D 45 E4 BA 9C 76 48 00')
URL_HOOK_NEXT_VA = 0x47A1D3
URL_RESP_CONT_VA = 0x47A399        # 命中/兜底后共同的继续点
LSTRASG_FUNC = 0x403C58            # Delphi 字符串赋值
FALLBACK_STR = 0x48769C            # 兜底常量「\0\s0……\w8\w8\s4ん？」（翻译表改写）

CAVE2_OFF = 0x20                   # 第二桩在 .cave 内的偏移
PREFIX_OFF = 0x3A00                # 响应前缀常量（见 URL_RESP_PREFIX；0x100 原位只够 16B）
BUF_DATA_OFF = 0x200               # 响应缓冲（数据指针）
# RSS 锚点响应前缀——社区标准模式（emily4 等成熟人格的 OnAnchorSelect 处理）：
#   \C                       → 追记到"当前气球"（不开始新一轮 talk → 气泡不关！）
#   \![set,choicetimeout,-1] → 关掉选择超时
#   \![set,balloontimeout,-1]→ 关掉气球超时
#   \j[                      → 打开浏览器（后接 URL，桩里再补 ']'）
URL_RESP_PREFIX = rb'\C\![set,choicetimeout,-1]\![set,balloontimeout,-1]\j['

# ---- 游戏/双击相关补丁的 .cave 数据区 ----
# .cave 布局（0x2000 字节追加节；改动后应做区间重叠检查）：
#   0x000 链接化桩(26) | 0x020 RSS桩(134) | 0x3A00 响应前缀 "\C…\j["（54B，
#   挪到 IME 字符串(止于 0x39E5)与打字闸门桩(0x3B00)之间的空闲段；0x100 原位过小）
#   0x110 类名串 + 空提交脚本(dstr @0x140/数据@0x148)
#   0x1F8-0x3FF 链接桩2 响应缓冲（string 头 + 数据，链接文档）
#   0x380 视力双击小段 | 0x400 关窗体辅助桩(0x400-0x486) | 0x500 双击判定桩B | 0x600 响应监控桩A
#   0x940 标志组(FLAG/MARK/PENDING/GAMELEFT/SWALLOW/EYEBUSY)
#   0x95C 菜单缓存头(rc@0x95C/长度@0x960/数据@0x964，cap 0x6E0)
#   0x1050 CLOSE_CMD(123) | 0x10D0 CloseQuery跳板 | 0x10F8 PENDING前置拼接缓冲(rc/len@0x10FC/数据@0x1100，
#          数据可达 0x1F7B，故 0x1F7C 之后才空)
#   高分屏包装桩：request@0x170（空闲段 0x167-0x1F7）| load@0x1F7C（尾段）| 数据@0xB0
#   （PSET/字符串，占用 0xA7-0xFF 空闲段）
#   拖动包装桩 @0x2000（调用点 6 处 SC_DRAGMOVE）
#   IME 层 0x2810-0x39E5 | 打字定向屏蔽桩 @0x3B00（止于 0x3CDE；测试副本入口区 0x3D00-0x3DF0 保留）
#   重力语变换桩 @0x4000（对话/窗口两个调用点共用）
# .cave 节大小 0x6000（0x2000 → 0x2200 拖动 → 0x4000 IME → 0x6000 重力语桩）。

CAVE_B_OFF = 0x500                 # 桩B：双击判定（读请求 Status + 游戏/输入框标志）
CAVE_A_OFF = 0x600                 # 桩A：响应监控（标志维护/退出收尾/菜单缓存）
FLAG_OFF = 0x940                   # 输入框标志（dword：1=有 SSP 输入框打开）
MARK_OFF = 0x944                   # 视力游戏标记（1=已进入视力游戏）
PENDING_OFF = 0x948                # 退出待处理字节（1=本次退出响应前置 CLOSE_CMD）
GAMELEFT_OFF = 0x94C               # 游戏已退出字节（1=吞掉残留游戏事件响应）
EYEBUSY_OFF = 0x94E                # 视力题已弹出字节（1=C图窗+输入框在 → 双击禁用）
CACHE_OFF = 0x960                  # 游戏菜单缓存（rc@0x95C / 长度@0x960 / 数据@0x964）
CLOSE_CMD_OFF = 0x1050             # 常量：退出时前置到响应的收尾命令
EYE_CANCEL_STR_OFF = 0x140         # 空提交脚本 dstr（视力取消输入框时回它）
EYE_CANCEL_DATA_OFF = 0x148        # 上面的数据指针（桩A 里 LStrAsg 用）
EYE_DC_STUB_OFF = 0x380            # 视力未弹框双击小段：自设 ebx 调关窗辅助桩
                                   # （辅助桩占 0x400-0x486，改布局时勿重叠）
# 说明：问答游戏的答题框是 SSP 的 inputbox；打字游戏的框是 DLL 自己的窗体
#      （由辅助桩 WM_CLOSE 关闭，不靠这些命令）。多关几个箱型无副作用。
#      `leave,passivemode` 追加在尾部：问答/打字退出时 DLL 自身响应本来就以
#      该命令开头（等价冗余）；视力"未弹框双击"路径靠它退被动。
CLOSE_CMD = (b'\\![quicksection,false]'
             b'\\![close,communicatebox]'
             b'\\![close,teachbox]'
             b'\\![close,inputbox,__SYSTEM_ALL_INPUT__]'
             b'\\![leave,passivemode]')
PBUF_LEN_OFF = 0x10FC              # PENDING 前置拼接缓冲：长度（rc 在 -4，数据在 +4）
PBUF_DATA_OFF = 0x1100             # 数据（cap 0xE00）
TYPING_CLOSEQ_OFF = 0x6E200        # formCloseQuery：CanClose := 窗体.已提交标志[Self+0x321]
TYPING_CLOSEQ_ORIG = bytes.fromhex('8A 80 21 03 00 00 88 01 C3')
TYPING_CLOSEQ_VA = 0x46EE00
CAVE_Q_OFF = 0x10D0                # CloseQuery 跳板：GAMELEFT 时放行关闭（配合 WM_CLOSE）

# 打字游戏期间的定向屏蔽（只保护"要打文本"与"玩家输入"，其余照常转换）：
#   打字框文字经 SSTP 提交时会被 SSP 的脚本翻译过一遍（= DLL 的 ConvertAll C），
#   题面显示也被同样转换，而判定按原文比较 → 开着任一模式时"怎么打都不对"。
#   处理：在 OnTranslate 处理的转换调用处（0x47239D）加闸门——
#   打字游戏进行中（[0x4ADCDC]≠0；进入游戏置1、退出置0，DLL 官方状态）时：
#     a) 文本含 '\![raise,OnTypinggameInput,'（玩家输入脚本）→ 整体原样输出；
#     b) 文本含当前"要打文本" P=[0x4B29F8] → 仅 P 保持原文，
#        前后两段照常转换后拼回 out = C(前缀) + P + C(后缀)；
#     c) 其余（题面提示语、结算语…）→ 照常 ConvertAll。
#   非打字游戏时：完全不变（照常 ConvertAll）。
TYPING_GATE_HOOK_VA = 0x47239D
TYPING_GATE_HOOK_ORIG = bytes.fromhex('E8 3A E5 FF FF')     # call 0x4708DC
TYPING_GATE_STUB_OFF = 0x3B00      # 桩位置（cave 尾部空闲区：IME 字符串止于 0x39E5）
TYPING_FLAG_VA = 0x4ADCDC          # 打字游戏进行中标志
CONVERT_ALL_VA = 0x4708DC          # 四模式转换（OnTranslate / 对话框都用它）
LSTRASG_VA = 0x403C14              # Delphi 字符串赋值（var ← 值）
LSTRCAT_VA = 0x403E48              # Delphi 字符串拼接（var += 值）
SETLEN_VA = 0x404174               # Delphi SetLength（var, n；含写时复制）
COPY_PREFIX_VA = 0x4187B4          # out := Copy(s, 1, n)（DLL 内部小工具，按字节）
INPUT_NEEDLE_VA = 0x46EBFC         # '\![raise,OnTypinggameInput,' ANSI 常量
PHRASE_VAR_VA = 0x4B29F8           # 当前"要打文本" ANSI 串全局

# 重力语中文化（对话 0x418D21 / 菜单窗口 0x46FC03 两处 call 0x417048 → 新桩）：
#   原版核心三步 = 转义(0x417048) + CharLowerBuffA(0x408358) + 解码(0x4171A0)：
#   转义把双字节对的尾字节临时裸露成 ASCII，小写步把 A-Z 压成 a-z
#   （尾字节 0x41-0x5A 的字符因此 +0x20 变字），解码还原 %XX。
#   GBK 汉字尾字节为 A1-FE，永不命中 → 中文版形同失效（只剩 ASCII 小写）。
#   本桩替换"转义"一环（两个调用点；百度搜索的 URL 编码调用点 0x476548 保持原样）：
#     - ASCII A-Z → a-z；
#     - 表驱动逐字复刻：把"原版会变的 SJIS 字符（尾字节 0x41-0x5A → +0x20）"里
#       凡在 GBK 也存在的逐字映射成表（假名/汉字/符号共约 1044 条，见
#       _build_gravity_map；右→影、宇→映、。→｜、！→（、リンゴ→リンピ……），
#       命中则换、未命中/不在 GBK 一律原样；
#     - [ ] 区逐字节原样（脚本指令安全；并补原版"方括号内 %xx 被解码吃掉"的洞）；
#     - '%' → %25（保住环境变量标签 %month/%* 与字面百分号；两路径一致）。
#   输出全部为小写 %xx 转义：对后续"小写+解码"严格幂等/可逆（不依赖 CharLowerBuffA
#   的 DBCS 行为），解码后即最终文本。
GRAVITY_ESCAPE_VA = 0x417048       # 原转义函数（搜索 URL 编码共用一个调用点，勿动本体）
GRAVITY_HOOK_DLG_VA = 0x418D21     # 对话管线内 call 0x417048
GRAVITY_HOOK_WIN_VA = 0x46FC03     # Tjtogvform.editChange 内 call 0x417048
GRAVITY_STUB_OFF = 0x4000          # 新桩（cave 扩至 0x6000 后的空闲段）
GRAVITY_TABLE_OFF = 0x4800         # 逐字映射表（4B/条 + 0000 终结；约 0x1052 字节）

RESP_DONE_VA = 0x47A399            # 事件响应汇总点（所有响应都经过）
RESP_DONE_ORIG = bytes.fromhex('83 7D E4 00 75 12')     # cmp [ebp-1C],0 / jne
RESP_FALL_VA = 0x47A39F            # cmp 为 0 的路径（204）
RESP_NZ_VA = 0x47A3B1              # cmp 非 0 的路径（200）

DC_ENTRY_VA = 0x4782BD             # OnMouseDoubleClick 处理入口
DC_ENTRY_ORIG = bytes.fromhex('8D 45 E4 E8 FB B8 F8 FF')  # lea eax,[ebp-1C] / call clr
DC_CONT_VA = 0x4782C5              # 原样继续点
DC_DONE_VA = 0x478848              # 处理完成的共同出口（清响应 → 204）


def _build_url_stub(rva):
    """OnAnchorSelect 桩：Ref0=[ebp-0x1c]。http 开头 ->
    拼接 URL_RESP_PREFIX（\\C 追记 + 关超时 + \\j[）+ Ref0 + "]" 到节内缓冲并返回；
    否则原兜底。\\C = 追记到当前气球，新闻气泡不会被新一轮 talk 顶掉。"""
    b = bytearray()
    va = lambda i: rva + i
    fb_target = va(112)          # .fb 标签（见下面的偏移注释）

    def rel32(target, at):
        return struct.pack('<i', target - va(at + 4))

    b += b'\x53\x56\x57'                              # 0: push ebx/esi/edi
    b += b'\xE8\x00\x00\x00\x00'                      # 3: call $+5
    b += b'\x5B'                                      # 8: pop ebx (= va(8))
    b += b'\x8B\x7D\xE4'                              # 9: mov edi,[ebp-0x1c]
    b += b'\x85\xFF'                                  # 12: test edi,edi
    b += b'\x0F\x84' + rel32(fb_target, 16)           # 14: jz .fb
    b += b'\x81\x3F\x68\x74\x74\x70'                  # 20: cmp dword [edi],'http'
    b += b'\x0F\x85' + rel32(fb_target, 28)           # 26: jne .fb
    b += b'\x8B\x4F\xFC'                              # 32: mov ecx,[edi-4]
    b += b'\x81\xF9\x00\x01\x00\x00'                  # 35: cmp ecx,0x100
    b += b'\x0F\x87' + rel32(fb_target, 43)           # 41: ja .fb
    # 注意：ebx = 本桩内 pop 的地址 = 桩VA+8 = 节VA + (CAVE2_OFF+8)，
    # 所以指向节内偏移时要减去 (CAVE2_OFF + 8)。
    assert len(URL_RESP_PREFIX) == 54, len(URL_RESP_PREFIX)
    b += b'\x8D\x93' + struct.pack('<i', BUF_DATA_OFF - CAVE2_OFF - 16)  # 47: lea edx,[ebx+buf-8]
    b += b'\xC7\x02\xFF\xFF\xFF\xFF'                  # 53: mov dword [edx],-1
    b += b'\x8D\x41' + bytes([len(URL_RESP_PREFIX) + 1])   # 59: lea eax,[ecx+前缀长+1]（含 ']'）
    b += b'\x89\x42\x04'                              # 62: mov [edx+4],eax
    b += b'\x8D\xB3' + struct.pack('<i', PREFIX_OFF - CAVE2_OFF - 8)  # 65: lea esi,[ebx+prefix]
    b += b'\x8D\xBA\x08\x00\x00\x00'                  # 71: lea edi,[edx+8]
    b += b'\x51'                                      # 77: push ecx
    b += b'\xB9' + struct.pack('<I', len(URL_RESP_PREFIX))   # 78: mov ecx,前缀长
    b += b'\xF3\xA4'                                  # 83: rep movsb
    b += b'\x59'                                      # 85: pop ecx
    b += b'\x8B\x75\xE4'                              # 86: mov esi,[ebp-0x1c]
    b += b'\xF3\xA4'                                  # 89: rep movsb
    b += b'\xC6\x07\x5D'                              # 91: mov byte [edi],']'
    b += b'\xC6\x47\x01\x00'                          # 94: mov byte [edi+1],0
    b += b'\x83\xC2\x08'                              # 98: add edx,8
    b += b'\x89\x55\xE4'                              # 101: mov [ebp-0x1c],edx
    b += b'\x5F\x5E\x5B'                              # 104: pop edi/esi/ebx
    b += b'\xE9' + rel32(URL_RESP_CONT_VA, 108)       # 107: jmp cont
    assert len(b) == 112, len(b)
    # .fb（offset 112）
    b += b'\x8D\x45\xE4'                              # 112: lea eax,[ebp-0x1c]
    b += b'\x8D\x93' + struct.pack('<i', FALLBACK_STR - va(8))   # 115: lea edx,[ebx+fb]
    b += b'\xE8' + rel32(LSTRASG_FUNC, 122)           # 121: call LStrAsg
    b += b'\x5F\x5E\x5B'                              # 126: pop edi/esi/ebx
    b += b'\xE9' + rel32(URL_RESP_CONT_VA, 130)       # 129: jmp cont
    return bytes(b)


def _build_resp_monitor_stub(rva):
    """响应监控桩（挂在 0x47A399，request() 帧内，EBP 有效）：

       【事件分流】读事件名（[ebp-0x4c]，SEH 保护）：
         - OnUs*（取消 SSP 输入框）：EYEBUSY=1（视力输入框被取消）→ 按"提交
           空值"处理：响应 := \\![raise,OnEyesightgameInput,]，DLL 走与超时/
           空输入相同的收尾（反馈+leave,passivemode+关视力窗）；否则只清 FLAG。
         - OnGo*（搜索提交）及各游戏事件：清 FLAG。
         - OnQu* / OnTy*：Leave → PENDING+GAMELEFT+关窗体（见 .setpend）；
           Enter → 清 GAMELEFT；其余（进度事件）→ 见 GAMELEFT。
         - OnEy*（视力）：Next → MARK=1+EYEBUSY=1（题已弹框）；Input →
           MARK=0+EYEBUSY=0（本局结束）；其余（Enter 等）→ MARK=1+EYEBUSY=0
           （未弹框，双击走"置 PENDING + 关窗小段 + 原版出主菜单"）。

       【固定动作】
         - PENDING：把 CLOSE_CMD 前置到本次响应（仅一次，关普通输入框）；
         - GAMELEFT+SWALLOW：游戏已退出后，游戏自己的进度事件（提交/下一题/
           超时）响应整条清空（→204），断掉游戏链；Enter 时清 GAMELEFT；
         - 缓存扫描：响应含 \\![*]\\q[ → 整条复制到菜单缓存（供双击重放）；
         - 输入框扫描：响应含 \\![open,inputbox → FLAG=1；
         - 最后复刻被覆盖的 cmp/jne，跳回 0x47A3B1（响应非空）/0x47A39F（空）。

       【防崩】[ebp-0x4c] 对没有 ID 的请求（SSP 协议探测 GET Version 等）是
       未初始化栈残留，直接解引用会崩（曾导致 SSP 降级 2.2、ghost 打不开）。
       这里给整段读取装了 SEH 保护：异常时处理器把 Eip 改到收尾处，安全跳过。

       【实现】分支全部用标签 + 回填（fixups），指令长度变化不再手算偏移；
       分支距离超界会在回填时 assert 报错。
    """
    b = bytearray()
    va = lambda i: rva + i
    labels = {}
    fixups = []

    def label(name):
        labels[name] = len(b)

    def jcc8(op, name):
        b.append(op)
        fixups.append(('rel8', len(b), name))
        b.append(0)

    def jmp8(name):
        jcc8(0xEB, name)

    def jcc32(op0, op1, name):
        b.append(op0)
        b.append(op1)
        fixups.append(('rel32', len(b), name))
        b.extend(b'\x00\x00\x00\x00')

    def jmp32(name):
        b.append(0xE9)
        fixups.append(('rel32', len(b), name))
        b.extend(b'\x00\x00\x00\x00')

    def call_abs(target_va):
        b.append(0xE8)
        b.extend(struct.pack('<i', target_va - va(len(b) + 4)))

    disp = FLAG_OFF - (CAVE_A_OFF + 0x0B)
    pend = PENDING_OFF - (CAVE_A_OFF + 0x0B)   # GAMELEFT 就在 pend+4
    eyebusy = EYEBUSY_OFF - (CAVE_A_OFF + 0x0B)   # 视力输入框已弹出标志

    # --- 序言 + SEH 安装（handler 地址稍后回填）---
    b += b'\x50\x51\x52\x56\x57\x53'                    # push eax/ecx/edx/esi/edi/ebx
    b += b'\xE8\x00\x00\x00\x00'                        # call $+5
    b += b'\x5B'                                        # pop ebx（= va(0x0B)）
    lea_handler_disp = len(b) + 2                       # lea eax,[ebx+handler] 的 disp32 位置
    b += b'\x8D\x83\x00\x00\x00\x00'                    # （回填）
    b += b'\x50'                                        # push eax（handler）
    b += b'\x64\xFF\x35\x00\x00\x00\x00'                # push dword [fs:0]
    b += b'\x64\x89\x25\x00\x00\x00\x00'                # mov dword [fs:0],esp
    # --- 事件名判定（受保护）---
    b += b'\x8B\x75\xB4'                                # mov esi,[ebp-0x4c]（事件名）
    b += b'\x85\xF6'                                    # test esi,esi
    jcc32(0x0F, 0x84, 'after')                          # jz .after（空名→跳过）
    b += b'\x81\x3E\x4F\x6E\x55\x73'                    # cmp [esi],'OnUs'
    jcc8(0x74, 'chkus')                                 # je .chkus（取消输入框要按游戏状态分流）
    b += b'\x81\x3E\x4F\x6E\x47\x6F'                    # cmp [esi],'OnGo'
    jcc8(0x74, 'clrf')                                  # je .clrf
    b += b'\x81\x3E\x4F\x6E\x51\x75'                    # cmp [esi],'OnQu'
    jcc32(0x0F, 0x84, 'chkq')                           # je .chkq（距离远，须 rel32）
    b += b'\x81\x3E\x4F\x6E\x54\x79'                    # cmp [esi],'OnTy'
    jcc32(0x0F, 0x84, 'chkt')                           # je .chkt（距离远，须 rel32）
    b += b'\x81\x3E\x4F\x6E\x45\x79'                    # cmp [esi],'OnEy'
    jcc32(0x0F, 0x84, 'chkey')                          # je .chkey（视力分流，距离远须 rel32）
    jmp32('after')                                      # 都不是→跳过（距离远，须 rel32）
    # .chkus：OnUs*（OnUserInput 取消输入框）按游戏状态分流：
    #   视力输入框被取消（EYEBUSY=1）→ 按"提交空值"处理：响应 :=
    #   `\![raise,OnEyesightgameInput,]`，SSP 触发该事件后 DLL 走和超时/空输入
    #   一样的收尾（反馈 + leave,passivemode + 关视力窗）。原版 materia 输入框
    #   无法关闭，所以 DLL 对取消只回 204——这里替它补上。
    #   其余（搜索框等）→ 普通清 FLAG。
    label('chkus')
    b += b'\x80\xBB' + struct.pack('<i', eyebusy)       # cmp byte [ebx+eyebusy],0
    b += b'\x00'
    jcc32(0x0F, 0x84, 'clrf')                           # je .clrf（距离远，须 rel32）
    b += b'\xC6\x83' + struct.pack('<i', eyebusy) + b'\x00'  # mov byte [ebx+eyebusy],0
    b += b'\x8D\x93' + struct.pack('<i', disp)          # lea edx,[ebx+flag]
    b += b'\xC7\x02\x00\x00\x00\x00'                    # mov dword [edx],0
    b += b'\x8D\x45\xE4'                                # lea eax,[ebp-0x1c]（响应）
    b += b'\x8D\x93' + struct.pack('<i', EYE_CANCEL_DATA_OFF - (CAVE_A_OFF + 0x0B))  # lea edx,空提交脚本
    call_abs(0x403C58)                                  # call LStrAsg（响应 := \![raise,OnEyesightgameInput,]）
    jmp32('after')
    # .clrf：输入框标志清零
    label('clrf')
    b += b'\x8D\x93' + struct.pack('<i', disp)          # lea edx,[ebx+flag]
    b += b'\xC7\x02\x00\x00\x00\x00'                    # mov dword [edx],0
    jmp32('after')                                      # 距离远，须 rel32
    # .clrb：标志清零 + 游戏标记清零
    label('clrb')
    b += b'\x8D\x93' + struct.pack('<i', disp)          # lea edx,[ebx+flag]
    b += b'\xC7\x02\x00\x00\x00\x00'                    # mov dword [edx],0
    b += b'\x8D\x93' + struct.pack('<i', disp + 4)      # lea edx,[ebx+flag+4]（mark）
    b += b'\xC7\x02\x00\x00\x00\x00'                    # mov dword [edx],0
    jmp32('after')                                      # 距离远，须 rel32
    # .chkey：OnEy*（视力）分流（事件名第 15 字符起在 [esi+14]）：
    #   默认：FLAG=0、MARK=1（视力进行中）、EYEBUSY=0（未弹框 → 双击交还原版）；
    #   Input → MARK=0（本局结束）；Next → EYEBUSY=1（题已弹框 → 双击禁用）。
    label('chkey')
    b += b'\x8D\x93' + struct.pack('<i', disp)          # lea edx,[ebx+flag]
    b += b'\xC7\x02\x00\x00\x00\x00'                    # FLAG=0
    b += b'\x8D\x93' + struct.pack('<i', disp + 4)      # lea edx,[ebx+mark]
    b += b'\xC7\x02\x01\x00\x00\x00'                    # MARK=1
    b += b'\xC6\x83' + struct.pack('<i', eyebusy) + b'\x00'  # EYEBUSY=0
    b += b'\x81\x7E\x0E\x49\x6E\x70\x75'                # cmp [esi+14],'Inpu'
    jcc8(0x75, 'chknext')                               # jne .chknext
    b += b'\x8D\x93' + struct.pack('<i', disp + 4)      # lea edx,[ebx+mark]
    b += b'\xC7\x02\x00\x00\x00\x00'                    # MARK=0
    jmp32('after')
    label('chknext')
    b += b'\x81\x7E\x0E\x4E\x65\x78\x74'                # cmp [esi+14],'Next'
    jcc32(0x0F, 0x85, 'after')                          # jne .after（距离远，rel32）
    b += b'\xC6\x83' + struct.pack('<i', eyebusy) + b'\x01'  # EYEBUSY=1
    jmp32('after')
    # .chkq：OnQuiz* 分流（Leave→置位；Enter→清 GAMELEFT；其余→.gamem）
    label('chkq')
    b += b'\x81\x7E\x04\x69\x7A\x4C\x65'                # cmp [esi+4],'izLe'（OnQuizLeave）
    jcc32(0x0F, 0x84, 'setpend')                          # je .setpend（距离远，rel32）
    b += b'\x81\x7E\x04\x69\x7A\x45\x6E'                # cmp [esi+4],'izEn'（OnQuizEnter）
    jcc32(0x0F, 0x84, 'clrgl')                            # je .clrgl（距离远，rel32）（清 GAMELEFT）
    jmp32('gamem')                                      # 其余 OnQuiz* → .gamem
    # .chkt：OnTypinggame* 分流
    label('chkt')
    b += b'\x81\x7E\x0C\x4C\x65\x61\x76'                # cmp [esi+12],'Leav'（OnTypinggameLeave）
    jcc32(0x0F, 0x84, 'setpend')                          # je .setpend（距离远，rel32）
    b += b'\x81\x7E\x0C\x45\x6E\x74\x65'                # cmp [esi+12],'Ente'（OnTypinggameEnter）
    jcc32(0x0F, 0x84, 'clrgl')                          # je .clrgl（距离远，rel32）
    jmp32('gamem')                                      # 其余 OnTypinggame* → .gamem
    # .gamem：游戏已退出（GAMELEFT）期间，游戏自己的事件（进度/下一题/超时等）
    #         继续跑的话会不断出新题、生成新输入框——本次响应标记为待吞
    label('gamem')
    b += b'\x80\xBB' + struct.pack('<i', pend + 4) + b'\x00'  # cmp byte [ebx+gameleft],0
    jcc32(0x0F, 0x84, 'clrb')                           # je .clrb（没退出过）
    b += b'\xC6\x83' + struct.pack('<i', pend + 5) + b'\x01'  # mov byte [ebx+swallow],1
    jmp32('clrb')                                       # 再进 .clrb 清标志/标记
    # .setpend：置 PENDING + GAMELEFT

    label('setpend')
    b += b'\x8D\x93' + struct.pack('<i', pend)          # lea edx,[ebx+pending]
    b += b'\xC6\x02\x01'                                # mov byte [edx],1（PENDING）
    b += b'\xC6\x42\x04\x01'                            # mov byte [edx+4],1（GAMELEFT）
    # 立即关游戏窗体：辅助桩按类名找 Ttypinggameform/Teyesightform/Tcountdownform 并投递
    # WM_CLOSE（异步）。打字框的 CloseQuery 由 .cave+0x10D0 的桩在 GAMELEFT
    # 时放行；视力窗本来就无拦截。全程无键盘消息，故没有编辑框回车提示音。
    call_abs(rva - 0x200)                               # 辅助桩在 cave+0x400（stubA 起点-0x200）
    jmp32('clrb')
    # .clrgl：清 GAMELEFT（回到游戏）
    label('clrgl')
    b += b'\x8D\x93' + struct.pack('<i', pend + 4)      # lea edx,[ebx+pending+4]
    b += b'\xC6\x02\x00'                                # mov byte [edx],0
    jmp32('clrb')
    # --- SEH 收尾（异常处理器也回到这里）---
    label('after')
    b += b'\x8B\x04\x24'                                # mov eax,[esp]
    b += b'\x64\x89\x05\x00\x00\x00\x00'                # mov dword [fs:0],eax
    b += b'\x83\xC4\x08'                                # add esp,8
    # --- PENDING：退出事件时给响应前置收尾命令（仅此一次）---
    b += b'\x80\xBB' + struct.pack('<i', pend) + b'\x00'  # cmp byte [ebx+pending],0
    jcc8(0x74, 'swallow')                               # je .swallow（无事可做）
    b += b'\xC6\x83' + struct.pack('<i', pend) + b'\x00'  # mov byte [ebx+pending],0
    b += b'\x8B\x75\xE4'                                # mov esi,[ebp-0x1c]（响应）
    b += b'\x85\xF6'                                    # test esi,esi
    jcc8(0x74, 'swallow')                               # jz .swallow
    b += b'\x8B\x4E\xFC'                                # mov ecx,[esi-4]（响应长度）
    b += b'\x81\xF9\x00\x0E\x00\x00'                    # cmp ecx,0xE00
    jcc8(0x76, 'oklen')                                 # jbe .oklen
    b += b'\xB9\x00\x0E\x00\x00'                        # mov ecx,0xE00（超长截断）
    label('oklen')
    b += b'\x8D\x41' + bytes([len(CLOSE_CMD)])          # lea eax,[ecx+命令长]
    b += b'\x8D\x93' + struct.pack('<i', PBUF_LEN_OFF - (CAVE_A_OFF + 0x0B))   # lea edx,[ebx+pbuflen]
    b += b'\x89\x02'                                    # mov [edx],eax（总长度）
    b += b'\xC7\x42\xFC\xFF\xFF\xFF\xFF'                # mov dword [edx-4],-1（refcount）
    b += b'\x8D\xBB' + struct.pack('<i', PBUF_DATA_OFF - (CAVE_A_OFF + 0x0B))  # lea edi,[ebx+pbufdata]
    b += b'\x51'                                        # push ecx（响应长度）
    b += b'\x8D\xB3' + struct.pack('<i', CLOSE_CMD_OFF - (CAVE_A_OFF + 0x0B))  # lea esi,[ebx+cmd]
    b += b'\xB9' + struct.pack('<I', len(CLOSE_CMD))    # mov ecx,命令长
    b += b'\xF3\xA4'                                    # rep movsb（先写命令）
    b += b'\x8B\x75\xE4'                                # mov esi,[ebp-0x1c]
    b += b'\x59'                                        # pop ecx
    b += b'\xF3\xA4'                                    # rep movsb（再写原响应）
    b += b'\x8D\x83' + struct.pack('<i', PBUF_DATA_OFF - (CAVE_A_OFF + 0x0B))  # lea eax,[ebx+pbufdata]
    b += b'\x89\x45\xE4'                                # mov [ebp-0x1c],eax（替换响应）
    # --- 游戏遗留：吞掉本次响应（GAMELEFT 期间游戏自己的事件，见 .gamem）---
    label('swallow')
    b += b'\x80\xBB' + struct.pack('<i', pend + 5) + b'\x00'  # cmp byte [ebx+swallow],0
    jcc8(0x74, 'scan')                                  # je .scan（本次响应正常处理）
    b += b'\xC6\x83' + struct.pack('<i', pend + 5) + b'\x00'  # mov byte [ebx+swallow],0
    b += b'\x8D\x45\xE4'                                # lea eax,[ebp-0x1c]
    call_abs(0x403BC0)                                  # call LStrClr（清响应 → 204）
    jmp32('done')                                       # 跳过缓存/标志扫描
    # --- 缓存扫描：\![*]\q[ → 复制整段响应到菜单缓存（cave+CACHE_OFF）---
    label('scan')
    b += b'\x8B\x75\xE4'                                # mov esi,[ebp-0x1c]
    b += b'\x85\xF6'                                    # test esi,esi
    jcc8(0x74, 'flag')                                  # jz .flag
    b += b'\x8B\x4E\xFC'                                # mov ecx,[esi-4]
    b += b'\x83\xF9\x08'                                # cmp ecx,8
    jcc8(0x72, 'flag')                                  # jb .flag
    b += b'\x8B\xD1'                                    # mov edx,ecx
    b += b'\x83\xEA\x07'                                # sub edx,7（剩余窗口）
    b += b'\x8B\xFE'                                    # mov edi,esi
    label('cs')
    b += b'\x81\x3F\x5C\x21\x5B\x2A'                    # cmp [edi],'\\![*'
    jcc8(0x75, 'cn')                                    # jne .cn
    b += b'\x81\x7F\x04\x5D\x5C\x71\x5B'                # cmp [edi+4],']\\q['
    jcc8(0x74, 'copy')                                  # je .copy
    label('cn')
    b += b'\x47'                                        # inc edi
    b += b'\x4A'                                        # dec edx
    jcc8(0x75, 'cs')                                    # jnz .cs
    jmp8('flag')
    label('copy')
    b += b'\x81\xF9\xE0\x06\x00\x00'                    # cmp ecx,0x6E0
    jcc8(0x76, 'cl')                                    # jbe .cl
    b += b'\xB9\xE0\x06\x00\x00'                        # mov ecx,0x6E0
    label('cl')
    b += b'\x8D\x93' + struct.pack('<i', CACHE_OFF - (CAVE_A_OFF + 0x0B))  # lea edx,[ebx+cache]
    b += b'\xC7\x42\xFC\xFF\xFF\xFF\xFF'                # mov dword [edx-4],-1（refcount=-1）
    b += b'\x89\x0A'                                    # mov [edx],ecx（长度）
    b += b'\x8D\x7A\x04'                                # lea edi,[edx+4]（数据）
    b += b'\xF3\xA4'                                    # rep movsb
    # --- 响应扫描：\![open,inputbox（前缀）→ 置标志 ---
    label('flag')
    b += b'\x8B\x75\xE4'                                # mov esi,[ebp-0x1c]
    b += b'\x85\xF6'                                    # test esi,esi
    jcc8(0x74, 'done')                                  # jz .done
    b += b'\x8B\x4E\xFC'                                # mov ecx,[esi-4]
    b += b'\x83\xF9\x10'                                # cmp ecx,16
    jcc8(0x72, 'done')                                  # jb .done
    b += b'\x8B\xD1'                                    # mov edx,ecx
    b += b'\x83\xEA\x0F'                                # sub edx,15
    b += b'\x8B\xFE'                                    # mov edi,esi
    label('rscan')
    b += b'\x81\x3F\x5C\x21\x5B\x6F'                    # cmp [edi],'\\![o'
    jcc8(0x75, 'rnext')                                 # jne .rnext
    b += b'\x81\x7F\x04\x70\x65\x6E\x2C'                # cmp [edi+4],'pen,'
    jcc8(0x75, 'rnext')                                 # jne .rnext
    b += b'\x81\x7F\x08\x69\x6E\x70\x75'                # cmp [edi+8],'inpu'
    jcc8(0x75, 'rnext')                                 # jne .rnext
    b += b'\x81\x7F\x0C\x74\x62\x6F\x78'                # cmp [edi+12],'tbox'
    jcc8(0x74, 'set')                                   # je .set
    label('rnext')
    b += b'\x47'                                        # inc edi
    b += b'\x4A'                                        # dec edx
    jcc8(0x75, 'rscan')                                 # jnz .rscan
    jmp32('done')
    label('set')
    b += b'\x8D\x93' + struct.pack('<i', disp)          # lea edx,[ebx+flag]
    b += b'\xC7\x02\x01\x00\x00\x00'                    # mov dword [edx],1
    label('done')
    b += b'\x5B\x5F\x5E\x5A\x59\x58'                    # pop ebx/edi/esi/edx/ecx/eax
    b += b'\x83\x7D\xE4\x00'                            # cmp dword [ebp-0x1c],0
    b.append(0x0F)
    b.append(0x85)
    nz_pos = len(b)
    b += b'\x00\x00\x00\x00'                            # jne 0x47A3B1（回填）
    b.append(0xE9)
    fall_pos = len(b)
    b += b'\x00\x00\x00\x00'                            # jmp 0x47A39F（回填）
    # --- SEH 处理器：CONTEXT.Eip = .after，返回“继续执行”---
    label('handler')
    b += b'\xE8\x00\x00\x00\x00'                        # call $+5
    pop_off = len(b)
    b += b'\x58'                                        # pop eax（= va(pop_off)）
    b += b'\x05' + struct.pack('<i', labels['after'] - pop_off)  # add eax, .after-va(pop_off)
    b += b'\x8B\x4C\x24\x08'                            # mov ecx,[esp+8]（CONTEXT*）
    b += b'\x89\x81\xB8\x00\x00\x00'                    # mov [ecx+0xB8],eax（Eip）
    b += b'\x31\xC0'                                    # xor eax,eax（ContinueExecution）
    b += b'\xC3'                                        # ret
    # --- 回填 ---
    struct.pack_into('<i', b, lea_handler_disp, labels['handler'] - 0x0B)
    struct.pack_into('<i', b, nz_pos, RESP_NZ_VA - va(nz_pos + 4))
    struct.pack_into('<i', b, fall_pos, RESP_FALL_VA - va(fall_pos + 4))
    for kind, pos, name in fixups:
        target = labels[name]
        if kind == 'rel8':
            v = target - (pos + 1)
            assert -128 <= v <= 127, (name, hex(target), hex(pos), v)
            struct.pack_into('<b', b, pos, v)
        else:
            struct.pack_into('<i', b, pos, target - (pos + 4))
    assert len(b) == 0x2E3, hex(len(b))
    return bytes(b)


def _build_typing_closeq_stub(cave_va):
    """formCloseQuery 跳板（.cave+0x10D0，挂在 0x46EE00）。

    原逻辑：CanClose := 窗体.已提交标志（[Self+0x321]）——只有回车提交路径和
    游戏超时会置位。GAMELEFT=1（已从游戏退出）时直接放行关闭，配合辅助桩发的
    WM_CLOSE，让 DLL 走自己的 Close→FormClose(caFree) 路径关框：全程没有键盘
    消息，也就没有编辑框的回车"叮"（超时关闭同样走这条路，所以它是安静的）。
    GAMELEFT=0（正常游戏中）保持原逻辑不变。
    """
    b = bytearray()
    b += b'\xE8\x00\x00\x00\x00'                                  # 0: call $+5
    b += b'\x5A'                                                  # 5: pop edx
    disp = (cave_va + GAMELEFT_OFF) - (cave_va + CAVE_Q_OFF + 5)
    b += b'\x80\xBA' + struct.pack('<i', disp) + b'\x00'          # 6: cmp byte [edx+disp],0
    b += b'\x74\x04'                                              # 13: je .orig（→+19）
    b += b'\xC6\x01\x01'                                          # 15: mov byte [ecx],1（放行）
    b += b'\xC3'                                                  # 18: ret
    b += b'\x8A\x80\x21\x03\x00\x00'                              # 19: .orig: mov al,[eax+0x321]
    b += b'\x88\x01'                                              # 25: mov [ecx],al
    b += b'\xC3'                                                  # 27: ret
    assert len(b) == 28, hex(len(b))
    return bytes(b)


def _build_typing_gate_stub(stub_va):
    """打字游戏期间的定向闸门（.cave+0x3B00，挂在 0x47239D 的 ConvertAll 调用处）。

    进入时 EAX=待翻译文本（ANSI 串值）、EDX=输出串变量地址（与原 ConvertAll 调用一致）。
    - 未在打字游戏（[0x4ADCDC]==0）：照常 ConvertAll（完全兼容原行为）；
    - 进行中：
        a) text 含 '\\![raise,OnTypinggameInput,'（玩家输入脚本）→ 整体原样输出
           （连 \\![...] 语法一起保持），你打的字原样进判定；
        b) text 含 P=[0x4B29F8]（当前"要打文本"）→ out = C(前缀) + P + C(后缀)：
           前缀/后缀分开转换 → 转换规则永远看不到 P，天然免疫；
           前缀用 0x4187B4（Copy(s,1,n)）取，后缀用 SetLength 写时复制 + rep movsb
           去 P 得到；空串跳过转换（防 nil）。
        c) 其余（题面提示语、结算语…）→ 照常 ConvertAll（保留模式风味）。
    全程位置无关：先 call/pop/sub 求"运行时基址-链接基址"存 [ebp-0x2C]，
    标志/常量/全局地址一律 delta 修正；查找/搬移为内联字节循环（CP936 多字节安全）。
    字符串函数按 DLL 寄存器约定：EAX=&var / 值，EDX=值 / n（与原代码一致）。
    桩 ≤ 0x200 字节（测试副本的测试入口固定在 cave+0x3D00）。
    """
    b = bytearray()
    labels = {}
    fixups = []

    def op(*xs):
        b.extend(xs)

    def L(name):
        labels[name] = len(b)

    def rel(kind, name):
        fixups.append((kind, len(b), name))
        op(0, 0, 0, 0)

    def jmp_l(name):
        op(0xE9); rel('rel', name)

    def jcc(cc, name):
        op(0x0F, cc); rel('rel', name)

    def call_l(name):
        op(0xE8); rel('rel', name)

    def call_va(target):
        op(0xE8); fixups.append(('va', len(b), target)); op(0, 0, 0, 0)

    # --- 序：保存寄存器/立帧/清空局部串变量 ---
    op(0x53)                                      # push ebx
    op(0x56)                                      # push esi
    op(0x57)                                      # push edi
    op(0x55)                                      # push ebp
    op(0x8B, 0xEC)                                # mov ebp,esp
    op(0x81, 0xEC, 0x40, 0x00, 0x00, 0x00)        # sub esp,0x40
    op(0x89, 0x55, 0xFC)                          # mov [ebp-4],edx   ; &dst
    op(0x89, 0x45, 0xF8)                          # mov [ebp-8],eax   ; src
    # --- 运行时基址差（位置无关）：delta = 运行时stub - 链接stub，存 [ebp-0x2C] ---
    op(0xE8, 0x00, 0x00, 0x00, 0x00)              # call $+5
    base_ret_va = stub_va + len(b)                # 返回地址（pop eax 后 EAX 的值）
    op(0x58)                                      # pop eax
    op(0x2D); fixups.append(('imm32', len(b), base_ret_va)); op(0, 0, 0, 0)
                                                  # sub eax,base_ret_va → delta
    op(0x89, 0x45, 0xD4)                          # mov [ebp-0x2C],eax
    op(0x31, 0xC0)                                # xor eax,eax
    for d in (0xE4, 0xE0, 0xDC, 0xD8):            # -1C/-20/-24/-28 = 0
        op(0x89, 0x45, d)                         # mov [ebp-x],eax

    # --- 游戏进行中？ ---
    op(0x8B, 0x45, 0xD4)                          # mov eax,[ebp-0x2C]
    op(0x05); fixups.append(('imm32', len(b), TYPING_FLAG_VA)); op(0, 0, 0, 0)
                                                  # add eax,TYPING_FLAG_VA
    op(0x80, 0x38, 0x00)                          # cmp byte [eax],0
    jcc(0x84, 'plain')                            # je .plain

    # --- 输入脚本？ ---
    op(0x8B, 0x45, 0xF8)                          # mov eax,[ebp-8]
    op(0x8B, 0x55, 0xD4)                          # mov edx,[ebp-0x2C]
    op(0x81, 0xC2); fixups.append(('imm32', len(b), INPUT_NEEDLE_VA)); op(0, 0, 0, 0)
                                                  # add edx,needle地址
    call_l('find')
    op(0x83, 0xF8, 0xFF)                          # cmp eax,-1
    jcc(0x85, 'rawecho')                          # jne .rawecho

    # --- 取当前要打文本 P ---
    op(0x8B, 0x55, 0xD4)                          # mov edx,[ebp-0x2C]
    op(0x81, 0xC2); fixups.append(('imm32', len(b), PHRASE_VAR_VA)); op(0, 0, 0, 0)
                                                  # add edx,PHRASE_VAR_VA
    op(0x8B, 0x12)                                # mov edx,[edx]（全局的值）
    op(0x89, 0x55, 0xF4)                          # mov [ebp-0xC],edx
    op(0x85, 0xD2)                                # test edx,edx
    jcc(0x84, 'plain')                            # je .plain
    op(0x89, 0xD0)                                # mov eax,edx
    call_l('strlen')
    op(0x85, 0xC9)                                # test ecx,ecx
    jcc(0x84, 'plain')                            # je .plain（P 为空）
    op(0x89, 0x4D, 0xF0)                          # mov [ebp-0x10],ecx  ; plen
    op(0x8B, 0x45, 0xF8)                          # mov eax,[ebp-8]
    op(0x8B, 0x55, 0xF4)                          # mov edx,[ebp-0xC]
    call_l('find')
    op(0x83, 0xF8, 0xFF)                          # cmp eax,-1
    jcc(0x84, 'plain')                            # je .plain（未找到）
    op(0x89, 0x45, 0xEC)                          # mov [ebp-0x14],eax  ; o
    op(0x8B, 0x45, 0xF8)                          # mov eax,[ebp-8]
    call_l('strlen')
    op(0x89, 0x4D, 0xE8)                          # mov [ebp-0x18],ecx  ; srclen

    # --- 前缀：p := Copy(src,1,o)（DLL 小工具 0x4187B4） ---
    op(0x8B, 0x45, 0xF8)                          # mov eax,[ebp-8]
    op(0x8B, 0x55, 0xEC)                          # mov edx,[ebp-0x14]
    op(0x8D, 0x4D, 0xE4)                          # lea ecx,[ebp-0x1C]
    call_va(COPY_PREFIX_VA)

    # --- 后缀：s := src; 去 P; SetLength 收尾 ---
    op(0x8D, 0x45, 0xE0)                          # lea eax,[ebp-0x20]
    op(0x8B, 0x55, 0xF8)                          # mov edx,[ebp-8]
    call_va(LSTRASG_VA)
    op(0x8D, 0x45, 0xE0)                          # lea eax,[ebp-0x20]
    op(0x8B, 0x55, 0xE8)                          # mov edx,[ebp-0x18]
    call_va(SETLEN_VA)                            # 写时复制 → 独占缓冲
    op(0x8B, 0x7D, 0xE0)                          # mov edi,[ebp-0x20]
    op(0x89, 0xFE)                                # mov esi,edi
    op(0x8B, 0x45, 0xEC)                          # mov eax,[ebp-0x14]
    op(0x01, 0xC6)                                # add esi,eax
    op(0x8B, 0x45, 0xF0)                          # mov eax,[ebp-0x10]
    op(0x01, 0xC6)                                # add esi,eax        ; esi = s+o+plen
    op(0x8B, 0x4D, 0xE8)                          # mov ecx,[ebp-0x18]
    op(0x2B, 0x4D, 0xEC)                          # sub ecx,[ebp-0x14]
    op(0x2B, 0x4D, 0xF0)                          # sub ecx,[ebp-0x10]
    op(0xF3, 0xA4)                                # rep movsb
    op(0x8D, 0x45, 0xE0)                          # lea eax,[ebp-0x20]
    op(0x8B, 0x55, 0xE8)                          # mov edx,[ebp-0x18]
    op(0x2B, 0x55, 0xEC)                          # sub edx,[ebp-0x14]
    op(0x2B, 0x55, 0xF0)                          # sub edx,[ebp-0x10]
    call_va(SETLEN_VA)

    # --- 转换两段（空串跳过，保持空） ---
    op(0x8B, 0x45, 0xE4)                          # mov eax,[ebp-0x1C]
    op(0x85, 0xC0)                                # test eax,eax
    jcc(0x84, 'sk1')
    op(0x8D, 0x55, 0xDC)                          # lea edx,[ebp-0x24]
    call_va(CONVERT_ALL_VA)                       # c1 = C(前缀)
    L('sk1')
    op(0x8B, 0x45, 0xE0)                          # mov eax,[ebp-0x20]
    op(0x85, 0xC0)                                # test eax,eax
    jcc(0x84, 'sk2')
    op(0x8D, 0x55, 0xD8)                          # lea edx,[ebp-0x28]
    call_va(CONVERT_ALL_VA)                       # c2 = C(后缀)
    L('sk2')

    # --- out := c1 + P + c2 ---
    op(0x8B, 0x45, 0xFC)                          # mov eax,[ebp-4]
    op(0x8B, 0x55, 0xDC)                          # mov edx,[ebp-0x24]
    call_va(LSTRASG_VA)
    op(0x8B, 0x45, 0xFC)                          # mov eax,[ebp-4]
    op(0x8B, 0x55, 0xF4)                          # mov edx,[ebp-0xC]
    call_va(LSTRCAT_VA)
    op(0x8B, 0x45, 0xFC)                          # mov eax,[ebp-4]
    op(0x8B, 0x55, 0xD8)                          # mov edx,[ebp-0x28]
    call_va(LSTRCAT_VA)
    jmp_l('out')

    L('rawecho')                                  # out := src（原文）
    op(0x8B, 0x45, 0xFC)                          # mov eax,[ebp-4]
    op(0x8B, 0x55, 0xF8)                          # mov edx,[ebp-8]
    call_va(LSTRASG_VA)
    jmp_l('out')

    L('plain')                                    # 照常转换
    op(0x8B, 0x45, 0xF8)                          # mov eax,[ebp-8]
    op(0x8B, 0x55, 0xFC)                          # mov edx,[ebp-4]
    call_va(CONVERT_ALL_VA)

    L('out')
    op(0x8B, 0xE5)                                # mov esp,ebp
    op(0x5D)                                      # pop ebp
    op(0x5F)                                      # pop edi
    op(0x5E)                                      # pop esi
    op(0x5B)                                      # pop ebx
    op(0xC3)                                      # ret

    # --- strlen：eax=ptr → ecx=len（保留 eax） ---
    L('strlen')
    op(0x50)                                      # push eax
    op(0x31, 0xC9)                                # xor ecx,ecx
    L('sl_loop')
    op(0x80, 0x38, 0x00)                          # cmp byte [eax],0
    jcc(0x84, 'sl_done')
    op(0x40)                                      # inc eax
    op(0x41)                                      # inc ecx
    jmp_l('sl_loop')
    L('sl_done')
    op(0x58)                                      # pop eax
    op(0xC3)                                      # ret

    # --- find：eax=hay, edx=needle → eax=偏移 或 -1（保留 ebx/esi/edi） ---
    L('find')
    op(0x53)                                      # push ebx
    op(0x56)                                      # push esi
    op(0x57)                                      # push edi
    op(0x89, 0xC3)                                # mov ebx,eax      ; hay 基准
    op(0x89, 0xD6)                                # mov esi,edx      ; needle
    op(0x89, 0xF7)                                # mov edi,esi
    L('nl_loop')
    op(0x80, 0x3F, 0x00)                          # cmp byte [edi],0
    jcc(0x84, 'nl_done')
    op(0x47)                                      # inc edi
    jmp_l('nl_loop')
    L('nl_done')
    op(0x29, 0xF7)                                # sub edi,esi      ; nl
    op(0x85, 0xFF)                                # test edi,edi
    jcc(0x84, 'find_fail')
    op(0x89, 0xD8)                                # mov eax,ebx
    L('outer')
    op(0x80, 0x38, 0x00)                          # cmp byte [eax],0
    jcc(0x84, 'find_fail')
    op(0x31, 0xC9)                                # xor ecx,ecx
    L('f_cmp')
    op(0x39, 0xF9)                                # cmp ecx,edi
    jcc(0x84, 'find_hit')
    op(0x8A, 0x14, 0x08)                          # mov dl,[eax+ecx]
    op(0x84, 0xD2)                                # test dl,dl
    jcc(0x84, 'find_fail')                        # 干草已到末尾
    op(0x8A, 0x34, 0x0E)                          # mov dh,[esi+ecx]
    op(0x38, 0xF2)                                # cmp dl,dh
    jcc(0x85, 'f_next')
    op(0x41)                                      # inc ecx
    jmp_l('f_cmp')
    L('f_next')
    op(0x40)                                      # inc eax
    jmp_l('outer')
    L('find_hit')
    op(0x29, 0xD8)                                # sub eax,ebx
    jmp_l('find_done')
    L('find_fail')
    op(0xB8, 0xFF, 0xFF, 0xFF, 0xFF)              # mov eax,-1
    L('find_done')
    op(0x5F)                                      # pop edi
    op(0x5E)                                      # pop esi
    op(0x5B)                                      # pop ebx
    op(0xC3)                                      # ret

    # --- 回填 ---
    for kind, pos, name in fixups:
        if kind == 'rel':
            struct.pack_into('<i', b, pos, labels[name] - (pos + 4))
        elif kind == 'va':
            struct.pack_into('<i', b, pos, name - (stub_va + pos + 4))
        elif kind == 'imm32':
            struct.pack_into('<I', b, pos, name & 0xFFFFFFFF)
    assert len(b) <= 0x200, hex(len(b))   # 测试入口在 cave+0x3D00，桩不得越过
    return bytes(b)


def _build_gravity_map():
    """生成重力语逐字映射表（4 字节/条：GBK 源对 + GBK 目标对；00 00 终结）。

    规格：原版会影响的所有 SJIS 字符（双字节、尾字节 0x41-0x5A → +0x20），
    凡"源与结果字形"都存在于 GBK 的，逐字映射（行为与原版相同）；
    其余（SJIS 不存在 / 不受影响 / 不在 GBK）一律保持不变。
    实测：假名 32 + 汉字 946 + 符号 66 = 1044 条（如 右→影、宇→映、。→｜、！→（、
    リンゴ→リンピ……与日文原版逐字一致；日文标点也按原版参与）。
    """
    entries = bytearray()
    count = 0
    for hi in range(0x81, 0xFD):
        if hi == 0x7F:
            continue
        for lo in range(0x41, 0x5B):
            try:
                ch = bytes([hi, lo]).decode('cp932')
                ch2 = bytes([hi, lo + 0x20]).decode('cp932')
            except UnicodeError:
                continue
            if len(ch) != 1 or len(ch2) != 1:
                continue
            try:
                g1 = ch.encode('gbk')
                g2 = ch2.encode('gbk')
            except UnicodeError:
                continue
            if len(g1) != 2 or len(g2) != 2:
                continue
            entries += g1 + g2
            count += 1
    entries += b'\x00\x00'
    return bytes(entries), count


def _build_gravity_stub(stub_va, table_va):
    """重力语中文化的 GBK 变换桩（.cave+0x4000；替换 0x418D21 / 0x46FC03 两处
    call 0x417048"转义"调用，对话与菜单窗口共用）。

    进入时 EAX=待翻译文本（ANSI 串值）、EDX=输出串变量地址（与原转义调用一致）。
    输出 = 全小写 %xx 转义文本，交给后续原有的"小写(0x408358)+解码(0x4171A0)"：
      - 输出无大写 ASCII、无裸 %，两步对它幂等/严格可逆（不依赖 CharLowerBuffA 的
        DBCS 行为）；解码后即最终文本（对话管线随后的 0x01→% 还原不受影响）。
    规则（表驱动，逐字复刻原版行为）：
      - ASCII A-Z → a-z（原版单字节规则）；
      - 双字节对命中映射表（table_va，见 _build_gravity_map）→ 换成表中结果对；
      - 其余双字节 / 孤立字节原样（SJIS 不存在 / 不受影响 / 不在 GBK 的一律不变）；
      - [ ] 区逐字节原样（脚本安全）；'%'→%25。
    一次扫描 + 3×len 预分配 + 收尾 SetLength 收缩（免二次计数扫描）。
    位置无关：call/pop/delta 求表地址；只有相对 call（E8）与立即数，无绝对引用。
    """
    b = bytearray()
    labels = {}
    fixups = []

    def op(*xs):
        b.extend(xs)

    def L(name):
        labels[name] = len(b)

    def rel(name):
        fixups.append(('rel', len(b), name))
        op(0, 0, 0, 0)

    def jmp_l(name):
        op(0xE9); rel(name)

    def jcc(cc, name):
        op(0x0F, cc); rel(name)

    def call_l(name):
        op(0xE8); rel(name)

    def call_va(target):
        op(0xE8); fixups.append(('va', len(b), target)); op(0, 0, 0, 0)

    # --- 序 ---
    op(0x53)                                     # push ebx
    op(0x56)                                     # push esi
    op(0x57)                                     # push edi
    op(0x55)                                     # push ebp
    op(0x8B, 0xEC)                               # mov ebp,esp
    op(0x81, 0xEC, 0x20, 0x00, 0x00, 0x00)       # sub esp,0x20
    op(0x89, 0x55, 0xFC)                         # mov [ebp-4],edx   ; &out
    op(0x89, 0x45, 0xF8)                         # mov [ebp-8],eax   ; src
    op(0xC6, 0x45, 0xF0, 0x00)                   # mov byte [ebp-0x10],0（bracket）
    op(0xE8, 0x00, 0x00, 0x00, 0x00)             # call $+5
    tbl_ret_va = stub_va + len(b)
    op(0x58)                                     # pop eax
    op(0x05); op(*struct.pack('<i', table_va - tbl_ret_va))   # add eax,Δ → 映射表地址
    op(0x89, 0x45, 0xEC)                         # mov [ebp-0x14],eax（表基址）
    op(0x8B, 0x45, 0xF8)                         # mov eax,[ebp-8]（取回 src）
    op(0x85, 0xC0)                               # test eax,eax
    jcc(0x84, 'empty')                           # je .empty

    # --- len = strlen(src) ---
    op(0x89, 0xC6)                               # mov esi,eax
    op(0x31, 0xC9)                               # xor ecx,ecx
    L('len_loop')
    op(0x80, 0x3E, 0x00)                         # cmp byte [esi],0
    jcc(0x84, 'len_done')
    op(0x46)                                     # inc esi
    op(0x41)                                     # inc ecx
    jmp_l('len_loop')
    L('len_done')
    op(0x8D, 0x14, 0x49)                         # lea edx,[ecx+ecx*2]（3×len）
    op(0x8B, 0x45, 0xFC)                         # mov eax,[ebp-4]
    call_va(SETLEN_VA)                           # SetLength(&out, 3*len)
    op(0x8B, 0x45, 0xFC)                         # mov eax,[ebp-4]
    op(0x8B, 0x38)                               # mov edi,[eax]      ; W
    op(0x89, 0x7D, 0xF4)                         # mov [ebp-0xC],edi  ; base
    op(0x8B, 0x75, 0xF8)                         # mov esi,[ebp-8]

    # --- 扫描/发射 ---
    L('scan')
    op(0x8A, 0x06)                               # mov al,[esi]
    op(0x84, 0xC0)                               # test al,al
    jcc(0x84, 'done')
    op(0x3C, 0x5B)                               # cmp al,0x5B '['
    jcc(0x85, 'chk_5d')
    op(0xC6, 0x45, 0xF0, 0x01)                   # mov byte [ebp-0x10],1
    op(0x88, 0x07)                               # mov [edi],al
    op(0x47)                                     # inc edi
    op(0x46)                                     # inc esi
    jmp_l('scan')
    L('chk_5d')
    op(0x3C, 0x5D)                               # cmp al,0x5D ']'
    jcc(0x85, 'chk_br')
    op(0xC6, 0x45, 0xF0, 0x00)                   # mov byte [ebp-0x10],0
    op(0x88, 0x07)                               # mov [edi],al
    op(0x47)
    op(0x46)
    jmp_l('scan')
    L('chk_br')
    op(0x80, 0x7D, 0xF0, 0x00)                   # cmp byte [ebp-0x10],0
    jcc(0x85, 'esc1')                            # jne .esc1（方括号内 → 逐字节原样）
    op(0x3C, 0x25)                               # cmp al,'%'
    jcc(0x84, 'esc1')
    op(0xA8, 0x80)                               # test al,0x80
    jcc(0x85, 'pair')
    op(0x3C, 0x41)                               # cmp al,'A'
    jcc(0x82, 'raw1')                            # jb
    op(0x3C, 0x5A)                               # cmp al,'Z'
    jcc(0x87, 'raw1')                            # ja
    op(0x04, 0x20)                               # add al,0x20
    L('raw1')
    op(0x88, 0x07)                               # mov [edi],al
    op(0x47)
    op(0x46)
    jmp_l('scan')
    L('esc1')
    call_l('emit_esc')
    op(0x46)                                     # inc esi
    jmp_l('scan')
    L('pair')
    op(0x8A, 0x5E, 0x01)                         # mov bl,[esi+1]
    op(0x84, 0xDB)                               # test bl,bl
    jcc(0x84, 'lone')
    op(0x80, 0xFB, 0x40)                         # cmp bl,0x40
    jcc(0x82, 'lone')                            # jb
    op(0x80, 0xFB, 0xFE)                         # cmp bl,0xFE
    jcc(0x87, 'lone')                            # ja（0xFF）
    op(0x80, 0xFB, 0x7F)                         # cmp bl,0x7F
    jcc(0x84, 'lone')                            # je
    # --- 查映射表：key = b1 | b2<<8（小端字）；命中则换成表内目标对 ---
    op(0x88, 0xC2)                               # mov dl,al（b1 → key 低字节）
    op(0x88, 0xDE)                               # mov dh,bl（b2 → key 高字节）
    op(0x8B, 0x45, 0xEC)                         # mov eax,[ebp-0x14]（表基址）
    L('lk')
    op(0x66, 0x8B, 0x08)                         # mov cx,[eax]
    op(0x66, 0x85, 0xC9)                         # test cx,cx
    jcc(0x84, 'lk_done')                         # 终结（0000）→ 未命中
    op(0x66, 0x39, 0xD1)                         # cmp cx,dx
    jcc(0x84, 'lk_found')
    op(0x83, 0xC0, 0x04)                         # add eax,4
    jmp_l('lk')
    L('lk_found')
    op(0x8A, 0x58, 0x03)                         # mov bl,[eax+3]（目标尾字节）
    op(0x8A, 0x40, 0x02)                         # mov al,[eax+2]（目标前导）
    jmp_l('pair_emit')
    L('lk_done')
    op(0x88, 0xD0)                               # mov al,dl（未命中 → 恢复源前导 b1）
    L('pair_emit')
    call_l('emit_esc')                           # 前导
    op(0x88, 0xD8)                               # mov al,bl
    call_l('emit_esc')                           # 尾字节
    op(0x83, 0xC6, 0x02)                         # add esi,2
    jmp_l('scan')
    L('lone')
    call_l('emit_esc')
    op(0x46)                                     # inc esi
    jmp_l('scan')

    # --- 收尾 ---
    L('done')
    op(0x89, 0xFA)                               # mov edx,edi
    op(0x2B, 0x55, 0xF4)                         # sub edx,[ebp-0xC]
    op(0x8B, 0x45, 0xFC)                         # mov eax,[ebp-4]
    call_va(SETLEN_VA)                           # SetLength(&out, written)
    op(0x8B, 0x45, 0xFC)                         # mov eax,[ebp-4]
    op(0x8B, 0x00)                               # mov eax,[eax]
    op(0x89, 0xFA)                               # mov edx,edi
    op(0x2B, 0x55, 0xF4)                         # sub edx,[ebp-0xC]
    op(0xC6, 0x04, 0x10, 0x00)                   # mov byte [eax+edx],0
    jmp_l('epi')
    L('empty')
    op(0x31, 0xD2)                               # xor edx,edx
    op(0x8B, 0x45, 0xFC)                         # mov eax,[ebp-4]
    call_va(SETLEN_VA)                           # out := ''
    L('epi')
    op(0x8B, 0xE5)                               # mov esp,ebp
    op(0x5D); op(0x5F); op(0x5E); op(0x5B)       # pop ebp/edi/esi/ebx
    op(0xC3)                                     # ret

    # --- 子程序：emit_esc（al=字节 → 写 "%xx" 小写 hex，edi+=3；clobber ah/cl） ---
    L('emit_esc')
    op(0xC6, 0x07, 0x25)                         # mov byte [edi],0x25
    op(0x47)                                     # inc edi
    op(0x88, 0xC4)                               # mov ah,al
    op(0xC0, 0xEC, 0x04)                         # shr ah,4
    op(0x88, 0xC1)                               # mov cl,al
    op(0x80, 0xE1, 0x0F)                         # and cl,0x0F
    op(0x80, 0xFC, 0x0A)                         # cmp ah,10
    jcc(0x82, 'hi_d')                            # jb
    op(0x80, 0xC4, 0x57)                         # add ah,0x57（'a'-10）
    jmp_l('hi_s')
    L('hi_d')
    op(0x80, 0xC4, 0x30)                         # add ah,0x30
    L('hi_s')
    op(0x88, 0x27)                               # mov [edi],ah
    op(0x47)
    op(0x80, 0xF9, 0x0A)                         # cmp cl,10
    jcc(0x82, 'lo_d')
    op(0x80, 0xC1, 0x57)
    jmp_l('lo_s')
    L('lo_d')
    op(0x80, 0xC1, 0x30)
    L('lo_s')
    op(0x88, 0x0F)                               # mov [edi],cl
    op(0x47)
    op(0xC3)                                     # ret

    # --- 回填 ---
    for kind, pos, name in fixups:
        if kind == 'rel':
            struct.pack_into('<i', b, pos, labels[name] - (pos + 4))
        elif kind == 'va':
            struct.pack_into('<i', b, pos, name - (stub_va + pos + 4))
    assert len(b) < 0x600, hex(len(b))
    return bytes(b)


def _build_closebox_stub(cave_va):
    """关游戏窗体的辅助桩（主块 .cave+0x400，由 stubA 的 .setpend 调用，
    ebx = 运行时 cave+0x60B）：

      退出游戏时，把游戏自己创建的 Delphi 窗体关掉：
        - "Ttypinggameform"：打字游戏的输入框（内含 TEdit 子窗）；
        - "Teyesightform"：问答第一题的 C 图窗 / 视力检查共用；
        - "Tcountdownform"：倒计时窗（无 FormClose 处理器 → 关闭=隐藏）。
      都是 FindWindowA 找到后各发一条 WM_CLOSE，让 DLL 走自己的 Close 路径
      （打字框的 CloseQuery 需先放行，由 .cave+0x10D0 的桩在 GAMELEFT 时处理；
       另外两个窗体本来就无拦截）。全程没有键盘消息 → 无编辑框回车提示音。
      PostMessage 异步投递，不会死锁（同步调用是之前卡死的教训）。
    """
    b = bytearray()
    fixups = []
    labels = {}

    def label(n):
        labels[n] = len(b)

    def r32(name):
        fixups.append(('r32', len(b), name))
        return b'\x00\x00\x00\x00'

    def jn(short_op, name):
        b.append(0x0F)
        b.append(short_op + 0x10)
        b.extend(r32(name))

    STR_CLS = 0x110        # "Ttypinggameform"
    STR_EYE = 0x120        # "Teyesightform"
    STR_CD = 0x130         # "Tcountdownform"（倒计时窗；无 FormClose → 关闭=caHide）

    IAT_FW = 0x4B3704      # FindWindowA
    IAT_POST = 0x4B35E0    # PostMessageA

    def D(cave_off):
        return cave_off - 0x60B

    def call_iat(iat_va):
        b.extend(b'\xFF\x93' + struct.pack('<i', iat_va - (cave_va + 0x60B)))

    def close_one(cls_off, skip_label):
        b.extend(b'\x8D\x83' + struct.pack('<i', D(cls_off)))   # lea eax,[ebx+cls]
        b.extend(b'\x6A\x00')                                   # push 0（lpClassName 参数占位）
        b.extend(b'\x50')                                        # push eax
        call_iat(IAT_FW)
        b.extend(b'\x85\xC0')                                   # test eax,eax
        jn(0x74, skip_label)                                      # 没找到 → 跳过
        b.extend(b'\x8B\xF0')                                   # mov esi,eax（窗体句柄）
        b.extend(b'\x6A\x00')                                   # push 0（lparam）
        b.extend(b'\x6A\x00')                                   # push 0（wparam）
        b.extend(b'\x68\x10\x00\x00\x00')                    # push WM_CLOSE
        b.extend(b'\x56')                                        # push esi
        call_iat(IAT_POST)

    b.extend(b'\x50\x51\x52\x56\x57')                        # push eax/ecx/edx/esi/edi
    close_one(STR_CLS, 'eye')
    label('eye')
    close_one(STR_EYE, 'cd')
    label('cd')
    close_one(STR_CD, 'done')
    label('done')
    b.extend(b'\x5F\x5E\x5A\x59\x58')                        # pop edi/esi/edx/ecx/eax
    b.append(0xC3)                                                # ret
    for kind, pos, name in fixups:
        t = labels[name]
        struct.pack_into('<i', b, pos, t - (pos + 4))
    assert len(b) <= 0x100, hex(len(b))
    # ================= 数据区（.cave+0x110）=================
    strs = bytearray(b'\x00' * (0x1F2 - 0x110))
    strs[0x000:0x010] = b'Ttypinggameform\x00'
    strs[0x010:0x01F] = b'Teyesightform\x00'
    strs[0x020:0x02E] = b'Tcountdownform\x00'
    # 空提交脚本（视力输入框被取消时回它，SSP 触发 OnEyesightgameInput 空值）
    eyecancel = b'\\![raise,OnEyesightgameInput,]'
    o = EYE_CANCEL_STR_OFF - 0x110
    strs[o:o + 8 + len(eyecancel) + 2] = (
        struct.pack('<i', -1) + struct.pack('<I', len(eyecancel)) + eyecancel + b'\x00\x00')
    return bytes(b), bytes(strs)


def _build_eye_dc_stub(cave_va):
    """视力未弹框双击小段（.cave+0x380，由桩B 调用）：自设 ebx 后调关窗辅助桩
    （竞态兜底：万一 C 图窗已出现就 WM_CLOSE 掉）。
    注意：辅助桩以 ebx=cave+0x60B 为基址且**不**恢复它——调用方必须自己设置/恢复，
    否则用垃圾指针调 FindWindowA → 异常 → SSP 收 500
    （桩A 本来就以 ebx=cave+0x60B 跑；桩B 的 ebx 是外部值，需本小段兜底）。"""
    rva = cave_va + EYE_DC_STUB_OFF
    b = bytearray()
    va = lambda i: rva + i

    def rel32(target, at):
        return struct.pack('<i', target - va(at + 4))

    b += b'\x53'                                                  # 0: push ebx（保存调用者）
    b += b'\xE8\x00\x00\x00\x00'                                  # 1: call $+5
    b += b'\x5B'                                                  # 6: pop ebx (= va(6))
    b += b'\x81\xC3' + struct.pack('<i', 0x60B - (EYE_DC_STUB_OFF + 6))  # 7: add ebx,Δ（→ cave+0x60B）
    b += b'\xE8' + rel32(cave_va + 0x400, 14)                     # 13: call 关窗辅助桩
    b += b'\x5B'                                                  # 18: pop ebx（恢复）
    b += b'\xC3'                                                  # 19: ret
    assert len(b) == 20, hex(len(b))
    return bytes(b)


def _build_dc_status_stub(rva):
    """双击入口桩（挂在 0x4782BD，request() 帧内，EBP 有效）：

      分流（按优先级）：
      1) 请求含 "Status: choosing"（选择肢/菜单等待中）→ 吞掉（204）；
      2) 请求含 "passive"（游戏进行中）：
         - EYEBUSY=1（视力题目的窗口+输入框已弹出）→ 吞掉（防误触退出）；
         - MARK=1（视力已进入但还没弹框）→ 置 PENDING（桩A 前置含
           leave,passivemode 的 CLOSE_CMD）+ 关窗小段 + 清响应走原版入口出主菜单
           （原版该入口依赖请求里的鼠标 Reference，真实双击事件才有）。
         - MARK=0（打字/问答）→ 菜单缓存（cave+0x960）非空则 LStrAsg 写回
           缓存菜单（方便点「退出」）；为空则吞掉。
      3) 输入框标志 FLAG=1（搜索等 SSP 输入框打开）→ 吞掉；
      4) 其余 → 清响应后跳 0x4782C5，走 ghost 原逻辑（弹主菜单）。

      注意：不要动 DLL 内部"点击/菜单状态机"字节 0x4B29E8——它在菜单流程里
      有自己的状态转移（0/1/2/3），在拦截出口清零会破坏"菜单→开始"流程
      （曾导致点 Period 进不去）。

      请求扫描约定：[ebp+8]=请求字符串指针（PChar）、[ebp+0xC]=指向长度的
      指针（DWORD*）（经 0x45F220 + System.Move 0x40283C 反汇编确认）。
    """
    b = bytearray()
    labels = {}
    fixups = []
    va = lambda i: rva + i

    def label(n):
        labels[n] = len(b)

    def r8(name):
        fixups.append(('r8', len(b), name))
        return b'\x00'

    def r32(name):
        fixups.append(('r32', len(b), name))
        return b'\x00\x00\x00\x00'

    def j8(op, name):
        b.append(op)
        b.extend(r8(name))

    def jcc32(op0, op1, name):
        b.append(op0)
        b.append(op1)
        b.extend(r32(name))

    def call_abs(target):
        b.append(0xE8)
        b.extend(struct.pack('<i', target - va(len(b) + 4)))

    def jmp_abs(target):
        b.append(0xE9)
        b.extend(struct.pack('<i', target - va(len(b) + 4)))

    disp = FLAG_OFF - (CAVE_B_OFF + 0x07)   # 基址计算：call$+5 在偏移2 → pop 得 stub+7
    mark_off = MARK_OFF - FLAG_OFF          # 各标志相对 eax(=cave+FLAG_OFF) 的位移
    eye_off = EYEBUSY_OFF - FLAG_OFF
    cache_len_off = CACHE_OFF - FLAG_OFF
    cache_data_off = CACHE_OFF + 4 - FLAG_OFF

    # --- 0x00: 基址 + 扫请求找 "Status: choosing" ---
    b += b'\x57\x56'                                    # push edi/esi
    b += b'\xE8\x00\x00\x00\x00'                     # call $+5
    b += b'\x58'                                         # pop eax
    b += b'\x05' + struct.pack('<i', disp)               # add eax, disp（eax=cave+FLAG_OFF）
    b += b'\x8B\x7D\x08'                               # mov edi,[ebp+8]（请求指针）
    b += b'\x8B\x4D\x0C'                               # mov ecx,[ebp+0xC]
    b += b'\x8B\x09'                                    # mov ecx,[ecx]（长度）
    b += b'\x83\xF9\x10'                               # cmp ecx,16
    j8(0x72, 'reqdone')                                   # jb .reqdone
    b += b'\x8D\x74\x0F\xF0'                          # lea esi,[ecx+edi-16]
    label('scan')
    b += b'\x81\x3F\x53\x74\x61\x74'                # cmp [edi],'Stat'
    j8(0x75, 'nxt')
    b += b'\x81\x7F\x04\x75\x73\x3A\x20'           # cmp [edi+4],'us: '
    j8(0x75, 'nxt')
    b += b'\x81\x7F\x08\x63\x68\x6F\x6F'           # cmp [edi+8],'choo'
    j8(0x75, 'nxt')
    b += b'\x81\x7F\x0C\x73\x69\x6E\x67'           # cmp [edi+0xC],'sing'
    jcc32(0x0F, 0x84, 'suppress')                         # je .suppress（距离远，rel32）
    label('nxt')
    b += b'\x47'                                         # inc edi
    b += b'\x39\xF7'                                    # cmp edi,esi
    j8(0x76, 'scan')                                      # jbe .scan
    # --- 扫请求找 "passive"（eax 已是 cave+FLAG_OFF）---
    label('reqdone')
    b += b'\x8B\x7D\x08'                               # mov edi,[ebp+8]
    b += b'\x8B\x4D\x0C'                               # mov ecx,[ebp+0xC]
    b += b'\x8B\x09'                                    # mov ecx,[ecx]
    b += b'\x83\xF9\x07'                               # cmp ecx,7
    jcc32(0x0F, 0x82, 'flagchk')                          # jb .flagchk（距离远，rel32）
    b += b'\x8D\x74\x0F\xF9'                          # lea esi,[ecx+edi-7]
    b += b'\x8D\x51\xFA'                               # lea edx,[ecx-6]
    label('ps')
    b += b'\x81\x3F\x70\x61\x73\x73'                # cmp [edi],'pass'
    j8(0x75, 'pn')
    b += b'\x66\x81\x7F\x04\x69\x76'                # cmp word [edi+4],'iv'
    j8(0x75, 'pn')
    b += b'\x80\x7F\x06\x65'                          # cmp byte [edi+6],'e'
    j8(0x74, 'passive')
    label('pn')
    b += b'\x47'                                         # inc edi
    b += b'\x4A'                                         # dec edx
    j8(0x75, 'ps')
    jcc32(0x0F, 0x84, 'flagchk')                          # 未找到 → .flagchk（距离远，rel32）

    # --- passive：EYEBUSY → 吞；MARK → 收尾+原版出主菜单；否则重放缓存 ---
    label('passive')
    b += b'\x80\xB8' + struct.pack('<i', eye_off) + b'\x00'   # cmp byte [eax+EYEBUSY],0
    jcc32(0x0F, 0x85, 'suppress')                         # jne .suppress（视力答题中双击禁用）
    b += b'\x83\xB8' + struct.pack('<i', mark_off) + b'\x00'  # cmp dword [eax+MARK],0
    jcc32(0x0F, 0x85, 'eyeexit')                          # jne .eyeexit（视力未弹框 → 收尾+出菜单）
    b += b'\x83\xB8' + struct.pack('<i', cache_len_off) + b'\x00'   # cmp dword [eax+cache_len],0
    jcc32(0x0F, 0x84, 'suppress')                         # je .suppress（无缓存 → 吞）
    b += b'\x8D\x90' + struct.pack('<i', cache_data_off)  # lea edx,[eax+cache_data]（缓存串）
    b += b'\x8D\x45\xE4'                               # lea eax,[ebp-0x1c]
    call_abs(0x403C58)                                    # call LStrAsg（响应 := 缓存菜单）
    b += b'\x5E\x5F'                                    # pop esi/edi
    jmp_abs(DC_DONE_VA)                                   # jmp 0x478848
    # --- 吞掉双击（204）---
    label('suppress')
    b += b'\x5E\x5F'                                    # pop esi/edi
    b += b'\x8D\x45\xE4'                               # lea eax,[ebp-0x1c]
    call_abs(0x403BC0)                                    # call 0x403BC0（清响应）
    jmp_abs(DC_DONE_VA)                                   # jmp 0x478848
    # --- 输入框标志检查（清响应 → 204）---
    label('flagchk')
    b += b'\x83\x38\x00'                               # cmp dword [eax],0（FLAG）
    jcc32(0x0F, 0x85, 'suppress')                         # jne .suppress
    # --- 非 passive 非输入框：清响应走原逻辑（主菜单）---
    label('normal')
    b += b'\x5E\x5F'                                    # pop esi/edi
    b += b'\x8D\x45\xE4'                               # lea eax,[ebp-0x1c]
    call_abs(0x403BC0)                                    # call 0x403BC0（清响应）
    jmp_abs(DC_CONT_VA)                                   # jmp 0x4782C5（原入口）
    # --- 视力未弹框：置 PENDING（桩A 前置 CLOSE_CMD，含 leave,passivemode）+
    #     调关窗小段（竞态）+ 清响应走原版出主菜单（依赖真实双击的鼠标 Reference）---
    label('eyeexit')
    b += b'\x5E\x5F'                                    # pop esi/edi
    call_abs(rva + (EYE_DC_STUB_OFF - CAVE_B_OFF))        # call .cave+0x380（关窗小段）
    b += b'\xC6\x40' + bytes([PENDING_OFF - FLAG_OFF]) + b'\x01'  # mov byte [eax+PENDING],1
    b += b'\x8D\x45\xE4'                               # lea eax,[ebp-0x1c]
    call_abs(0x403BC0)                                    # call 0x403BC0（清响应）
    jmp_abs(DC_CONT_VA)                                   # jmp 0x4782C5（原入口 → 主菜单）
    # --- 解算分支 ---
    for kind, pos, name in fixups:
        t = labels[name]
        if kind == 'r8':
            v = t - (pos + 1)
            assert -128 <= v <= 127, (name, hex(t), hex(pos), v)
            struct.pack_into('<b', b, pos, v)
        else:
            struct.pack_into('<i', b, pos, t - (pos + 4))
    assert len(b) <= 0x100, hex(len(b))
    return bytes(b)


def patch_extra_link(data: bytearray) -> bytearray:
    """应用全部 .cave 补丁：链接化 / RSS 打开浏览器 / 响应监控（输入框标志、
    游戏状态、退出收尾）/ 关游戏窗体 / CloseQuery 放行 / 双击判定。"""
    blob = bytearray(IME_CAVE_SIZE)  # 原 0x2800 → IME 层 0x4000 → 重力语桩 0x6000
    rva = add_cave_section(data, bytes(blob))
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    sec_tab = e + 24 + _u16(data, e + 20)
    raw = _u32(data, sec_tab + (nsec - 1) * 40 + 20)      # .cave 的原始偏移

    # 统一用首选 VA（镜像基址 0x400000 + RVA）做 rel32 计算；
    # 运行时无论是否重定位，桩与目标同基址平移，相对量保持不变。
    cave_va = 0x400000 + rva

    # 桩 1：海原雄山
    data[raw:raw + 26] = _build_linkify_stub(cave_va)
    if bytes(data[LINKIFY_CALL_OFF:LINKIFY_CALL_OFF + 5]) != LINKIFY_CALL_ORIG:
        raise RuntimeError('海原雄山补丁：重定向点原始字节不匹配')
    data[LINKIFY_CALL_OFF:LINKIFY_CALL_OFF + 5] = (
        b'\xE8' + struct.pack('<i', cave_va - LINKIFY_CALL_NEXT_VA))

    # 桩 2：OnAnchorSelect http
    stub2 = _build_url_stub(cave_va + CAVE2_OFF)
    data[raw + CAVE2_OFF:raw + CAVE2_OFF + len(stub2)] = stub2
    data[raw + PREFIX_OFF:raw + PREFIX_OFF + len(URL_RESP_PREFIX)] = URL_RESP_PREFIX
    if bytes(data[URL_HOOK_OFF:URL_HOOK_OFF + 8]) != URL_HOOK_ORIG:
        raise RuntimeError('RSS 链接补丁：重定向点原始字节不匹配')
    data[URL_HOOK_OFF:URL_HOOK_OFF + 5] = (
        b'\xE9' + struct.pack('<i', cave_va + CAVE2_OFF - URL_HOOK_NEXT_VA))
    data[URL_HOOK_OFF + 5:URL_HOOK_OFF + 8] = b'\x90' * 3

    # 桩 A：响应监控（输入框标志/游戏状态/退出收尾，挂在事件响应汇总点）
    stubA = _build_resp_monitor_stub(cave_va + CAVE_A_OFF)
    data[raw + CAVE_A_OFF:raw + CAVE_A_OFF + len(stubA)] = stubA
    off = RESP_DONE_VA - 0x400C00
    if bytes(data[off:off + 6]) != RESP_DONE_ORIG:
        raise RuntimeError('双击补丁：响应汇总点原始字节不匹配')
    data[off:off + 6] = (b'\xE9' + struct.pack(
        '<i', cave_va + CAVE_A_OFF - (RESP_DONE_VA + 5))) + b'\x90'

    # 常量：关闭所有输入框的命令（退出事件时前置到响应）
    data[raw + CLOSE_CMD_OFF:raw + CLOSE_CMD_OFF + len(CLOSE_CMD)] = CLOSE_CMD

    # 关游戏窗体辅助桩（.cave+0x400）+ 类名数据（.cave+0x110）
    _cb, _cbstrs = _build_closebox_stub(cave_va)
    data[raw + 0x400:raw + 0x400 + len(_cb)] = _cb
    data[raw + 0x110:raw + 0x110 + len(_cbstrs)] = _cbstrs

    # 视力未弹框双击小段（桩B 调用：自设 ebx → 调关窗辅助桩）
    _edc = _build_eye_dc_stub(cave_va)
    data[raw + EYE_DC_STUB_OFF:raw + EYE_DC_STUB_OFF + len(_edc)] = _edc

    # 跳板：GAMELEFT 时放行 formCloseQuery（配合辅助桩的 WM_CLOSE 关框）
    stubQ = _build_typing_closeq_stub(cave_va)
    data[raw + CAVE_Q_OFF:raw + CAVE_Q_OFF + len(stubQ)] = stubQ
    off = TYPING_CLOSEQ_OFF
    if bytes(data[off:off + 9]) != TYPING_CLOSEQ_ORIG:
        raise RuntimeError('打字框关闭补丁：CloseQuery 原始字节不匹配')
    data[off:off + 5] = b'\xE9' + struct.pack(
        '<i', cave_va + CAVE_Q_OFF - (TYPING_CLOSEQ_VA + 5))
    data[off + 5:off + 9] = b'\x90' * 4

    # 打字定向屏蔽：游戏进行中只豁免"玩家输入脚本"与"要打文本"子串，提示语等
    # 照常转换；退出游戏恢复；未游戏/开关全关时与原行为完全一致
    stubG = _build_typing_gate_stub(cave_va + TYPING_GATE_STUB_OFF)
    data[raw + TYPING_GATE_STUB_OFF:raw + TYPING_GATE_STUB_OFF + len(stubG)] = stubG
    off = TYPING_GATE_HOOK_VA - 0x400C00
    if bytes(data[off:off + 5]) != TYPING_GATE_HOOK_ORIG:
        raise RuntimeError('打字定向屏蔽补丁：挂钩点原始字节不匹配')
    data[off:off + 5] = b'\xE8' + struct.pack(
        '<i', cave_va + TYPING_GATE_STUB_OFF - (TYPING_GATE_HOOK_VA + 5))

    # 重力语变换：对话（0x418D21）与菜单窗口（0x46FC03）两处 call 0x417048 → 表驱动变换桩；
    # 百度搜索的 URL 编码调用点（0x476548）保持原样不动
    stubV = _build_gravity_stub(cave_va + GRAVITY_STUB_OFF, cave_va + GRAVITY_TABLE_OFF)
    if GRAVITY_STUB_OFF + len(stubV) > GRAVITY_TABLE_OFF:
        raise RuntimeError('重力语变换补丁：桩越过了映射表区')
    data[raw + GRAVITY_STUB_OFF:raw + GRAVITY_STUB_OFF + len(stubV)] = stubV
    mapV, mapN = _build_gravity_map()
    if GRAVITY_TABLE_OFF + len(mapV) > IME_CAVE_SIZE:
        raise RuntimeError('重力语变换补丁：映射表越出 cave')
    data[raw + GRAVITY_TABLE_OFF:raw + GRAVITY_TABLE_OFF + len(mapV)] = mapV
    print(f'  重力语逐字映射表: {mapN} 条 / {len(mapV)} 字节 @cave+0x{GRAVITY_TABLE_OFF:X}')
    for site_va, site_label in ((GRAVITY_HOOK_DLG_VA, '对话'), (GRAVITY_HOOK_WIN_VA, '窗口')):
        off = site_va - 0x400C00
        expect = b'\xE8' + struct.pack('<i', GRAVITY_ESCAPE_VA - (site_va + 5))
        if bytes(data[off:off + 5]) != expect:
            raise RuntimeError(f'重力语变换补丁：{site_label}挂钩点原始字节不匹配')
        data[off:off + 5] = b'\xE8' + struct.pack(
            '<i', cave_va + GRAVITY_STUB_OFF - (site_va + 5))

    # 桩 B：双击判定（choosing / 输入框 / 视力游戏 → 无反应；其他游戏重放菜单）
    stubB = _build_dc_status_stub(cave_va + CAVE_B_OFF)
    data[raw + CAVE_B_OFF:raw + CAVE_B_OFF + len(stubB)] = stubB
    off = DC_ENTRY_VA - 0x400C00
    if bytes(data[off:off + 8]) != DC_ENTRY_ORIG:
        raise RuntimeError('双击补丁：入口原始字节不匹配')
    data[off:off + 8] = (b'\xE9' + struct.pack(
        '<i', cave_va + CAVE_B_OFF - (DC_ENTRY_VA + 5))) + b'\x90' * 3

    print(f'补丁已应用: 链接化/RSS/响应监控/关窗体/双击判定/打字定向屏蔽/重力语中文化 @ RVA 0x{rva:X}')
    return data


# ------------------------------------------------------------- 高分屏缩放（导出包装）
# 不碰 CreateWindowEx 跳板/API 导入，改成把 DLL 的 load / request 两个导出入口
# 重定向到 .cave 的包装桩：调用真实函数前后把当前线程的 DPI 感知上下文临时切到
# UNAWARE_GDISCALED（-5），系统即按屏幕缩放、以 GDI 方式清晰放大这些窗口
# （含窗口控件与 FormPaint 自绘文字）。请求之外 SSP 自己的界面不受影响。
# 这些导出是「调用方清栈」（函数末尾为裸 ret），所以桩也以裸 ret 返回；
# 真实函数调用后由桩 add esp,8 清掉自压的实参副本。桩为位置无关代码，
# 全部状态在栈上（可重入/多线程安全）；PSET 指针惰性解析后存 .cave。
DPI_WRAP_REQ_OFF = 0x170      # request 包装桩（0x170-0x1F7 空闲，须 < 0x1F8）
DPI_WRAP_LOAD_OFF = 0x1F7C    # load 包装桩（0x1F7C-0x1FFF 空闲，须 ≤ 0x2000）
DPI_WRAP_DATA_OFF = 0xB0      # 数据：PSET(+0) / "user32.dll"(+4) / "SetThreadDpiAwarenessContext"(+0x10)
DPI_WRAP_IAT_GMH = 0x4B31E4   # GetModuleHandleA 的 IAT 槽（VA，首选基址 0x400000）
DPI_WRAP_IAT_GPA = 0x4B31E0   # GetProcAddress 的 IAT 槽（VA）
DPI_WRAP_PSET = 0x00
DPI_WRAP_USER32 = 0x04
DPI_WRAP_SETNAME = 0x10
DPI_WRAP_IB = 0x400000        # 映像首选基址
DPI_WRAP_ENABLE = True        # 总开关：出问题改 False 重建即可回到普通构建
def _build_dpi_wrap_stub(stub_va, data_va, target_va):
    """位置无关的导出包装桩（stub/data/target 均为首选基址下的 VA）。"""
    pset_va = data_va + DPI_WRAP_PSET
    str1_va = data_va + DPI_WRAP_USER32
    str2_va = data_va + DPI_WRAP_SETNAME
    gmh_va = DPI_WRAP_IAT_GMH
    gpa_va = DPI_WRAP_IAT_GPA
    buf = bytearray()
    disp = []
    rel = []
    marks = {}

    def d32(va):
        disp.append((len(buf), va))
        buf.extend(b'\x00' * 4)

    def r8(mk):
        rel.append((len(buf), mk))
        buf.append(0)

    def mark(mk):
        marks[mk] = len(buf)

    buf += b'\x53\x56'                    # push ebx ; push esi
    buf += b'\xE8\x00\x00\x00\x00'        # call $+5
    base = stub_va + len(buf)             # pop ebx 所在 VA
    buf += b'\x5B'                        # pop ebx
    buf += b'\x8B\x83'; d32(pset_va)      # mov eax,[ebx+pset-base]
    buf += b'\x85\xC0'                    # test eax,eax
    buf += b'\x75'; r8('ctx')             # jne .ctx
    buf += b'\x8D\x83'; d32(str1_va)      # lea eax,[ebx+str1-base]
    buf += b'\x50'                        # push eax
    buf += b'\xFF\x93'; d32(gmh_va)       # call [ebx+gmh-base]  GMH("user32.dll")
    buf += b'\x8B\xF0'                    # mov esi,eax
    buf += b'\x85\xF6'                    # test esi,esi
    buf += b'\x74'; r8('noctx')           # je .noctx
    buf += b'\x8D\x83'; d32(str2_va)      # lea eax,[ebx+str2-base]
    buf += b'\x50'                        # push eax
    buf += b'\x56'                        # push esi
    buf += b'\xFF\x93'; d32(gpa_va)       # call [ebx+gpa-base] GPA(hmod,name)
    buf += b'\x85\xC0'                    # test eax,eax
    buf += b'\x74'; r8('noctx')           # je .noctx
    buf += b'\x89\x83'; d32(pset_va)      # mov [ebx+pset-base],eax
    mark('ctx')
    buf += b'\x8B\x83'; d32(pset_va)      # mov eax,[ebx+pset-base]
    buf += b'\x6A\xFB'                    # push -5（UNAWARE_GDISCALED）
    buf += b'\xFF\xD0'                    # call eax
    buf += b'\x50'                        # push eax（旧上下文）
    buf += b'\xFF\x74\x24\x14'            # push [esp+0x14]（len 副本）
    buf += b'\xFF\x74\x24\x14'            # push [esp+0x14]（h 副本）
    buf += b'\x8D\x8B'; d32(target_va)    # lea ecx,[ebx+target-base]
    buf += b'\xFF\xD1'                    # call ecx
    buf += b'\x83\xC4\x08'                # add esp,8
    buf += b'\x50'                        # push eax（保存返回值）
    buf += b'\x52'                        # push edx
    buf += b'\x8B\x4C\x24\x08'            # mov ecx,[esp+8]（旧上下文）
    buf += b'\x51'                        # push ecx
    buf += b'\xFF\x93'; d32(pset_va)      # call [ebx+pset-base] PSET(旧)
    buf += b'\x5A\x58'                    # pop edx ; pop eax
    buf += b'\x59'                        # pop ecx（丢弃旧上下文槽）
    buf += b'\x5E\x5B'                    # pop esi ; pop ebx
    buf += b'\xC3'                        # ret（调用方清栈）
    mark('noctx')
    buf += b'\xFF\x74\x24\x10'            # push [esp+0x10]（len 副本）
    buf += b'\xFF\x74\x24\x10'            # push [esp+0x10]（h 副本）
    buf += b'\x8D\x8B'; d32(target_va)
    buf += b'\xFF\xD1'                    # call ecx
    buf += b'\x83\xC4\x08'                # add esp,8
    buf += b'\x5E\x5B'                    # pop esi ; pop ebx
    buf += b'\xC3'                        # ret

    for pos, va in disp:
        struct.pack_into('<i', buf, pos, va - base)
    for pos, mk in rel:
        buf[pos] = (marks[mk] - (pos + 1)) & 0xFF
    return bytes(buf)


# 拖动修复：信息窗用 SendMessage(WM_SYSCOMMAND, SC_DRAGMOVE) 拖动（6 个调用点）。
# 窗口被系统虚拟化后，拖动模态循环与线程感知不一致会失效；这里把 6 个
# SendMessageA 调用改指向包装桩：整个调用（含模态拖动循环）期间线程置 GDISCALED，
# 结束后还原。调用点参数为 (hwnd, msg, wparam, lparam) 4 个 dword，桩以 ret 16 返回。
DPI_DRAG_STUB_OFF = 0x2000    # 拖动包装桩（cave 扩到 0x2200 后的新增空间）
DPI_DRAG_SLOT_SM = 0x4B35A8   # SendMessageA 的 IAT 槽（VA）
DPI_DRAG_CALL_ORIG = 0x406F2C  # 原调用目标（SendMessageA 跳板）
DPI_DRAG_CALLS = (0x46326D, 0x46755E, 0x468C56, 0x46D615, 0x46EC75, 0x4704DE)
def _build_drag_wrap_stub(stub_va, data_va):
    pset_va = data_va + DPI_WRAP_PSET
    str1_va = data_va + DPI_WRAP_USER32
    str2_va = data_va + DPI_WRAP_SETNAME
    gmh_va = DPI_WRAP_IAT_GMH
    gpa_va = DPI_WRAP_IAT_GPA
    sm_va = DPI_DRAG_SLOT_SM
    buf = bytearray()
    disp = []
    rel = []
    marks = {}

    def d32(va):
        disp.append((len(buf), va))
        buf.extend(b'\x00' * 4)

    def r8(mk):
        rel.append((len(buf), mk))
        buf.append(0)

    def mark(mk):
        marks[mk] = len(buf)

    buf += b'\x53\x56'
    buf += b'\xE8\x00\x00\x00\x00'
    base = stub_va + len(buf)
    buf += b'\x5B'
    buf += b'\x8B\x83'; d32(pset_va)
    buf += b'\x85\xC0'
    buf += b'\x75'; r8('ctx')
    buf += b'\x8D\x83'; d32(str1_va)
    buf += b'\x50'
    buf += b'\xFF\x93'; d32(gmh_va)
    buf += b'\x8B\xF0'
    buf += b'\x85\xF6'
    buf += b'\x74'; r8('noctx')
    buf += b'\x8D\x83'; d32(str2_va)
    buf += b'\x50'
    buf += b'\x56'
    buf += b'\xFF\x93'; d32(gpa_va)
    buf += b'\x85\xC0'
    buf += b'\x74'; r8('noctx')
    buf += b'\x89\x83'; d32(pset_va)
    mark('ctx')
    buf += b'\x8B\x83'; d32(pset_va)
    buf += b'\x6A\xFB'
    buf += b'\xFF\xD0'
    buf += b'\x50'                        # old
    buf += b'\xFF\x74\x24\x1C'            # pt
    buf += b'\xFF\x74\x24\x1C'            # wparam
    buf += b'\xFF\x74\x24\x1C'            # msg
    buf += b'\xFF\x74\x24\x1C'            # hwnd
    buf += b'\xFF\x93'; d32(sm_va)        # SendMessageA（自动 ret 16）
    buf += b'\x50'
    buf += b'\x52'
    buf += b'\x8B\x4C\x24\x08'
    buf += b'\x51'
    buf += b'\xFF\x93'; d32(pset_va)
    buf += b'\x5A\x58'
    buf += b'\x59'
    buf += b'\x5E\x5B'
    buf += b'\xC2\x10\x00'                # ret 16
    mark('noctx')
    buf += b'\xFF\x74\x24\x18'
    buf += b'\xFF\x74\x24\x18'
    buf += b'\xFF\x74\x24\x18'
    buf += b'\xFF\x74\x24\x18'
    buf += b'\xFF\x93'; d32(sm_va)
    buf += b'\x5E\x5B'
    buf += b'\xC2\x10\x00'

    for pos, va in disp:
        struct.pack_into('<i', buf, pos, va - base)
    for pos, mk in rel:
        buf[pos] = (marks[mk] - (pos + 1)) & 0xFF
    return bytes(buf)


def patch_dpi_drag(data: bytearray) -> bytearray:
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt_size = _u16(data, e + 20)
    opt = e + 24
    sec = opt + opt_size
    cave_rva = cave_raw = None
    for i in range(nsec):
        off = sec + 40 * i
        if bytes(data[off:off + 5]) == b'.cave':
            cave_rva = _u32(data, off + 12)
            cave_raw = _u32(data, off + 20)
    if cave_rva is None:
        raise RuntimeError('拖动包装：找不到 .cave 节')
    stub_va = DPI_WRAP_IB + cave_rva + DPI_DRAG_STUB_OFF
    stub = _build_drag_wrap_stub(stub_va, DPI_WRAP_IB + cave_rva + DPI_WRAP_DATA_OFF)
    if len(stub) > 0x200:
        raise RuntimeError(f'拖动包装：桩过长 {len(stub)}')
    if any(data[cave_raw + DPI_DRAG_STUB_OFF: cave_raw + DPI_DRAG_STUB_OFF + len(stub)]):
        raise RuntimeError('拖动包装：桩位置非空')
    data[cave_raw + DPI_DRAG_STUB_OFF: cave_raw + DPI_DRAG_STUB_OFF + len(stub)] = stub
    for site in DPI_DRAG_CALLS:
        fo = site - 0x400C00
        if data[fo] != 0xE8:
            raise RuntimeError(f'拖动包装：0x{site:X} 不是 call')
        rel = struct.unpack_from('<i', data, fo + 1)[0]
        if site + 5 + rel != DPI_DRAG_CALL_ORIG:
            raise RuntimeError(f'拖动包装：0x{site:X} 调用目标不符')
        struct.pack_into('<i', data, fo + 1, stub_va - (site + 5))
    print(f'拖动修复已应用: {len(DPI_DRAG_CALLS)} 处 SC_DRAGMOVE SendMessage 包装 @ .cave+0x{DPI_DRAG_STUB_OFF:X}')
    return data


# 系统字体初始化包装：应用/主题的系统字体（消息字体等）是在线程 DPI 感知时用
# SystemParametersInfo(SPI_GETNONCLIENTMETRICS) 取到的（144dpi 规格），放进被系统
# 虚拟化的窗口里会再被放大一次（Todo/Notify 状态栏提示字过大）。这里把两处
# “系统字体初始化”调用（0x44E024）改指向包装桩：调用期间线程置 UNAWARE_GDISCALED，
# 取到 96dpi 规格的系统字体，与其它控件一致；结束后还原。
DPI_SYSFONT_ENABLE = True
DPI_SYSFONT_STUB_OFF = 0x2200
DPI_SYSFONT_FUNC = 0x44E024
DPI_SYSFONT_CALLS = (0x44D946, 0x44EEED)


def _build_sysfont_wrap_stub(stub_va, data_va):
    """系统字体初始化包装桩：EAX=对象，无栈参数，原函数裸 ret。"""
    pset_va = data_va + DPI_WRAP_PSET
    str1_va = data_va + DPI_WRAP_USER32
    str2_va = data_va + DPI_WRAP_SETNAME
    gmh_va = DPI_WRAP_IAT_GMH
    gpa_va = DPI_WRAP_IAT_GPA
    buf = bytearray()
    disp = []
    rel = []
    marks = {}

    def d32(va):
        disp.append((len(buf), va))
        buf.extend(b'\x00' * 4)

    def r8(mk):
        rel.append((len(buf), mk))
        buf.append(0)

    def mark(mk):
        marks[mk] = len(buf)

    def call32(va):
        pos = len(buf)
        buf.append(0xE8)
        buf.extend(struct.pack('<i', va - (stub_va + pos + 5)))

    buf += b'\x9C\x60'                    # pushfd ; pushad
    buf += b'\x8B\x6C\x24\x1C'          # mov ebp,[esp+0x1C]（原 eax = 对象）
    buf += b'\xE8\x00\x00\x00\x00'     # call $+5
    base = stub_va + len(buf)             # pop ebx 所在 VA
    buf += b'\x5B'                        # pop ebx
    buf += b'\x8B\x83'; d32(pset_va)      # mov eax,[ebx+pset-base]
    buf += b'\x85\xC0'
    buf += b'\x75'; r8('ctx')
    buf += b'\x8D\x83'; d32(str1_va)      # lea eax,[ebx+str1-base]
    buf += b'\x50'
    buf += b'\xFF\x93'; d32(gmh_va)       # call [ebx+gmh-base] GMH("user32.dll")
    buf += b'\x8B\xF0'                    # mov esi,eax
    buf += b'\x85\xF6'
    buf += b'\x74'; r8('direct')           # je .direct
    buf += b'\x8D\x83'; d32(str2_va)      # lea eax,[ebx+str2-base]
    buf += b'\x50'
    buf += b'\x56'
    buf += b'\xFF\x93'; d32(gpa_va)       # call [ebx+gpa-base] GPA(hmod,name)
    buf += b'\x85\xC0'
    buf += b'\x74'; r8('direct')
    buf += b'\x89\x83'; d32(pset_va)      # mov [ebx+pset-base],eax
    mark('ctx')
    buf += b'\x8B\x83'; d32(pset_va)      # mov eax,[ebx+pset-base]
    buf += b'\x6A\xFB'                    # push -5（UNAWARE_GDISCALED）
    buf += b'\xFF\xD0'                    # call eax
    buf += b'\x8B\xF0'                    # mov esi,eax（旧上下文）
    buf += b'\x8B\xC5'                    # mov eax,ebp（对象）
    call32(DPI_SYSFONT_FUNC)                # call 原函数
    buf += b'\x56'                        # push esi
    buf += b'\x8B\x83'; d32(pset_va)
    buf += b'\xFF\xD0'                    # call eax（PSET(旧)）
    buf += b'\x61\x9D\xC3'               # popad ; popfd ; ret
    mark('direct')
    buf += b'\x8B\xC5'                    # mov eax,ebp
    call32(DPI_SYSFONT_FUNC)
    buf += b'\x61\x9D\xC3'               # popad ; popfd ; ret
    for pos, va in disp:
        struct.pack_into('<i', buf, pos, va - base)
    for pos, mk in rel:
        buf[pos] = (marks[mk] - (pos + 1)) & 0xFF
    return bytes(buf)


def patch_dpi_sysfont(data: bytearray) -> bytearray:
    """把系统字体初始化调用改为包装桩（期间线程 GDISCALED → 96dpi 规格）。"""
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt_size = _u16(data, e + 20)
    opt = e + 24
    sec = opt + opt_size
    cave_rva = cave_raw = None
    for i in range(nsec):
        off = sec + 40 * i
        if bytes(data[off:off + 5]) == b'.cave':
            cave_rva = _u32(data, off + 12)
            cave_raw = _u32(data, off + 20)
    if cave_rva is None:
        raise RuntimeError('系统字体包装：找不到 .cave 节')
    stub_va = DPI_WRAP_IB + cave_rva + DPI_SYSFONT_STUB_OFF
    stub = _build_sysfont_wrap_stub(stub_va, DPI_WRAP_IB + cave_rva + DPI_WRAP_DATA_OFF)
    if len(stub) > 0x200:
        raise RuntimeError(f'系统字体包装：桩过长 {len(stub)}')
    if any(data[cave_raw + DPI_SYSFONT_STUB_OFF: cave_raw + DPI_SYSFONT_STUB_OFF + len(stub)]):
        raise RuntimeError('系统字体包装：桩位置非空')
    data[cave_raw + DPI_SYSFONT_STUB_OFF: cave_raw + DPI_SYSFONT_STUB_OFF + len(stub)] = stub
    for site in DPI_SYSFONT_CALLS:
        fo = site - 0x400C00
        if data[fo] != 0xE8:
            raise RuntimeError(f'系统字体包装：0x{site:X} 不是 call')
        rel = struct.unpack_from('<i', data, fo + 1)[0]
        if site + 5 + rel != DPI_SYSFONT_FUNC:
            raise RuntimeError(f'系统字体包装：0x{site:X} 调用目标不对')
        struct.pack_into('<i', data, fo + 1, stub_va - (site + 5))
    print(f'系统字体包装已应用: {len(DPI_SYSFONT_CALLS)} 处 @ .cave+0x{DPI_SYSFONT_STUB_OFF:X}')
    return data


# 状态栏“改宽度不重绘”原始缺陷修复：VCL 给 TStatusBar 注册的窗口类缺 CS_HREDRAW，
# 改变宽度时系统不整窗失效 → 状态栏旧文字残留（重影/发虚，原版同款）。
# 做法：在 TWinControl.CreateWnd 调用（虚拟）CreateParams 的调用点挂透明小桩：
#   原序列 = mov ecx,[eax]; call [ecx+0x90]（5 字节，位于 0x4352BF）
#   桩内先补完原调用，再判断 CreateWnd 栈帧里刚填好的类名缓冲区（[ebp-0x40]）——
#   仅当 == "TStatusBar" 时给 Params.WindowClass.style（[ebp-0x68]）或上 CS_HREDRAW，
#   然后返回。只影响状态栏类，不碰 RegisterClassA，也不改其它窗口类。
CRPARAMS_HREDRAW_ENABLE = True
CRPARAMS_SITE = 0x4352C1
CRPARAMS_ORIG = bytes.fromhex('FF 91 90 00 00 00')   # call [ecx+0x90]（ecx=虚表，调用方已设）
CRPARAMS_STUB_OFF = 0x22C0


def _build_createparams_stub():
    """CreateParams 调用点透明桩（进入时 eax=控件对象，ecx=虚表，ebp=CreateWnd 栈帧）。"""
    b = bytearray()
    b += b'\xFF\x91\x90\x00\x00\x00'    # call [ecx+0x90]（补回原调用：CreateParams）
    b += b'\x81\x7D\xC0\x54\x53\x74\x61'   # cmp dword [ebp-0x40], "TSta"
    b += b'\x75\x0D'                    # jne .done
    b += b'\x81\x7D\xC4\x74\x75\x73\x42'   # cmp dword [ebp-0x3C], "tusB"
    b += b'\x75\x04'                    # jne .done
    b += b'\x83\x4D\x98\x02'          # or dword [ebp-0x68], 2（CS_HREDRAW）
    b += b'\xC3'                         # ret
    return bytes(b)


def patch_createparams_hredraw(data: bytearray) -> bytearray:
    """给 TStatusBar 的 WindowClass.style 补 CS_HREDRAW（状态栏 resize 不再残留重影）。"""
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt_size = _u16(data, e + 20)
    opt = e + 24
    sec = opt + opt_size
    cave_rva = cave_raw = None
    for i in range(nsec):
        off = sec + 40 * i
        if bytes(data[off:off + 5]) == b'.cave':
            cave_rva = _u32(data, off + 12)
            cave_raw = _u32(data, off + 20)
    if cave_rva is None:
        raise RuntimeError('状态栏类样式：找不到 .cave 节')
    fo = CRPARAMS_SITE - 0x400C00
    if bytes(data[fo:fo + len(CRPARAMS_ORIG)]) != CRPARAMS_ORIG:
        raise RuntimeError(f'状态栏类样式：0x{CRPARAMS_SITE:X} 原始字节不符')
    cave_va = DPI_WRAP_IB + cave_rva
    stub_va = cave_va + CRPARAMS_STUB_OFF
    stub = _build_createparams_stub()
    if any(data[cave_raw + CRPARAMS_STUB_OFF: cave_raw + CRPARAMS_STUB_OFF + len(stub)]):
        raise RuntimeError('状态栏类样式：桩位置非空')
    data[cave_raw + CRPARAMS_STUB_OFF: cave_raw + CRPARAMS_STUB_OFF + len(stub)] = stub
    data[fo:fo + 5] = b'\xE8' + struct.pack('<i', stub_va - (CRPARAMS_SITE + 5))
    data[fo + 5:fo + 6] = b'\x90'       # 原调用是 6 字节：call(5)+NOP(1)（桩尾用 ret 返回）
    print(f'状态栏类样式补丁已应用: CreateParams 调用点 + 仅 TStatusBar 补 CS_HREDRAW')
    return data


def patch_dpi_wrap(data: bytearray) -> bytearray:
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt_size = _u16(data, e + 20)
    opt = e + 24
    sec = opt + opt_size
    cave_rva = cave_raw = None
    for i in range(nsec):
        off = sec + 40 * i
        if bytes(data[off:off + 5]) == b'.cave':
            cave_rva = _u32(data, off + 12)
            cave_raw = _u32(data, off + 20)
    if cave_rva is None:
        raise RuntimeError('DPI 包装：找不到 .cave 节')

    def rva_off(rva):
        for i in range(nsec):
            off = sec + 40 * i
            va = _u32(data, off + 12)
            vsz = _u32(data, off + 8)
            raw = _u32(data, off + 20)
            rsz = _u32(data, off + 16)
            if va <= rva < va + max(vsz, rsz):
                return raw + (rva - va)
        raise RuntimeError(f'DPI 包装：RVA 0x{rva:X} 不在任何节内')

    ed_rva = _u32(data, opt + 96)
    if ed_rva == 0:
        raise RuntimeError('DPI 包装：无导出表')
    eo = rva_off(ed_rva)
    nnam = _u32(data, eo + 24)
    afn = _u32(data, eo + 28)
    anm = _u32(data, eo + 32)
    aord = _u32(data, eo + 36)
    found = {}
    for i in range(nnam):
        no = rva_off(_u32(data, rva_off(anm) + 4 * i))
        end = no
        while data[end] != 0:
            end += 1
        nm = bytes(data[no:end]).decode('latin1')
        if nm in ('load', 'request'):
            ordi = _u16(data, rva_off(aord) + 2 * i)
            found[nm] = (ordi, _u32(data, rva_off(afn) + 4 * ordi))
    if set(found) != {'load', 'request'}:
        raise RuntimeError(f'DPI 包装：导出缺失 {found}')

    # 数据区（PSET 初始为 0）
    blob = b'\x00' * 4 + b'user32.dll\x00' + b'\x00' * (DPI_WRAP_SETNAME - DPI_WRAP_USER32 - 11) \
        + b'SetThreadDpiAwarenessContext\x00'
    data[cave_raw + DPI_WRAP_DATA_OFF: cave_raw + DPI_WRAP_DATA_OFF + len(blob)] = blob

    targets = (('request', DPI_WRAP_REQ_OFF), ('load', DPI_WRAP_LOAD_OFF))
    for nm, off_ in targets:
        ordi, frva = found[nm]
        sv = DPI_WRAP_IB + cave_rva + off_
        dv = DPI_WRAP_IB + cave_rva + DPI_WRAP_DATA_OFF
        tv = DPI_WRAP_IB + frva
        stub = _build_dpi_wrap_stub(sv, dv, tv)
        limit = (0x1F8 if nm == 'request' else 0x2000)
        if len(stub) > limit - off_:
            raise RuntimeError(f'DPI 包装：{nm} 桩过长 {len(stub)}')
        if any(data[cave_raw + off_: cave_raw + off_ + len(stub)]):
            raise RuntimeError(f'DPI 包装：{nm} 桩位置非空')
        data[cave_raw + off_: cave_raw + off_ + len(stub)] = stub
        struct.pack_into('<I', data, rva_off(afn) + 4 * ordi, cave_rva + off_)

    print(f'高分屏缩放已应用: load/request 导出包装 @ 0x{DPI_WRAP_LOAD_OFF:X}/0x{DPI_WRAP_REQ_OFF:X}')
    return data


# ------------------------------------------------------------- AITXT 加密
# 算法：1) 整块反转
#       2) 与密钥流异或：keystream = MT19937(seed2) rand(0x7FFFFFFF) & 0xFF
#       seed2 = 以 9821 为种子的 MT19937 取 Random(0x7FFFFFFF) 后，
#               对其十进制字符串做 MD5，取十六进制结果中前 9 个数字字符

class _MT:
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


def _seed2():
    mt = _MT(0x265D)
    r1 = mt.rand(0x7FFFFFFF)
    digits = ''.join(c for c in hashlib.md5(
        str(r1).encode('ascii')).hexdigest() if c.isdigit())
    if not digits:
        return mt.rand(0x109A0)
    return int(digits[:9] if len(digits) >= 10 else digits)


def _keystream(n):
    mt = _MT(_seed2())
    return bytes(mt.rand(0x7FFFFFFF) & 0xFF for _ in range(n))


def aitxt_encrypt(plain):
    return bytes(b ^ s for b, s in zip(plain, _keystream(len(plain))))[::-1]


def aitxt_decrypt(res):
    return bytes(b ^ s for b, s in zip(res[::-1], _keystream(len(res))))


def gbk_bytes(text):
    """UTF-8 字符串 -> GBK 字节；GBK 装不下的字符先做 NFKC 再试。"""
    out = bytearray()
    bad = 0
    for ch in text:
        try:
            out += ch.encode('gbk')
            continue
        except UnicodeEncodeError:
            pass
        alt = unicodedata.normalize('NFKC', ch)
        if len(alt) == 1:
            try:
                out += alt.encode('gbk')
                continue
            except UnicodeEncodeError:
                pass
        out += b'?'
        bad += 1
    if bad:
        print(f'警告: {bad} 个字符无法编码为 GBK，已替换为 ?')
    return bytes(out)


# ------------------------------------------------------------- PE 定位

def _u16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def find_aitxt(data):
    """返回 (数据块文件偏移, 大小, Size 字段文件偏移)。"""
    e_lfanew = _u32(data, 0x3C)
    coff = e_lfanew + 4
    nsec = _u16(data, coff + 2)
    opt_size = _u16(data, coff + 16)
    opt = coff + 20
    res_rva = _u32(data, opt + 96 + 2 * 8)
    sec = opt + opt_size
    sections = [(_u32(data, sec + 40 * i + 12), _u32(data, sec + 40 * i + 8),
                 _u32(data, sec + 40 * i + 20), _u32(data, sec + 40 * i + 16))
                for i in range(nsec)]

    def rva_to_off(rva):
        for va, vsize, raw, rawsize in sections:
            if va <= rva < va + max(vsize, rawsize):
                return raw + (rva - va)
        raise RuntimeError(f'RVA 0x{rva:X} not mapped')

    base = rva_to_off(res_rva)

    def entries(dir_off):
        total = _u16(data, base + dir_off + 12) + _u16(data, base + dir_off + 14)
        for i in range(total):
            e = base + dir_off + 16 + 8 * i
            yield _u32(data, e), _u32(data, e + 4)

    def name_of(field):
        if field & 0x80000000:
            p = base + (field & 0x7FFFFFFF)
            n = _u16(data, p)
            return data[p + 2:p + 2 + n * 2].decode('utf-16-le', 'replace')
        return field

    for t_name, t_sub in entries(0):
        if name_of(t_name) != 'AITXT' or not (t_sub & 0x80000000):
            continue
        for i_name, i_sub in entries(t_sub & 0x7FFFFFFF):
            if name_of(i_name) != 101 or not (i_sub & 0x80000000):
                continue
            for _l_name, l_sub in entries(i_sub & 0x7FFFFFFF):
                data_rva, size = _u32(data, base + l_sub), _u32(data, base + l_sub + 4)
                return rva_to_off(data_rva), size, base + l_sub + 4
    raise RuntimeError('AITXT resource not found')


def patch_aitxt(data: bytearray) -> bytearray:
    text_path = os.path.join(BASE, 'aitxt_translated.txt')
    if not os.path.exists(text_path):
        raise RuntimeError(f'{text_path} 不存在，请先运行 build_from_csv.py')
    text = open(text_path, encoding='utf-8', newline='').read()
    raw = gbk_bytes(text)

    blob_off, slot_size, size_field = find_aitxt(bytes(data))
    blob = aitxt_encrypt(raw)
    if len(blob) > slot_size:
        raise RuntimeError(
            f'AITXT 超出资源槽位: {len(blob)} > {slot_size} 字节（需要 PE 手术）')
    if aitxt_decrypt(blob) != raw:
        raise RuntimeError('AITXT round-trip check failed')

    data[blob_off:blob_off + len(blob)] = blob
    data[blob_off + len(blob):blob_off + slot_size] = b'\x00' * (slot_size - len(blob))
    struct.pack_into('<I', data, size_field, len(blob))
    n_lines = len(raw.split(b'\n'))
    print(f'AITXT 已写入: {slot_size} -> {len(blob)} 字节 '
          f'(slot @0x{blob_off:X}, {n_lines} 行, round-trip OK)')
    return data


# ============================================================================
# 退出崩溃修复层（定稿：EAT 重定向 + DllMain detach 归还）
# ----------------------------------------------------------------------------
# 背景：SSP 退出/重载时，first.dll 卸载后残留的"僵尸活动"（窗口消息派发、收尾
#       遗留调用）会执行到已卸载模块的代码上（0x1476a/0x7474 一族）；而提前
#       处理（在模块自身收尾/存档前断路）又会破坏收尾流程（0x2E44 一族）。
#
# 现行方案（全同步，无定时器/线程/轮询）：
#   1) 入口保持原始字节；把 unload 导出经 EAT 重定向到存根 —— SSP 的调用进入
#      存根（模块内部对 0xAA234 的直接调用仍走原函数，无需接管）；
#   2) 存根：线程切 UNAWARE_GDISCALED(-5)；旧上下文存暂存槽并 SetPropA 写入
#      SSPMAIN 的 "dpictx" 属性；pushal/popal 间销毁两个注册窗体对象（析构
#      触发原生存档）；随后 call 原函数完好入口并等其返回；
#   3) 收尾例程：只做 EnumWindows 断路（WndProc 在模块范围内的窗口换成
#      DefWindowProcA）。【不做 DPI 恢复】——线程保持 -5 直到卸载完成：
#      这样 detach 期间内部清理/内部调用触发的"迟到存档"也读虚拟坐标
#      （否则存档尺寸 ×1.5——务必不要提前恢复）；
#   4) 归还发生在 DllMain 的 DLL_PROCESS_DETACH 末尾（V7，入口 detour）：
#      原 DllMain 跑完（迟到存档至此全部结束）后，从 SSPMAIN 的 "dpictx"
#      属性读回旧上下文归还。触发点绑定在"本模块自己的卸载"上——重载、
#      切到别的 SHIORI 人格、退出 SSP 全都覆盖（不依赖"下一次 load"）。
#
# 布局（.cave 固定偏移）：
#   0x2400  存根（开头先清扫一轮 + 切-5/存属性 + 双销毁 + call 完好入口 + 跳收尾）
#   0x226A  收尾例程（只断路；不恢复 DPI）
#   0x7500  枚举回调（断路：本模块范围 + 监控窗类名）
#           0x7600 类名缓冲 / 0x7660 "Tanalogclockform" / 0x7680 "Tcpuloadform"
#   0x23F4  上下文暂存槽 4B（存根写；供 SetPropA 转存窗口属性）
#   0x2640  V7 归还桩 / 0x2700 V7 入口跳板（DllMain detach 归还）
#   0x2600  窗口类名 / 0x2630 属性名 "dpictx"（卸载存根与归还桩共用）
# ============================================================================
EXITFIX_ENABLE = True
EXITFIX_STUB_OFF = 0x2400        # 存根
EXITFIX_POST_OFF = 0x226A        # 收尾例程（teardown 返回后执行）
EXITFIX_CB_OFF   = 0x7500        # 枚举回调（断路：本模块范围 + 监控窗类名）
EXITFIX_CB2_BUF  = 0x7600        # 类名缓冲（64B -> 0x763F）
EXITFIX_CB2_TA   = 0x7660        # "Tanalogclockform\0"（17B）
EXITFIX_CB2_TI   = 0x7680        # "Tcpuloadform\0"（13B）
EXITFIX_PSET_OFF = 0xB0          # .cave 高分屏数据区 +0x00：PSET 指针槽（包装桩惰性解析）
EXITFIX_CTX_STASH = 0x23F4       # 旧 DPI 上下文暂存槽（存根写；供 SetPropA 转存 SSPMAIN 属性）
EXITFIX_UNLOAD_RVA    = 0xAA234  # 原 unload 入口 RVA（入口保持原样；EAT 重定向到存根）
EXITFIX_UNLOAD_PROLOG = bytes.fromhex('55 8B EC 51 53')   # 原 unload 入口序言

_EXF_IAT_GWL    = 0xB3664        # GetWindowLongA
_EXF_IAT_SWL    = 0xB3568        # SetWindowLongA
_EXF_IAT_DEFWND = 0xB3764        # DefWindowProcA
_EXF_IAT_GCL    = 0xB36F0        # GetClassNameA
_EXF_IAT_ENUMW  = 0xB3714        # EnumWindows
_EXF_FREE_THUNK = 0x402E40       # TObject.Free 跳板
_EXF_SLOT_A     = 0x4B08CC       # 注册窗体槽 A（Tnotifyform）
_EXF_VMT_A      = 0x464280
_EXF_SLOT_B     = 0x4B298C       # 注册窗体槽 B（Tfirstconfigform）
_EXF_VMT_B      = 0x468DC8
_EXF_EXPECT_CAVE_RVA = 0xE2000   # .cave 期望 RVA（add_cave_section 的固定结果）

# —— V7：DllMain DLL_PROCESS_DETACH 结束时归还 DPI 上下文（人格切换场景）——
ENTRY_RVA = 0xAC704              # 原 DllMain 入口 RVA
ENTRY_PROLOG = bytes.fromhex('55 8B EC 83 C4 B4')
CTXDETACH_STUB_OFF = 0x2640      # 归还桩
CTXDETACH_TRAMP_OFF = 0x2700     # 入口跳板（原序言 6 字节 + 跳回入口+6）


def _exitfix_stub(stub_va: int, cave_va: int) -> bytes:
    """存根（卸载导出目标）：先按类名清扫监控窗（防残留的 WndProc 桩被收尾消息打到），
    再切请求态 DPI 上下文 + 销毁槽 A/B + call teardown + 跳收尾例程。"""
    anchor = stub_va + 5          # call 在偏移 0（无 pushal 前置），返回址 = 起点+5
    def L(va):                    # 绝对 VA 相对锚点的位移
        return struct.pack('<i', va - anchor)
    def C(off):                   # .cave 内偏移 -> 位移
        return L(cave_va + off)
    b = bytearray()
    b += b'\xE8\x00\x00\x00\x00\x5B'                    # call $+5; pop ebx（锚点，先于 pushal）
    # —— 键盘修复：卸载前先摘 WH_GETMESSAGE 钩子（钩子回调在本模块内，不摘则卸载后崩）——
    if KBD_ENABLE:
        b += b'\x8B\x8B' + C(KBD_HHK)                   # mov ecx,[hhk]
        b += b'\x85\xC9'                                # test ecx,ecx
        _k1 = len(b); b += b'\x0F\x84\x00\x00\x00\x00'  # je .kend（本就没装）
        b += b'\x8B\x83' + C(KBD_UNHOOK)                # mov eax,[真 UnhookWindowsHookEx]
        b += b'\x85\xC0'                                # test eax,eax
        _k2 = len(b); b += b'\x0F\x84\x00\x00\x00\x00'  # je .clr（API 未解析）
        b += b'\x51'                                    # push ecx
        b += b'\xFF\xD0'                                # call eax
        _kclr = len(b)
        b += b'\xC7\x83' + C(KBD_HHK) + b'\x00\x00\x00\x00'  # mov [hhk],0
        _kend = len(b)
        struct.pack_into('<i', b, _k1 + 2, _kend - (_k1 + 6))
        struct.pack_into('<i', b, _k2 + 2, _kclr - (_k2 + 6))
    b += b'\x8D\x83' + C(EXITFIX_POST_OFF)              # lea eax,[收尾例程]
    b += b'\xFF\xD0'                                    # call eax（卸载一开始先清扫一轮）
    # —— 线程 DPI 上下文切 UNAWARE_GDISCALED(-5)：析构触发的存档将读到
    #    96dpi 虚拟坐标（与手动关窗一致，修退出存档尺寸 ×1.5）；旧上下文
    #    先存槽，由收尾例程恢复（teardown 也在 GDISCALED 下运行，其存档坐标一致）。
    #    槽为空（旧系统无该 API，包装桩未解析出指针）时整段跳过。 ——
    b += b'\x8B\x83' + C(EXITFIX_PSET_OFF)              # mov eax,[ebx+pset槽]
    b += b'\x85\xC0'                                    # test eax,eax
    b += b'\x74\x0A'                                    # je .skip（跳过 10 字节）
    b += b'\x6A\xFB'                                    # push -5（UNAWARE_GDISCALED）
    b += b'\xFF\xD0'                                    # call eax（PSET；返回旧上下文）
    b += b'\x89\x83' + C(EXITFIX_CTX_STASH)             # mov [ebx+暂存槽],eax
    # --- 旧上下文经暂存槽写入 SSPMAIN 的 "dpictx" 属性（卸载期临时保存，供 V7 归还桩读回）---
    b += b'\x60'                                      # pushad
    b += b'\x6A\x00'                                  # push 0
    b += b'\x8D\x83' + C(0x2600)                     # lea eax,[class]
    b += b'\x50'                                      # push eax
    b += b'\xFF\x93' + L(0x4B3704)                   # call [FindWindowA]
    b += b'\x85\xC0'                                  # test
    _j1 = len(b); b += b'\x74\x00'                    # jz .out
    b += b'\x8B\x93' + C(EXITFIX_CTX_STASH)          # mov edx,[stash]
    b += b'\x52'                                      # push edx
    b += b'\x8D\x93' + C(0x2630)                     # lea edx,[prop]
    b += b'\x52'                                      # push edx
    b += b'\x50'                                      # push eax (hwnd)
    b += b'\xFF\x93' + L(0x4B3580)                   # call [SetPropA]
    _out1 = len(b)
    b[_j1 + 1] = (_out1 - (_j1 + 2)) & 0xFF
    b += b'\x61'                                      # popad
    # .skip:
    b += b'\x60'                                        # pushal
    # —— 双销毁（保存机制）：槽A/槽B VMT 校验后 TObject.Free + 置空 ——
    for _slot, _vmt in ((_EXF_SLOT_A, _EXF_VMT_A), (_EXF_SLOT_B, _EXF_VMT_B)):
        b += b'\x8B\x83' + L(_slot)                      # mov eax,[槽]
        b += b'\x85\xC0'                                 # test
        b += b'\x74\x1E'                                 # je 跳过本槽
        b += b'\x8B\x08'                                 # mov ecx,[eax]
        b += b'\x8D\x93' + L(_vmt)                       # lea edx,[期望VMT]
        b += b'\x3B\xCA'                                 # cmp
        b += b'\x75\x12'                                 # jne 跳过 Free
        b += b'\x8D\x93' + L(_EXF_FREE_THUNK)                   # lea edx,[TObject.Free 跳板]
        b += b'\xFF\xD2'                                 # call edx
        b += b'\xC7\x83' + L(_slot) + b'\x00\x00\x00\x00'  # mov [槽],0
    # —— 尾部：popal + 重放序言 + call teardown（等返回）+ 跳收尾例程。
    #    DPI 上下文（UNAWARE_GDISCALED）保持到收尾例程：teardown 自身销毁
    #    窗体触发的存档（时钟/CPU 等）同样读到 96dpi 虚拟坐标，与手动关窗一致。 ——
    b += b'\x61'                                        # popal
    # call 原函数完好入口（序言+函数体）。入口保持原始字节，切勿 call 到被改过的入口（递归）
    b += b'\xE8' + struct.pack('<i', (0x400000 + EXITFIX_UNLOAD_RVA) - (stub_va + len(b) + 5))
    here = stub_va + len(b)
    b += b'\xE9' + struct.pack('<i', (cave_va + EXITFIX_POST_OFF) - (here + 5))
    assert len(b) <= 0x100, len(b)
    b += bytes(0x100 - len(b))
    return bytes(b)


def _exitfix_post(cave_va: int) -> bytes:
    """收尾例程：EnumWindows 断路一轮 -> 交还 SSP（不做 DPI 恢复，见 V7）。"""
    pv = cave_va + EXITFIX_POST_OFF
    def L(va):
        return struct.pack('<i', va - pv)
    b = bytearray()
    b += b'\x60'                                        # pushal
    b += b'\xE8\x00\x00\x00\x00\x5B\x81\xEB' + struct.pack('<I', 6)   # ebx = 起点
    # 不在收尾恢复 DPI 上下文（关键）：线程保持 -5，直到 DllMain 的
    # DLL_PROCESS_DETACH 末尾由 V7 归还桩统一归还。这样 detach 期间模块
    # 内部的清理/存档也读虚拟坐标（修存档尺寸 ×1.5）；归还点与"下一个
    # 加载谁"解耦（重载/切人格/退出全覆盖，防整体放大）。
    # .skip:
    b += b'\x6A\x00'                                    # push 0 （lParam）
    b += b'\x8D\x83' + L(cave_va + EXITFIX_CB_OFF)      # lea eax,[ebx+cb]
    b += b'\x50'                                        # push eax（回调）
    b += b'\xFF\x93' + L(0x400000 + _EXF_IAT_ENUMW)     # call [EnumWindows]
    b += b'\x61\xC3'                                    # popal; ret（栈顶即 SSP 返回址）
    assert len(b) <= EXITFIX_CB_OFF - EXITFIX_POST_OFF, len(b)
    return bytes(b)


def _exitfix_cb(cave_va: int) -> bytes:
    """枚举回调：WndProc 落在本模块 [base, base+0x100000) 内，或窗口类名为
    Tanalogclockform / Tcpuloadform（不依赖范围，残留死桩也能清）→ 断路。"""
    cv = cave_va + EXITFIX_CB_OFF
    def L(va):
        return struct.pack('<i', va - cv)
    b = bytearray()
    ji = []
    marks = []

    def raw(x):
        b.extend(x)

    def jrel8(op, label):
        raw(bytes([op, 0])); ji.append((len(b) - 1, label, 1))

    def mark(label):
        marks.append((label, len(b)))

    raw(b'\x60')                                       # pushal
    raw(b'\xE8\x00\x00\x00\x00\x5B\x81\xEB')    # call$+5; pop ebx; sub ebx,6
    raw(struct.pack('<I', 6))                           # ebx = 回调起点
    raw(b'\xFC')                                       # cld
    raw(b'\x8B\x6C\x24\x24')                        # mov ebp,[esp+0x24]（hwnd）
    raw(b'\x6A\xFC\x55')
    raw(b'\xFF\x93'); raw(L(0x400000 + _EXF_IAT_GWL))  # GWL(hwnd,-4)
    raw(b'\x85\xC0')
    jrel8(0x74, 'clschk')
    raw(b'\x8B\xF0')                                   # esi=W
    raw(b'\x8B\xC3\x2D'); raw(struct.pack('<I', cave_va + EXITFIX_CB_OFF - 0x400000))
    raw(b'\x8B\xD0')                                   # edx=base
    raw(b'\x3B\xF2')
    jrel8(0x72, 'clschk')
    raw(b'\x81\xC2\x00\x00\x10\x00')
    raw(b'\x3B\xF2')
    jrel8(0x73, 'clschk')
    jrel8(0xEB, 'swap')
    mark('clschk')
    raw(b'\x6A\x3C')                                   # push 60
    raw(b'\x8D\x83'); raw(L(cave_va + EXITFIX_CB2_BUF))
    raw(b'\x50\x55')                                   # push buf; push hwnd
    raw(b'\xFF\x93'); raw(L(0x400000 + _EXF_IAT_GCL))  # GetClassNameA
    raw(b'\x83\xF8\x10')                              # cmp eax,16
    jrel8(0x75, 'ck2')
    raw(b'\x8D\xB3'); raw(L(cave_va + EXITFIX_CB2_BUF))
    raw(b'\x8D\xBB'); raw(L(cave_va + EXITFIX_CB2_TA))
    raw(b'\xB9\x10\x00\x00\x00')                    # mov ecx,16
    raw(b'\xF3\xA6')                                   # repe cmpsb
    jrel8(0x74, 'swap')
    mark('ck2')
    raw(b'\x83\xF8\x0C')                              # cmp eax,12
    jrel8(0x75, 'done')
    raw(b'\x8D\xB3'); raw(L(cave_va + EXITFIX_CB2_BUF))
    raw(b'\x8D\xBB'); raw(L(cave_va + EXITFIX_CB2_TI))
    raw(b'\xB9\x0C\x00\x00\x00')
    raw(b'\xF3\xA6')
    jrel8(0x75, 'done')
    mark('swap')
    raw(b'\xFF\xB3'); raw(L(0x400000 + _EXF_IAT_DEFWND))  # push [DefWindowProcA]
    raw(b'\x6A\xFC\x55')
    raw(b'\xFF\x93'); raw(L(0x400000 + _EXF_IAT_SWL))     # SetWindowLongA
    mark('done')
    raw(b'\x61\xB8\x01\x00\x00\x00\xC2\x08\x00')   # popad; mov eax,1; ret 8
    mk = dict(marks)
    for pos, label, sz in ji:
        off = mk[label] - (pos + sz)
        if sz == 1:
            assert -128 <= off <= 127, (label, off)
            b[pos] = off & 0xFF
        else:
            struct.pack_into('<i', b, pos, off)
    assert len(b) <= 0x120, len(b)
    return bytes(b)


# ------------------------------------------------------------- first.dll（ULW 命中掩码）
# 幽灵的透明监控窗（模拟时钟 Tanalogclockform / 文字信息窗 Tcpuloadform）是
# WS_EX_LAYERED + UpdateLayeredWindow 逐像素 alpha 窗口：系统按图层 alpha 做鼠标
# 命中判定（alpha==0 穿透）。100% 缩放下判定与显示一一对应；DPI 虚拟化（≠100%）下
# 系统读取图层的坐标被放大 2 倍（实测：鼠标在显示坐标 V 处读图层 V/2 处，偏移为 0），
# 命中区与显示错位（细笔画点不中、空白处出现幽灵命中区）。本层在每次 ULW 上屏前，
# 仅对这两个窗口的 32bpp 图层写“不可见命中掩码”（alpha=1/255）：
#   =100%：不做任何修改（与未打补丁的原版行为一致）；
#   ≠100%：掩码[U,·] = 内容[2U,·]（横竖各缩半，抵消系统的 2× 采样；DIB 自底向上，
#           行号翻转；只把 alpha==0 的像素置 1，不改可见像素）。
# 实现：4 处 ULW 调用点（VA 0x462C90/0x462CB8/0x462D2A/0x462D50，原 9 字节
# `mov eax,[表]; mov eax,[eax]; call eax`）替换为 `call 桩` + 4×NOP。
# 关键：被替换的操作数带 PE HIGHLOW 重定位项，必须同时从 .reloc 删除对应条目，
# 否则加载器把 rebase 差值加到补丁字节上 → 调用乱飞（实测崩溃过）。
# 同层附：跨 100% 缩放刷新修复——上屏桩每帧核对并（重）挂监控窗的 WndProc 子类化
# （子类化到本模块 WndProc 桩 _wl_wndproc）；穿越 100% 时 cloak + WS_EX_LAYERED
# 往返，待解除标记由上屏桩处理。
ULW_SITES = (0x462C90, 0x462CB8, 0x462D2A, 0x462D50)
ULW_SITE_ORIG = bytes.fromhex('A1A0DD4A008B00FFD0')
ULW_TABLE = 0x4ADDA0                # 表项（运行时指向 ULW 指针槽；尾调保留两级间接）
ULWFIX_STUB_OFF = 0x7700            # .cave：ULW 前置桩（≤0x360）
ULWFIX_SLOT_OFF = 0x7D00            # GetCurrentObject 指针槽（惰性解析）
ULWFIX_STDA_SLOT = 0x7D04           # SetThreadDpiAwarenessContext 指针槽
ULWFIX_GWR_SLOT = 0x7D08            # GetWindowRect 指针槽
ULWFIX_OLD_CTX = 0x7D0C             # 旧 DPI 上下文暂存
ULWFIX_MOD_U32 = 0x7D20             # user32 模块句柄缓存（解析 STDA/GWR 用）
ULWFIX_STR_GDI32 = 0x7D30           # "gdi32.dll"
ULWFIX_STR_GCO = 0x7D40             # "GetCurrentObject"
ULWFIX_DS_OFF = 0x7D50              # GetObjectA 输出（BITMAP，84B）
ULWFIX_KLS_BUF = 0x7DB0             # GetClassNameA 缓冲（64B）
ULWFIX_RECT = 0x7DF0                # GetWindowRect 输出
ULWFIX_BITS = 0x7E00                # 工作变量：像素指针
ULWFIX_STRIDE = 0x7E04              # 行字节
ULWFIX_W = 0x7E08                   # 图层宽
ULWFIX_H = 0x7E0C                   # 图层高
ULWFIX_X = 0x7E10                   # 扫描 x
ULWFIX_Y = 0x7E14                   # 扫描 y
ULWFIX_ROW = 0x7E18                 # 当前行指针
ULWFIX_PW = 0x7E1C                  # 窗口物理宽（PMv2 采样）
ULWFIX_PH = 0x7E20                  # 窗口物理高
ULWFIX_STR_USER32 = 0x7F00          # "user32.dll"
ULWFIX_STR_STDA = 0x7F10            # "SetThreadDpiAwarenessContext"
ULWFIX_STR_GWR = 0x7F40             # "GetWindowRect"
ULWFIX_IAT_GETHMOD = 0x4B31E4       # kernel32!GetModuleHandleA（first.dll IAT）
ULWFIX_IAT_GETPROC = 0x4B31E0       # kernel32!GetProcAddress
ULWFIX_IAT_GETOBJ = 0x4B349C        # gdi32!GetObjectA

# —— 跨 100% 缩放刷新修复（first.dll 侧）——
#  上屏桩每帧核对并（重）挂监控窗 WndProc 子类化到 WL WP 桩（_wl_wndproc）；穿越
#  100% 时该桩 cloak + WS_EX_LAYERED 往返刷新，待解除标记由上屏桩处理。索引 0=时钟 1=信息。
WL_DATA_OFF = 0x7A60                # WL 数据区（≤0xA0）
WL_WP_OFF = 0x7B00                  # WL WndProc 桩（≤0x200）
WL_OLD1 = 0x7A60                    # 旧 WndProc 槽（0=未安装）[idx*4]
WL_LASTW1 = 0x7A68                  # 上次物理宽 [idx*4]
WL_HWND1 = 0x7A70                   # 已安装句柄 [idx*4]
WL_DWM_PTR = 0x7A78                 # dwmapi!DwmSetWindowAttribute（懒解析）
WL_LIB_PTR = 0x7A7C                 # kernel32!LoadLibraryA（懒解析兜底）
WL_STYLE = 0x7A80                   # 样式暂存（WndProc）
WL_CLKBOOL = 0x7A84                 # cloak BOOL（WndProc）
WL_UCLKBOOL = 0x7A88                # 解除 cloak BOOL（上屏桩）
WL_CTX2 = 0x7A8C                    # WndProc 线程尖峰旧上下文暂存
WL_W1 = 0x7A90                      # 本窗层宽阈值 [idx*4]（64/148）
WL_FLAG1 = 0x7A98                   # 待解除 cloak（byte）[idx]
WL_ARMED1 = 0x7A9A                  # 校准位（byte）[idx]
WL_STR_DWMAPI = 0x7AA0              # "dwmapi.dll"
WL_STR_DWMSWA = 0x7AB0              # "DwmSetWindowAttribute"
WL_STR_K32 = 0x7AD0                 # "kernel32.dll"
WL_STR_LLA = 0x7AE0                 # "LoadLibraryA"
WL_CLOCK_W = 64                     # 时钟窗层宽（阈值写入 WL_W1/W2）
WL_INFO_W = 148                     # 信息窗层宽


def _ulwfix_stub(cave_va: int) -> bytes:
    """ULW 前置桩：取 hdcSrc 的图层位图；仅对监控窗 32bpp 图层，在 ≠100% 缩放时写
    命中掩码（1/255 不可见）；=100% 直通。所有间接访问都用绝对静态 VA 计算位移
    （桩静态 VA = cave_va + ULWFIX_STUB_OFF）。"""
    sv = cave_va + ULWFIX_STUB_OFF

    def C(va):
        return struct.pack('<i', va - (sv + 6))          # 基址 = pushad 后 call$+5 返回值

    def CS(off):
        return C(cave_va + off)

    b = bytearray()
    ji = []
    marks = {}

    def raw(x):
        b.extend(x)

    def jcc(cc, label):                                  # 近跳（0F 8x rel32）
        b.extend(bytes([0x0F, cc])); b.extend(b'\x00' * 4)
        ji.append((len(b) - 4, label, 4))

    def jmpE(label):
        b.extend(b'\xE9\x00\x00\x00\x00'); ji.append((len(b) - 4, label, 4))

    def mark(label):
        marks[label] = len(b)

    raw(b'\x60')                                        # pushad
    raw(b'\xE8\x00\x00\x00\x00\x5B')                    # call$+5; pop ebx
    raw(b'\x8B\x74\x24\x34')                            # esi = hdcSrc（[esp+0x34]）
    raw(b'\x85\xF6')
    jcc(0x84, 'done')
    raw(b'\x8B\x83'); raw(CS(ULWFIX_SLOT_OFF))          # GCO 槽已解析？
    raw(b'\x85\xC0')
    jcc(0x85, 'have')
    raw(b'\x8D\x83'); raw(CS(ULWFIX_STR_GDI32)); raw(b'\x50')
    raw(b'\xFF\x93'); raw(C(ULWFIX_IAT_GETHMOD))        # GMH("gdi32.dll")
    raw(b'\x85\xC0')
    jcc(0x84, 'done')
    raw(b'\x8B\xD0')
    raw(b'\x8D\x83'); raw(CS(ULWFIX_STR_GCO)); raw(b'\x50\x52')
    raw(b'\xFF\x93'); raw(C(ULWFIX_IAT_GETPROC))        # GPA("GetCurrentObject")
    raw(b'\x89\x83'); raw(CS(ULWFIX_SLOT_OFF))
    raw(b'\x85\xC0')
    jcc(0x84, 'done')
    mark('have')
    raw(b'\x6A\x07'); raw(b'\x56')                      # GetCurrentObject(hdcSrc, OBJ_BITMAP=7)
    raw(b'\xFF\x93'); raw(CS(ULWFIX_SLOT_OFF))
    raw(b'\x85\xC0')
    jcc(0x84, 'done')
    raw(b'\x8D\x93'); raw(CS(ULWFIX_DS_OFF))
    raw(b'\x52'); raw(b'\x6A\x54'); raw(b'\x50')        # GetObjectA(hbmp, 84, &ds)
    raw(b'\xFF\x93'); raw(C(ULWFIX_IAT_GETOBJ))
    raw(b'\x85\xC0')
    jcc(0x84, 'done')
    raw(b'\x8D\x93'); raw(CS(ULWFIX_DS_OFF))
    raw(b'\x0F\xB7\x42\x12'); raw(b'\x83\xF8\x20')      # bpp == 32 ?
    jcc(0x85, 'done')
    raw(b'\x8B\x6C\x24\x24')                            # ebp = hwnd（[esp+0x24]）
    raw(b'\x85\xED')
    jcc(0x84, 'done')
    raw(b'\x8D\x83'); raw(CS(ULWFIX_KLS_BUF))           # GetClassNameA(hwnd, buf, 64)
    raw(b'\x6A\x40'); raw(b'\x50'); raw(b'\x55')
    raw(b'\xFF\x93'); raw(C(0x400000 + _EXF_IAT_GCL))   # _EXF_IAT_* 是 RVA
    raw(b'\x8D\x93'); raw(CS(ULWFIX_KLS_BUF))
    raw(b'\x8B\x02')
    raw(b'\x3D'); raw(struct.pack('<I', 0x616E6154))    # "Tana"
    jcc(0x85, 'kls2')
    raw(b'\x80\x7A\x04\x6C')                            # cmp byte [edx+4],'l'
    jcc(0x84, 'mon')
    mark('kls2')
    raw(b'\x3D'); raw(struct.pack('<I', 0x75706354))    # "Tcpu"
    jcc(0x85, 'done')
    raw(b'\x80\x7A\x04\x6C')
    jcc(0x85, 'done')
    mark('mon')
    raw(b'\x31\xFF')                                    # edi=0（时钟）/1（信息）
    raw(b'\x3D'); raw(struct.pack('<I', 0x616E6154))    # cmp eax,"Tana"
    jcc(0x84, 'idx_ok')
    raw(b'\xBF\x01\x00\x00\x00')                        # mov edi,1
    mark('idx_ok')
    raw(b'\x8D\x93'); raw(CS(ULWFIX_DS_OFF))            # 载入位图信息
    raw(b'\x8B\x42\x14'); raw(b'\x89\x83'); raw(CS(ULWFIX_BITS))
    raw(b'\x8B\x42\x0C'); raw(b'\x89\x83'); raw(CS(ULWFIX_STRIDE))
    raw(b'\x8B\x42\x04'); raw(b'\x89\x83'); raw(CS(ULWFIX_W))
    raw(b'\x8B\x42\x08'); raw(b'\x89\x83'); raw(CS(ULWFIX_H))
    # —— DPI 因子采样：PMv2 下取窗口物理宽；解析失败按 100% 直通 ——
    raw(b'\x8B\x83'); raw(CS(ULWFIX_STDA_SLOT))
    raw(b'\x85\xC0')
    jcc(0x85, 'have_stda')
    raw(b'\x8D\x83'); raw(CS(ULWFIX_STR_USER32)); raw(b'\x50')
    raw(b'\xFF\x93'); raw(C(ULWFIX_IAT_GETHMOD))
    raw(b'\x85\xC0')
    jcc(0x84, 'done')
    raw(b'\x89\x83'); raw(CS(ULWFIX_MOD_U32))           # 缓存 user32 模块句柄
    raw(b'\x8D\x83'); raw(CS(ULWFIX_STR_STDA)); raw(b'\x50')
    raw(b'\xFF\xB3'); raw(CS(ULWFIX_MOD_U32))
    raw(b'\xFF\x93'); raw(C(ULWFIX_IAT_GETPROC))
    raw(b'\x89\x83'); raw(CS(ULWFIX_STDA_SLOT))
    raw(b'\x85\xC0')
    jcc(0x84, 'done')
    raw(b'\x8D\x83'); raw(CS(ULWFIX_STR_GWR)); raw(b'\x50')
    raw(b'\xFF\xB3'); raw(CS(ULWFIX_MOD_U32))
    raw(b'\xFF\x93'); raw(C(ULWFIX_IAT_GETPROC))
    raw(b'\x89\x83'); raw(CS(ULWFIX_GWR_SLOT))
    raw(b'\x85\xC0')
    jcc(0x84, 'done')
    mark('have_stda')
    raw(b'\x6A\xFC')                                    # push -4（PER_MONITOR_AWARE_V2）
    raw(b'\xFF\x93'); raw(CS(ULWFIX_STDA_SLOT))
    raw(b'\x89\x83'); raw(CS(ULWFIX_OLD_CTX))           # 保存旧上下文
    raw(b'\x8D\x83'); raw(CS(ULWFIX_RECT)); raw(b'\x50'); raw(b'\x55')
    raw(b'\xFF\x93'); raw(CS(ULWFIX_GWR_SLOT))          # GetWindowRect(hwnd,&rect)
    raw(b'\xFF\xB3'); raw(CS(ULWFIX_OLD_CTX))
    raw(b'\xFF\x93'); raw(CS(ULWFIX_STDA_SLOT))         # 恢复上下文
    raw(b'\x8D\x93'); raw(CS(ULWFIX_RECT))
    raw(b'\x8B\x42\x08'); raw(b'\x2B\x02')
    raw(b'\x89\x83'); raw(CS(ULWFIX_PW))
    raw(b'\x8B\x42\x0C'); raw(b'\x2B\x42\x04')
    raw(b'\x89\x83'); raw(CS(ULWFIX_PH))
    # —— 子类化安装/重挂：每帧核对当前 WndProc（被引擎重设或窗口重建都能自愈）——
    raw(b'\x6A\xFC\x55')
    raw(b'\xFF\x93'); raw(C(0x400000 + _EXF_IAT_GWL))   # eax=当前过程
    raw(b'\x85\xC0')
    jcc(0x84, 'inst_done')                              # 拿不到 → 跳过
    raw(b'\x8D\x93'); raw(CS(WL_WP_OFF))                # edx=本模块 WndProc
    raw(b'\x39\xD0')                                    # cmp eax,edx
    jcc(0x84, 'inst_done')                              # 已是本模块过程 → 不动
    raw(b'\x89\xAC\xBB'); raw(CS(WL_HWND1))             # [HWND]=hwnd
    raw(b'\x89\x84\xBB'); raw(CS(WL_OLD1))              # [OLD]=当前过程（尾跳链）
    raw(b'\x52\x6A\xFC\x55')
    raw(b'\xFF\x93'); raw(C(0x400000 + _EXF_IAT_SWL))
    mark('inst_done')
    raw(b'\x80\xBC\x3B'); raw(CS(WL_FLAG1)); raw(b'\x00')   # cmp byte[FLAG],0
    jcc(0x84, 'ucl_done')
    raw(b'\xC6\x84\x3B'); raw(CS(WL_FLAG1)); raw(b'\x00')   # FLAG=0
    raw(b'\x8B\x8B'); raw(CS(WL_DWM_PTR))
    raw(b'\x85\xC9')
    jcc(0x84, 'ucl_done')
    raw(b'\xC7\x83'); raw(CS(WL_UCLKBOOL)); raw(b'\x00' * 4)
    raw(b'\x6A\x04')
    raw(b'\x8D\x83'); raw(CS(WL_UCLKBOOL)); raw(b'\x50')
    raw(b'\x6A\x0D\x55')
    raw(b'\xFF\xD1')                                    # DwmSetWindowAttribute(hwnd,13,&0,4)
    mark('ucl_done')
    raw(b'\x8B\x83'); raw(CS(ULWFIX_PW))                # eax=物理宽（安装块会破坏 eax，置后载入）
    raw(b'\x3B\x83'); raw(CS(ULWFIX_W))
    jcc(0x84, 'done')                                   # 100% → 直通
    # —— ≠100%：掩码[U, ·] = 内容[2U, ·]（U=x>>1；行号缩半后翻回自底向上）——
    raw(b'\xC7\x83'); raw(CS(ULWFIX_Y)); raw(b'\x00\x00\x00\x00')
    mark('yloop')
    raw(b'\x8B\x83'); raw(CS(ULWFIX_Y))
    raw(b'\x3B\x83'); raw(CS(ULWFIX_H))
    jcc(0x83, 'done')
    raw(b'\x0F\xAF\x83'); raw(CS(ULWFIX_STRIDE))
    raw(b'\x03\x83'); raw(CS(ULWFIX_BITS))
    raw(b'\x89\x83'); raw(CS(ULWFIX_ROW))
    raw(b'\xC7\x83'); raw(CS(ULWFIX_X)); raw(b'\x00\x00\x00\x00')
    mark('xloop')
    raw(b'\x8B\x83'); raw(CS(ULWFIX_X))
    raw(b'\x3B\x83'); raw(CS(ULWFIX_W))
    jcc(0x83, 'ynext')
    raw(b'\x8B\xC8')                                    # ecx = x
    raw(b'\xC1\xE1\x02')                                # shl ecx,2
    raw(b'\x03\x8B'); raw(CS(ULWFIX_ROW))               # add ecx,[row]
    raw(b'\x8B\x11')                                    # edx = [ecx]（像素原值）
    raw(b'\xF7\xC2\xFF\xFF\xFF\x00')                    # test edx,0x00FFFFFF
    jcc(0x85, 'content')
    raw(b'\x8A\x51\x03')                                # mov dl,[ecx+3]
    raw(b'\x80\xFA\x02')                                # cmp dl,2
    jcc(0x82, 'next_x')
    mark('content')
    raw(b'\x8B\x83'); raw(CS(ULWFIX_X))
    raw(b'\xD1\xE8')                                    # shr eax,1 → U
    raw(b'\x8B\x93'); raw(CS(ULWFIX_H))                 # edx = H−1−y
    raw(b'\x4A')
    raw(b'\x2B\x93'); raw(CS(ULWFIX_Y))
    raw(b'\xD1\xEA')                                    # shr edx,1 → t
    raw(b'\x8B\x8B'); raw(CS(ULWFIX_H))                 # ecx = H−1−t
    raw(b'\x49')
    raw(b'\x2B\xCA')
    raw(b'\x0F\xAF\x8B'); raw(CS(ULWFIX_STRIDE))        # imul ecx, stride
    raw(b'\x03\x8B'); raw(CS(ULWFIX_BITS))              # add ecx, bits
    raw(b'\xC1\xE0\x02')                                # shl eax,2（U*4）
    raw(b'\x03\xC1')                                    # add eax, ecx
    raw(b'\x80\x78\x03\x00')                            # cmp byte [eax+3],0
    jcc(0x85, 'next_x')
    raw(b'\xC6\x40\x03\x01')                            # mov byte [eax+3],1
    mark('next_x')
    raw(b'\xFF\x83'); raw(CS(ULWFIX_X))
    jmpE('xloop')
    mark('ynext')
    raw(b'\xFF\x83'); raw(CS(ULWFIX_Y))
    jmpE('yloop')
    mark('done')
    raw(b'\x61')                                        # popad（EBX 被恢复，旧基址失效）
    P = len(b)
    raw(b'\xE8\x00\x00\x00\x00\x5B')                    # 重新锚定
    raw(b'\x8B\x83'); raw(struct.pack('<i', ULW_TABLE - (sv + P + 5)))
    raw(b'\x8B\x00')                                    # 表项 → 槽
    raw(b'\xFF\xE0')                                    # jmp eax（尾调真实 ULW）
    for pos, label, size in ji:
        off = marks[label] - (pos + size)
        if size == 1:
            assert -128 <= off <= 127, (label, off)
            b[pos] = off & 0xFF
        else:
            struct.pack_into('<i', b, pos, off)
    assert len(b) <= 0x360, len(b)
    return bytes(b)


def _wl_wndproc(stub_va: int, cave_va: int) -> bytes:
    """监控窗 WndProc 桩（first.dll）：WM_WINDOWPOSCHANGED 时按窗采物理宽（线程尖峰
    PMv2；STDA/GWR 槽由 ULW 桩预先解析），门控“上次 ≤ 层宽 → 本次 > 层宽”（向上穿越
    100%）→ DWM cloak + WS_EX_LAYERED 去/加往返刷新本窗；待解除 cloak 标记由 ULW
    上屏桩处理。窗口索引由 hwnd 判定（0=时钟 1=信息）。保护 EBX/ESI/EDI，尾跳旧过程。"""
    sv = stub_va

    def C(va):
        return struct.pack('<i', va - (sv + 8))          # 锚点 = 起点+8（push×3 + call$+5/pop）

    def CS(off):
        return C(cave_va + off)

    b = bytearray()
    ji = []
    marks = {}

    def raw(x):
        b.extend(x)

    def jcc(cc, label):
        b.extend(bytes([0x0F, cc])); b.extend(b'\x00' * 4)
        ji.append((len(b) - 4, label, 4))

    def jmpE(label):
        b.extend(b'\xE9\x00\x00\x00\x00'); ji.append((len(b) - 4, label, 4))

    def mark(label):
        marks[label] = len(b)

    raw(b'\x53\x56\x57')                            # push ebx/esi/edi
    raw(b'\xE8\x00\x00\x00\x00\x5B')                # call$+5; pop ebx
    raw(b'\x8B\x74\x24\x10')                        # esi = hwnd
    raw(b'\x31\xFF')                                # edi=0（时钟）/1（信息）：任意消息先定索引
    raw(b'\x39\xB3'); raw(CS(WL_HWND1))             # （非 0x47 直跳尾部的路径也要用）
    jcc(0x84, 'idx_ok')
    raw(b'\xBF\x01\x00\x00\x00')                    # mov edi,1
    mark('idx_ok')
    raw(b'\x83\x7C\x24\x14\x47')                    # cmp [esp+0x14],WM_WINDOWPOSCHANGED
    jcc(0x85, 'tail')
    raw(b'\x6A\xFC')                                # push -4（PER_MONITOR_AWARE_V2）
    raw(b'\xFF\x93'); raw(CS(ULWFIX_STDA_SLOT))
    raw(b'\x89\x83'); raw(CS(WL_CTX2))              # 旧上下文暂存
    raw(b'\x83\xEC\x10')                            # sub esp,0x10（栈上 RECT）
    raw(b'\x8B\xD4')                                # mov edx,esp
    raw(b'\x52\x56')                                # push edx; push esi
    raw(b'\xFF\x93'); raw(CS(ULWFIX_GWR_SLOT))
    raw(b'\x8B\x44\x24\x08')                        # eax = right
    raw(b'\x2B\x44\x24\x00')                        # eax -= left
    raw(b'\x83\xC4\x10')                            # add esp,0x10
    # 注意：旧上下文还原放到尾部（还原调用会破坏 EAX=宽度，必须在宽度用完后再还原）
    raw(b'\x8B\x94\xBB'); raw(CS(WL_W1))            # edx = 本窗层宽阈值
    raw(b'\x80\xBC\x3B'); raw(CS(WL_ARMED1)); raw(b'\x00')   # cmp byte[ARMED],0
    jcc(0x85, 'armed_ok')
    raw(b'\x3B\xC2')                                # cmp eax,edx
    jcc(0x86, 'arm_store')                          # jbe
    raw(b'\xC6\x84\x3B'); raw(CS(WL_ARMED1)); raw(b'\x01')   # ARMED=1
    mark('arm_store')
    raw(b'\x89\x84\xBB'); raw(CS(WL_LASTW1))        # LASTW=phys
    jmpE('tail')
    mark('armed_ok')
    raw(b'\x8B\x8C\xBB'); raw(CS(WL_LASTW1))        # ecx = LASTW
    raw(b'\x3B\xCA')                                # cmp ecx,edx
    jcc(0x87, 'keep')                               # ja
    raw(b'\x3B\xC2')                                # cmp eax,edx
    jcc(0x86, 'keep')                               # jbe
    raw(b'\x89\x84\xBB'); raw(CS(WL_LASTW1))
    raw(b'\x8B\x8B'); raw(CS(WL_DWM_PTR))           # —— DWM 懒解析 ——
    raw(b'\x85\xC9')
    jcc(0x85, 'dwmrdy')
    raw(b'\x8D\x83'); raw(CS(WL_STR_DWMAPI)); raw(b'\x50')
    raw(b'\xFF\x93'); raw(C(ULWFIX_IAT_GETHMOD))
    raw(b'\x85\xC0')
    jcc(0x85, 'gotdll')
    raw(b'\x8B\x8B'); raw(CS(WL_LIB_PTR))
    raw(b'\x85\xC9')
    jcc(0x85, 'have_lib')
    raw(b'\x8D\x83'); raw(CS(WL_STR_K32)); raw(b'\x50')
    raw(b'\xFF\x93'); raw(C(ULWFIX_IAT_GETHMOD))
    raw(b'\x85\xC0')
    jcc(0x84, 'dwmrdy')
    raw(b'\x8B\xD0')
    raw(b'\x8D\x83'); raw(CS(WL_STR_LLA)); raw(b'\x50\x52')
    raw(b'\xFF\x93'); raw(C(ULWFIX_IAT_GETPROC))
    raw(b'\x85\xC0')
    jcc(0x84, 'dwmrdy')
    raw(b'\x89\x83'); raw(CS(WL_LIB_PTR))
    mark('have_lib')
    raw(b'\x8D\x83'); raw(CS(WL_STR_DWMAPI)); raw(b'\x50')
    raw(b'\xFF\x93'); raw(CS(WL_LIB_PTR))
    raw(b'\x85\xC0')
    jcc(0x84, 'dwmrdy')
    mark('gotdll')
    raw(b'\x8B\xD0')
    raw(b'\x8D\x83'); raw(CS(WL_STR_DWMSWA)); raw(b'\x50\x52')
    raw(b'\xFF\x93'); raw(C(ULWFIX_IAT_GETPROC))
    raw(b'\x89\x83'); raw(CS(WL_DWM_PTR))
    mark('dwmrdy')
    raw(b'\x8B\x8B'); raw(CS(WL_DWM_PTR))           # —— cloak（解析失败则跳过）——
    raw(b'\x85\xC9')
    jcc(0x84, 'ncmk')
    raw(b'\xC7\x83'); raw(CS(WL_CLKBOOL)); raw(b'\x01\x00\x00\x00')
    raw(b'\x6A\x04')
    raw(b'\x8D\x83'); raw(CS(WL_CLKBOOL)); raw(b'\x50')
    raw(b'\x6A\x0D'); raw(b'\x56')
    raw(b'\xFF\xD1')
    mark('ncmk')
    raw(b'\xC6\x84\x3B'); raw(CS(WL_FLAG1)); raw(b'\x01')    # FLAG=1（待上屏解除）
    raw(b'\x6A\xEC\x56')                            # —— WS_EX_LAYERED 去/加往返 ——
    raw(b'\xFF\x93'); raw(C(0x400000 + _EXF_IAT_GWL))
    raw(b'\x89\x83'); raw(CS(WL_STYLE))
    raw(b'\x8B\xD0')
    raw(b'\x81\xE2\xFF\xFF\xF7\xFF')                # and edx,~WS_EX_LAYERED
    raw(b'\x52\x6A\xEC\x56')
    raw(b'\xFF\x93'); raw(C(0x400000 + _EXF_IAT_SWL))
    raw(b'\x8B\x93'); raw(CS(WL_STYLE))
    raw(b'\x52\x6A\xEC\x56')
    raw(b'\xFF\x93'); raw(C(0x400000 + _EXF_IAT_SWL))
    jmpE('tail')
    mark('keep')
    raw(b'\x89\x84\xBB'); raw(CS(WL_LASTW1))
    mark('tail')
    raw(b'\x8B\x8B'); raw(CS(WL_CTX2))              # 旧上下文（仅 0x47 路径设置过）
    raw(b'\x85\xC9')
    jcc(0x84, 'tail2')
    raw(b'\x51')
    raw(b'\xFF\x93'); raw(CS(ULWFIX_STDA_SLOT))     # 还原（此时宽度已用完）
    raw(b'\xC7\x83'); raw(CS(WL_CTX2)); raw(b'\x00' * 4)
    mark('tail2')
    raw(b'\x8B\x84\xBB'); raw(CS(WL_OLD1))          # eax = 旧 WndProc
    raw(b'\x5F\x5E\x5B')
    raw(b'\xFF\xE0')
    for pos, label, size in ji:
        off = marks[label] - (pos + size)
        if size == 1:
            assert -128 <= off <= 127, (label, off)
            b[pos] = off & 0xFF
        else:
            struct.pack_into('<i', b, pos, off)
    assert len(b) <= 0x200, len(b)
    return bytes(b)


def patch_ulw_hitmask(data: bytearray) -> bytearray:
    """写入 ULW 桩/WndProc 桩/数据、挂钩 4 个 ULW 调用点，并删除站点操作数的 PE 重定位项。"""
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt = e + 24
    opt_size = _u16(data, e + 20)
    sec = opt + opt_size
    cave_rva = cave_raw = None
    for i in range(nsec):
        off = sec + 40 * i
        if bytes(data[off:off + 5]) == b'.cave':
            cave_rva = _u32(data, off + 12)
            cave_raw = _u32(data, off + 20)
    if cave_rva is None:
        raise RuntimeError('ulwfix：未找到 .cave 段')
    cave_va = 0x400000 + cave_rva

    def rva_off(rva):
        for i in range(nsec):
            off = sec + 40 * i
            va = _u32(data, off + 12)
            vsz = _u32(data, off + 8)
            if va <= rva < va + vsz:
                return _u32(data, off + 20) + (rva - va)
        raise RuntimeError('ulwfix：RVA 0x%X 不在任何节' % rva)

    if any(data[cave_raw + ULWFIX_STUB_OFF:cave_raw + WL_DATA_OFF]):
        raise RuntimeError('ulwfix：桩区非零')
    if any(data[cave_raw + WL_DATA_OFF:cave_raw + WL_WP_OFF]):
        raise RuntimeError('ulwfix：WL 数据区非零')
    if any(data[cave_raw + WL_WP_OFF:cave_raw + ULWFIX_SLOT_OFF]):
        raise RuntimeError('ulwfix：WndProc 桩区非零')
    if any(data[cave_raw + ULWFIX_SLOT_OFF:cave_raw + 0x7F60]):
        raise RuntimeError('ulwfix：数据区非零')

    stub = _ulwfix_stub(cave_va)
    data[cave_raw + ULWFIX_STUB_OFF:cave_raw + ULWFIX_STUB_OFF + len(stub)] = stub
    wp = _wl_wndproc(cave_va + WL_WP_OFF, cave_va)
    data[cave_raw + WL_WP_OFF:cave_raw + WL_WP_OFF + len(wp)] = wp
    data[cave_raw + WL_W1:cave_raw + WL_W1 + 8] = struct.pack('<II', WL_CLOCK_W, WL_INFO_W)
    for off, s in ((ULWFIX_STR_GDI32, b'gdi32.dll\x00'),
                   (ULWFIX_STR_GCO, b'GetCurrentObject\x00'),
                   (ULWFIX_STR_USER32, b'user32.dll\x00'),
                   (ULWFIX_STR_STDA, b'SetThreadDpiAwarenessContext\x00'),
                   (ULWFIX_STR_GWR, b'GetWindowRect\x00'),
                   (WL_STR_DWMAPI, b'dwmapi.dll\x00'),
                   (WL_STR_DWMSWA, b'DwmSetWindowAttribute\x00'),
                   (WL_STR_K32, b'kernel32.dll\x00'),
                   (WL_STR_LLA, b'LoadLibraryA\x00')):
        data[cave_raw + off:cave_raw + off + len(s)] = s

    # 站点操作数带 HIGHLOW 重定位项：补丁取代后必须删除，否则加载器会把 rebase
    # 差值加到补丁字节上（rel32 加歪 → 调用乱飞）。重建 .reloc 并更新目录大小。
    site_rvas = [s - 0x400000 for s in ULW_SITES]
    rel_raw = rel_rsize = None
    for i in range(nsec):
        off = sec + 40 * i
        if bytes(data[off:off + 7]) == b'.reloc\x00':
            rel_rsize = _u32(data, off + 16)
            rel_raw = _u32(data, off + 20)
    if rel_raw is None:
        raise RuntimeError('ulwfix：未找到 .reloc 段')
    blk = rel_raw
    end = rel_raw + rel_rsize
    out = bytearray()
    removed = 0
    while blk + 8 <= end:
        page = _u32(data, blk)
        bsize = _u32(data, blk + 4)
        if page == 0 or bsize < 8:
            break
        keep = []
        for k in range((bsize - 8) // 2):
            w = _u16(data, blk + 8 + 2 * k)
            r = page + (w & 0xFFF)
            if any(s <= r < s + 9 for s in site_rvas):
                removed += 1
            else:
                keep.append(w)
        if keep:
            out += struct.pack('<II', page, 8 + 2 * len(keep))
            for w in keep:
                out += struct.pack('<H', w)
        blk += bsize
    if removed != len(site_rvas):
        raise RuntimeError('ulwfix：预期移除 %d 条重定位，实际 %d' % (len(site_rvas), removed))
    data[rel_raw:rel_raw + len(out)] = out
    data[rel_raw + len(out):rel_raw + rel_rsize] = b'\x00' * (rel_rsize - len(out))
    struct.pack_into('<I', data, opt + 96 + 5 * 8 + 4, len(out))

    for site_va in ULW_SITES:
        off = rva_off(site_va - 0x400000)
        if bytes(data[off:off + 9]) != ULW_SITE_ORIG:
            raise RuntimeError('ulwfix：0x%X 原始字节不符 %s' % (site_va, data[off:off + 9].hex()))
        rel = (cave_va + ULWFIX_STUB_OFF) - (site_va + 5)
        data[off] = 0xE8
        struct.pack_into('<i', data, off + 1, rel)
        data[off + 5:off + 9] = b'\x90' * 4
    print('ULW 命中掩码层已应用：%d 处挂钩 @ .cave+0x%X；移除站点重定位 %d 条；'
          '跨 100%% 刷新 WndProc @ .cave+0x%X' % (
              len(ULW_SITES), ULWFIX_STUB_OFF, removed, WL_WP_OFF))
    return data


def patch_exit_fix(data: bytearray) -> bytearray:
    """应用退出崩溃修复层。在 patch_dpi_wrap / patch_aitxt 之后调用。"""
    if not EXITFIX_ENABLE:
        return data
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt = e + 24
    opt_size = _u16(data, e + 20)
    sec = opt + opt_size
    cave_rva = cave_raw = None
    for i in range(nsec):
        off = sec + 40 * i
        if bytes(data[off:off + 5]) == b'.cave':
            cave_rva = _u32(data, off + 12)
            cave_raw = _u32(data, off + 20)
    if cave_rva is None:
        raise RuntimeError('exitfix：未找到 .cave 段')
    if cave_rva != _EXF_EXPECT_CAVE_RVA:
        raise RuntimeError('exitfix：.cave RVA=0x%X 与预期 0x%X 不符' % (cave_rva, _EXF_EXPECT_CAVE_RVA))
    cave_va = 0x400000 + cave_rva

    def rva_off(rva):
        for i in range(nsec):
            off = sec + 40 * i
            va = _u32(data, off + 12)
            vsz = _u32(data, off + 8)
            if va <= rva < va + vsz:
                return _u32(data, off + 20) + (rva - va)
        raise RuntimeError('exitfix：RVA 0x%X 不在任何节' % rva)

    # —— 区域预检（防踩踏；.cave 新附录区应为全零）——
    def expect_zero(off, ln, what):
        if any(data[cave_raw + off:cave_raw + off + ln]):
            raise RuntimeError('exitfix：%s @cave+0x%X 非零，疑似布局冲突' % (what, off))
    expect_zero(EXITFIX_POST_OFF, 0x22C0 - EXITFIX_POST_OFF, '收尾例程区')  # 0x22C0 起为既有桩，避开
    expect_zero(EXITFIX_CB_OFF, 0x200, '回调区(含类名缓冲/字符串)')
    expect_zero(EXITFIX_CTX_STASH, 4, '上下文暂存槽')
    if any(data[cave_raw + EXITFIX_STUB_OFF:cave_raw + EXITFIX_STUB_OFF + 0x100]):
        raise RuntimeError('exitfix：存根区 @cave+0x%X 非零' % EXITFIX_STUB_OFF)

    # —— 写入本层四段 ——
    d_stub = _exitfix_stub(cave_va + EXITFIX_STUB_OFF, cave_va)
    data[cave_raw + EXITFIX_STUB_OFF:cave_raw + EXITFIX_STUB_OFF + len(d_stub)] = d_stub
    d_post = _exitfix_post(cave_va)
    data[cave_raw + EXITFIX_POST_OFF:cave_raw + EXITFIX_POST_OFF + len(d_post)] = d_post
    d_cb = _exitfix_cb(cave_va)
    data[cave_raw + EXITFIX_CB_OFF:cave_raw + EXITFIX_CB_OFF + len(d_cb)] = d_cb
    data[cave_raw + EXITFIX_CB2_TA:cave_raw + EXITFIX_CB2_TA + 17] = b'Tanalogclockform\x00'
    data[cave_raw + EXITFIX_CB2_TI:cave_raw + EXITFIX_CB2_TI + 13] = b'Tcpuloadform\x00'

    # —— 原 unload 入口（函数体第一条指令处）：内联跳转 -> 存根 ——
    off_u = rva_off(EXITFIX_UNLOAD_RVA)
    if bytes(data[off_u:off_u + 5]) != EXITFIX_UNLOAD_PROLOG:
        raise RuntimeError('exitfix：unload 入口序言不符 %s' % data[off_u:off_u + 5].hex())
    # 导出表重定向（入口保持原始字节；存根要 call 完好入口，二者必须搭配）
    _eu = _u32(data, opt + 96)
    _efu = rva_off(_eu)
    _afu = _u32(data, _efu + 28)
    _fou = rva_off(_afu)
    if _u32(data, _fou) != EXITFIX_UNLOAD_RVA:
        raise RuntimeError('exitfix: unload EAT != entry, got 0x%X' % _u32(data, _fou))
    struct.pack_into('<I', data, _fou, cave_rva + EXITFIX_STUB_OFF)
    print('exitfix: unload EAT -> cave+0x%X（入口保持原样）' % EXITFIX_STUB_OFF)

    # —— 说明：不改导出表 EAT。SSP 经导出表调用到原入口 0xAA234，
    #         再由上面的入口内联跳转进入存根（与验证版设计一致）。
    # —— 归还统一由 V7（patch_ctx_return_on_detach，DllMain detach 归还）负责 ——
    # load 导出保持指向 load 包装（cave+0x1F7C）；此处只写归还所需的类名/属性名。
    data[cave_raw + 0x2600: cave_raw + 0x2600 + 45] = b'SSPMAIN-3145fdab-2ee0-4158-a1ce-832b553ad790\x00'
    data[cave_raw + 0x2630: cave_raw + 0x2630 + 7] = b'dpictx\x00'
    _er = _u32(data, opt + 96)
    _efo = rva_off(_er)
    _efun = rva_off(_u32(data, _efo + 28))
    if _u32(data, _efun + 1 * 4) != cave_rva + 0x1F7C:
        raise RuntimeError('load EAT 应为 load 包装 0x1F7C，实为 0x%X' % _u32(data, _efun + 1 * 4))
    print('退出崩溃修复层已应用: 存根(含DPI上下文切换)+收尾+断路 (cave+0x%X/0x%X/0x%X)，unload 入口内联跳转指向存根'
          % (EXITFIX_STUB_OFF, EXITFIX_POST_OFF, EXITFIX_CB_OFF))
    return data


def patch_ctx_return_on_detach(data: bytearray) -> bytearray:
    """V7：DllMain 的 DLL_PROCESS_DETACH 走完后归还线程 DPI 上下文。

    归还必须同时满足两点：①晚于所有"迟到存档"（否则存档 ×1.5）；
    ②不依赖"下一次加载谁"（否则切换人格时线程残留 GDISCALED(-5)，
    之后新建的窗口/UI 全被 GDI 放大）。
    DLL_PROCESS_DETACH 末尾正好同时满足：它是本模块卸载的最后一刻
    （原 DllMain 已跑完、存档已结束），且卸载必然触发、与下一个加载者无关。
    实现：入口 detour —— pushad 后以调用方式执行原 DllMain（保存返回值），
    reason==0 时从 SSPMAIN 的 "dpictx" 属性读回旧上下文并归还。"""
    import struct as _st
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt = e + 24
    opt_size = _u16(data, e + 20)
    sec = opt + opt_size
    cave_rva = cave_raw = None
    for i in range(nsec):
        off = sec + 40 * i
        if bytes(data[off:off + 5]) == b'.cave':
            cave_rva = _u32(data, off + 12)
            cave_raw = _u32(data, off + 20)
    if cave_rva != _EXF_EXPECT_CAVE_RVA:
        raise RuntimeError('ctxdetach：.cave 不符')
    cave_va = 0x400000 + cave_rva

    def rva_off(rva):
        for i in range(nsec):
            off = sec + 40 * i
            va = _u32(data, off + 12)
            vsz = _u32(data, off + 8)
            if va <= rva < va + vsz:
                return _u32(data, off + 20) + (rva - va)
        raise RuntimeError('ctxdetach：RVA 0x%X 不在节' % rva)

    if any(data[cave_raw + CTXDETACH_STUB_OFF:cave_raw + CTXDETACH_TRAMP_OFF]):
        raise RuntimeError('ctxdetach：桩区 @cave+0x%X 非零' % CTXDETACH_STUB_OFF)
    if any(data[cave_raw + CTXDETACH_TRAMP_OFF:cave_raw + CTXDETACH_TRAMP_OFF + 0x10]):
        raise RuntimeError('ctxdetach：跳板区 @cave+0x%X 非零' % CTXDETACH_TRAMP_OFF)
    efo = rva_off(ENTRY_RVA)
    if bytes(data[efo:efo + len(ENTRY_PROLOG)]) != ENTRY_PROLOG:
        raise RuntimeError('ctxdetach：入口序言不符 %s' % bytes(data[efo:efo + 8]).hex(' '))

    stub_va = cave_va + CTXDETACH_STUB_OFF
    tramp_va = cave_va + CTXDETACH_TRAMP_OFF
    entry_va = 0x400000 + ENTRY_RVA
    b = bytearray()
    fix = []
    lab = {}

    def LAB(n):
        lab[n] = len(b)

    def RJ(op, n):
        b.append(op); fix.append((len(b), n)); b.append(0)

    anchor = stub_va + 5

    def AL(va):
        return _st.pack('<i', va - anchor)

    def C(off):
        return AL(cave_va + off)

    b += b'\xE8\x00\x00\x00\x00\x5B'                    # call $+5; pop ebx
    b += b'\x60'                                        # pushad
    b += b'\xFF\x74\x24\x2C'                            # push [esp+0x2C] lpReserved
    b += b'\xFF\x74\x24\x2C'                            # push [esp+0x2C] fdwReason
    b += b'\xFF\x74\x24\x2C'                            # push [esp+0x2C] hinst
    b += b'\x8D\x83' + C(CTXDETACH_TRAMP_OFF)           # lea eax,[跳板]
    b += b'\xFF\xD0'                                    # call eax（原 DllMain 全程）
    b += b'\x89\x44\x24\x1C'                            # mov [esp+0x1C],eax（保存返回值）
    b += b'\x8B\x44\x24\x28'                            # mov eax,[esp+0x28] fdwReason
    b += b'\x85\xC0'                                    # test eax,eax
    RJ(0x75, 'done')                                    # jnz .done（非 DETACH 跳过）
    # —— 归还 DPI 上下文（与 load 包装桩相同的调用序列）——
    b += b'\x8B\x83' + C(EXITFIX_PSET_OFF)              # mov eax,[pset]
    b += b'\x85\xC0'
    RJ(0x75, 'have')                                    # jnz .have
    b += b'\x8D\x83' + C(0xB4)                          # lea eax,[user32.dll]
    b += b'\x50'
    b += b'\xFF\x93' + AL(0x4B31E4)                     # call [GMH]
    b += b'\x85\xC0'
    RJ(0x74, 'out')                                     # jz .out
    b += b'\x8D\x93' + C(0xC0)                          # lea edx,[SetThreadDpiAwarenessContext]
    b += b'\x52\x50'
    b += b'\xFF\x93' + AL(0x4B31E0)                     # call [GPA]
    b += b'\x85\xC0'
    RJ(0x74, 'out')
    b += b'\x89\x83' + C(EXITFIX_PSET_OFF)              # mov [pset],eax
    LAB('have')
    b += b'\x6A\x00'                                    # push 0
    b += b'\x8D\x83' + C(0x2600)                        # lea eax,[类名]
    b += b'\x50'
    b += b'\xFF\x93' + AL(0x4B3704)                     # call [FindWindowA]
    b += b'\x85\xC0'
    RJ(0x74, 'out')
    b += b'\x8D\x93' + C(0x2630)                        # lea edx,[属性名]
    b += b'\x52\x50'
    b += b'\xFF\x93' + AL(0x4B368C)                     # call [GetPropA]
    b += b'\x85\xC0'
    RJ(0x74, 'out')
    b += b'\x8B\x93' + C(EXITFIX_PSET_OFF)              # mov edx,[pset]
    b += b'\x85\xD2'
    RJ(0x74, 'out')
    b += b'\x50'                                        # push eax（旧上下文）
    b += b'\xFF\xD2'                                    # call edx（归还）
    LAB('out')
    LAB('done')
    b += b'\x61'                                        # popad
    b += b'\xC2\x0C\x00'                                # ret 12
    for pos, name in fix:
        off = lab[name] - (pos + 1)
        if not (-128 <= off <= 127):
            raise RuntimeError('ctxdetach：短跳超出范围 %s' % name)
        b[pos] = off & 0xFF
    if len(b) > CTXDETACH_TRAMP_OFF - CTXDETACH_STUB_OFF:
        raise RuntimeError('ctxdetach：桩过长 %d' % len(b))
    data[cave_raw + CTXDETACH_STUB_OFF:cave_raw + CTXDETACH_STUB_OFF + len(b)] = b
    tramp = ENTRY_PROLOG + b'\xE9' + _st.pack('<i', (entry_va + len(ENTRY_PROLOG)) - (tramp_va + len(ENTRY_PROLOG) + 5))
    data[cave_raw + CTXDETACH_TRAMP_OFF:cave_raw + CTXDETACH_TRAMP_OFF + len(tramp)] = tramp
    data[efo:efo + 6] = b'\xE9' + _st.pack('<i', stub_va - (entry_va + 5)) + b'\x90'
    print('V7 applied: DllMain detach 归还 (桩 cave+0x%X %dB, 跳板 cave+0x%X)'
          % (CTXDETACH_STUB_OFF, len(b), CTXDETACH_TRAMP_OFF))
    return data


# ============================================================ IME 修复层（新版微软拼音）
# 现象与根因（详见 docs/窗口分析及修复.md 第 9 节）：
#   GDISCALED(96dpi 虚拟)窗口下：候选框位置偏移、组字窗字体过小/尺寸不随文自适应、相对偏移。
#   根因：MSCTF 按虚拟坐标计算锚点；组字窗绘制用缓存字体
#        （[obj+0x18C/0x194]，只在"字体设置事件"重建），测量读数只喂布局。
# 输入法修复层（定稿：两门控 + 动态 DPI + 三重修复）
#   背景：GDISCALED 输入框（96dpi 窗口）在感知进程内上报的"光标客户坐标/尺寸"是虚拟值，
#   而游戏按物理（×f）渲染 → 候选框空态位置、组字窗位置/字号偏差。
#   门控：① 安装时 GetProcessDpiAwareness(当前进程)==UNAWARE（原版 MATERIA 等）→ 整层 no-op；
#         ② 运行时 GetDpiForWindow(相关窗口)==96 才缩放（仅 GDISCALED 窗口，SSP 普通窗口不修）。
#   动态因子：f = FNUM/FDEN，由 CT 站点观察实时刷新（96dpi 时 f=1，全恒等 no-op）。
#   三重修复：
#   1) 候选框空态位置：msctf+0x5495C（空态分支 call 0x5496E 处）桩 —— 调用原函数后
#      prc' = win + f·(prc−win)（win = ClientToScreen([obj],(0,0)) 现算）；
#      叠加感知修正层（AWRFIX_*）：截答 textinputframework 的感知查询为 PMv2，
#      阻止其按"未感知应用"对锚点再补一次 ×f。
#   2) 组字窗位置：msctf+0x47749（摆位链锚点 ClientToScreen 前）桩 —— pt ×f 后走原
#      ClientToScreen（win 现算；拖动自动跟随）。
#   3) 组字窗字号：gdi32!SelectObject 入口钩 —— 返回地址/对象命中时替换为按 f 重建的字体
#      （惰性创建、两句柄缓存；字体缓存随因子变化失效）。
#   组装：
#   - 钩1 user32!ClientToScreen 入口：返回地址 ∈ msctf+{0xE55D1,0xE55DD,0xEB083} →
#     门控后刷新动态因子（f 的唯一来源）。
#   - 感知修正层 AWRFIX：钩 user32!GetWindowDpiAwarenessContext，命中 textinputframework
#     两处返回地址时答 PMv2。
#   - 装配：load 导出 EAT 重定向 → 载入包装（call 安装）→ 原包装；unload 同理先还原再转；
#     目标函数于安装时经 GetModuleHandleA/GetProcAddress 解析（IAT 槽 0x4B31E4/0x4B31E0）。
#   - 还原安全：所有被改站点在安装时保存"运行时原字节"，卸载写回保存值（含重定位值，绝不写死）。
#   cave 布局（cave 内偏移）：数据 0x2810 | CTS 桩 0x2A00 | SEL 桩 0x2C00 | 跳板 0x3200/0x320C
#   | 安装 0x3300 | hook1 0x36E0 | unhook1 0x3760 | 载入/卸载包装 0x3800/0x3840 | 字符串 0x3900
#   | 感知修正层 0x5860-0x5DC0 | 组字窗桩 0x5E80 | 还原 0x6000 | 空态桩 0x7000
#   （0x3B00 打字屏蔽桩、0x4000 重力语变换桩为其他功能，勿动）

IME_BASE_OFF = 0x2810
IME_PTR_CTS = 0x00
IME_PTR_SEL = 0x04
IME_PTR_FOCUS = 0x0C
IME_PTR_GDPI = 0x10
IME_PTR_GOBJ = 0x14
IME_PTR_CFIW = 0x18
IME_PTR_GPDA = 0x1C    # shcore!GetProcessDpiAwareness（宿主判别用）
IME_PTR_STDA = 0xFC    # user32!SetThreadDpiAwarenessContext
IME_PTR_MFP = 0x08     # user32!MonitorFromPoint
IME_PTR_GPFM = 0x3C   # shcore!GetDpiForMonitor
IME_PTR_LL = 0x40     # kernel32!LoadLibraryA
IME_PTR_VPROT = 0x20
IME_MSCTF_BASE = 0x24
IME_FLAGS = 0x28
IME_ORIG_CTS = 0x2C
IME_ORIG_SEL = 0x34
IME_FNUM = 0x4C
IME_FDEN = 0x50
IME_SRC1 = 0x5C
IME_DST1 = 0x60
IME_SRC2 = 0x64
IME_DST2 = 0x68
IME_BUF = 0x70
IME_C1 = 0xD0
IME_C2 = 0xD4
IME_C3 = 0xD8
IME_C4 = 0xDC
IME_CT1 = 0xE0
IME_CT2 = 0xE4
IME_CT3 = 0xE8
IME_SELCTR = 0xEC
IME_CACHEF = 0xCC      # 缓存创建时的因子（变化则失效重建）
IME_VPOLD = 0xF0
IME_T5_CTS = 0xF4
IME_T5_SEL = 0xF8
IME_HOSTOFF = 0x120    # u8：1=unaware 宿主（如 MATERIA）→ IME 层整体 no-op
IME_HOSTVAL = 0x124    # u32：GetProcessDpiAwareness 输出暂存
# ---- 三重修复站点/槽位 ----
IME_STUB_CW = 0x5E80        # 组字窗位置修复桩（msctf+0x47749）
IME_SITE_CW = 0x47749       # msctf RVA（原 6B：FF 15 18 50 10 10，运行时已重定位）
IME_ORIG_CW = 0x18C         # 组字窗站点原 6 字节暂存（安装时保存运行时值）
IME_FLAG_CW = 0x121         # 组字窗站点装钩标志
IME_STUB_CARET = 0x7000     # 空态位置修复桩（msctf+0x5495C）
IME_SITE_CARET = 0x5495C    # msctf RVA（原 5B：E8 0D 00 00 00）
IME_ORIG_CARET = 0x192      # 空态站点原 6 字节暂存（安装时保存）
IME_FLAG_CARET = 0x168      # 空态站点装钩标志
IME_CARET_TGT = 0x16C       # 空态桩：原函数地址槽（msctf_base+0x5496E）
IME_CARET_RET = 0x170       # 空态桩：原 call 下一条地址槽（msctf_base+0x54961）
IME_CARET_OBJ = 0x174       # 空态桩：位置对象指针暂存槽
IME_CARET_PRC = 0x178       # 空态桩：prc 指针暂存槽

IME_STUB_CTS = 0x2A00
IME_STUB_SEL = 0x2C00
IME_TR_CTS = 0x3200     # 12B（8B FF 55 8B EC / FF B3 disp / C3）
IME_TR_SEL = 0x320C
IME_INSTALL = 0x3300
IME_RESTORE = 0x6000    # 还原例程（尾部空段；槽位 0x6000-0x6400）
IME_WRAP_LOAD = 0x3800
IME_WRAP_UNLOAD = 0x3840
IME_STR = 0x3900
# ---- 感知修正层（根治件）：截答 textinputframework 的窗口感知查询 ----
# 现象链：GDISCALED 幽灵窗口下，msctf 算出的锚点已是物理值；新版微软拼音的
#   进程内管线（textinputframework.dll）按窗口感知级别判断"未感知应用"再补一次
#   ×f 缩放 → 候选框二次放大（偏右下）。旧版引擎不经这段管线，故原本正确。
# 修法：钩 user32!GetWindowDpiAwarenessContext；命中 textinputframework 两处
#   调用点时回答 PMv2（物理感知）→ 补偿不再发生；锚点源头保持原值。
# 版本护栏：站点用精确返回地址比对，失配则不截答、自动降级直通（功能静默失效、无副作用）。
AWRFIX_SITE1 = 0x93642              # textinputframework.dll 两处查询的返回地址（RVA）
AWRFIX_SITE2 = 0x9365C
AWRFIX_PTR = 0x100                  # user32!GetWindowDpiAwarenessContext 指针（data_va 相对）
AWRFIX_ORIG = 0x104                 # 原序言暂存
AWRFIX_T5 = 0x108                   # 跳板槽
AWRFIX_FLAG = 0x10C                 # 挂载标志
AWRFIX_TIB = 0x110                  # 懒解析的 textinputframework 基址
AWRFIX_PMV2 = 0x114                 # 安装时取得的 PMv2 规范句柄
AWRFIX_GTC = 0x118                  # user32!GetThreadDpiAwarenessContext
AWRFIX_TMP = 0x11C
AWRFIX_STUB = 0x5860                # 感知修正桩
AWRFIX_TR = 0x5A00                  # 跳板（12B）
AWRFIX_INSTALL = 0x5A20             # 安装例程
AWRFIX_RESTORE = 0x5B80             # 还原例程
AWRFIX_STR_AWARE = 0x5C00           # "GetWindowDpiAwarenessContext\0"
AWRFIX_STR_TIB = 0x5C20             # "textinputframework.dll\0"
AWRFIX_STR_GTC = 0x5C40             # "GetThreadDpiAwarenessContext\0"
AWRFIX_HOOK1 = 0x5C80               # 本层专用 hook1（ebx=本层安装例程基址）
AWRFIX_UNHOOK1 = 0x5D00             # 本层专用 unhook1
AWRFIX_CHAIN_LOAD = 0x5D80          # call 安装两例程（IME + 本层）
AWRFIX_CHAIN_UNLOAD = 0x5DA0        # call 还原两例程（本层 + IME）
IME_CAVE_SIZE = 0x8000              # 0x6000（重力语桩）→ 0x8000：尾部空段放修复桩（组字窗/空态/还原）
IME_HOOK1 = 0x3700
IME_UNHOOK1 = 0x3780
IME_LOAD_WRAP_ORIG = 0x1F7C
IME_UNLOAD_STUB_ORIG = 0x2400
# ---- 后半 ----


GPA_IAT = 0x4B31E0
GMH_IAT = 0x4B31E4

# 字符串偏移由 _ime_str_off 自动计算（见 IME_STR_BLOB 之后）
IME_STR_BLOB = (b'user32.dll\x00' b'gdi32.dll\x00' b'msctf.dll\x00'
                b'kernel32.dll\x00' b'ClientToScreen\x00' b'SelectObject\x00'
                b'GetFocus\x00' b'GetDpiForWindow\x00'
                b'SetThreadDpiAwarenessContext\x00' b'MonitorFromPoint\x00' b'GetDpiForMonitor\x00'
                b'shcore.dll\x00' b'LoadLibraryA\x00' b'GetObjectW\x00' b'CreateFontIndirectW\x00'
                b'VirtualProtect\x00' b'GetProcessDpiAwareness\x00')

def _ime_str_off(name):
    return IME_STR_BLOB.index(name + b'\x00')


IME_S_USER32 = _ime_str_off(b'user32.dll')
IME_S_GDI32 = _ime_str_off(b'gdi32.dll')
IME_S_MSCTF = _ime_str_off(b'msctf.dll')
IME_S_KERNEL = _ime_str_off(b'kernel32.dll')
IME_S_CTS = _ime_str_off(b'ClientToScreen')
IME_S_SEL = _ime_str_off(b'SelectObject')
IME_S_FOCUS = _ime_str_off(b'GetFocus')
IME_S_GDPI = _ime_str_off(b'GetDpiForWindow')
IME_S_STDA = _ime_str_off(b'SetThreadDpiAwarenessContext')
IME_S_MFP = _ime_str_off(b'MonitorFromPoint')
IME_S_GPFM = _ime_str_off(b'GetDpiForMonitor')
IME_S_SHCORE = _ime_str_off(b'shcore.dll')
IME_S_LL = _ime_str_off(b'LoadLibraryA')
IME_S_GOBJ = _ime_str_off(b'GetObjectW')
IME_S_CFIW = _ime_str_off(b'CreateFontIndirectW')
IME_S_VPROT = _ime_str_off(b'VirtualProtect')
IME_S_GPDA = _ime_str_off(b'GetProcessDpiAwareness')


class _IB:
    def __init__(self, base_va):
        self.b = bytearray()
        self.base = base_va
        self.disp = []
        self.rel8 = []
        self.rel32 = []
        self.calls = []
        self.marks = {}

    def call_va(self, va):
        self.b += b'\xE8'
        self.calls.append((len(self.b), va))
        self.b += b'\x00' * 4

    def raw(self, d):
        self.b += d

    def d32(self, va):
        self.disp.append((len(self.b), va))
        self.b += b'\x00' * 4

    def j8(self, op, mk):
        self.b += bytes([op, 0])
        self.rel8.append((len(self.b) - 1, mk))

    def j32(self, mk):
        self.b += b'\xE9'
        self.rel32.append((len(self.b), mk))
        self.b += b'\x00' * 4

    def jc32(self, op2, mk):
        self.b += bytes([0x0F, op2])
        self.rel32.append((len(self.b), mk))
        self.b += b'\x00' * 4

    def mark(self, mk):
        self.marks[mk] = len(self.b)

    def finish(self):
        for pos, va in self.disp:
            struct.pack_into('<i', self.b, pos, va - self.base)
        for pos, mk in self.rel8:
            o = self.marks[mk] - (pos + 1)
            assert -128 <= o <= 127, ('rel8', mk, o)
            self.b[pos] = o & 0xFF
        for pos, mk in self.rel32:
            struct.pack_into('<i', self.b, pos, self.marks[mk] - (pos + 4))
        for pos, va in self.calls:
            struct.pack_into('<i', self.b, pos, va - (self.base + pos - 2))
        return bytes(self.b)


def _ime_factor_refresh(o, data_va, tag=''):
    # 线程上下文尖峰到 PMv2 -> GetDpiForMonitor（唯一实时跟随系统缩放的来源）
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_PTR_STDA)     # SetThreadDpiAwarenessContext
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'nof' + tag)
    o.raw(b'\x6A\xFC')                               # push -4 (PER_MONITOR_AWARE)
    o.raw(b'\xFF\xD0')                               # -> 旧上下文
    o.raw(b'\x50')                                    # 暂存
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_PTR_MFP)      # MonitorFromPoint
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'done2' + tag)
    o.raw(b'\x6A\x02\x6A\x00\x6A\x00')           # push 2; push y; push x
    o.raw(b'\xFF\xD0')                               # -> hm
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'done2' + tag)
    o.raw(b'\x8B\x8B'); o.d32(data_va + IME_PTR_GPFM)       # ecx = GetDpiForMonitor
    o.raw(b'\x85\xC9')
    o.j8(0x74, 'done2' + tag)
    o.raw(b'\x8D\x93'); o.d32(data_va + IME_BUF + 4)        # lea edx,[BUF+4] = &dy
    o.raw(b'\x52')                                    # push &dy（第4参）
    o.raw(b'\x8D\x93'); o.d32(data_va + IME_BUF)            # lea edx,[BUF] = &dx
    o.raw(b'\x52\x6A\x00')                          # push &dx（第3参）; push 0（第2参 dpiType）
    o.raw(b'\x50')                                    # push hm（第1参）
    o.raw(b'\xFF\xD1')                               # call ecx
    o.raw(b'\x85\xC0')
    o.j8(0x75, 'done1' + tag)
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_BUF)
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'done1' + tag)
    o.raw(b'\x89\x83'); o.d32(data_va + IME_FNUM)
    o.mark('done1' + tag)
    o.mark('done2' + tag)
    o.raw(b'\x58')                                    # pop 旧上下文
    o.raw(b'\x50')                                    # push 旧上下文
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_PTR_STDA); o.raw(b'\xFF\xD0')
    o.mark('nof' + tag)
    o.raw(b'\xC7\x83'); o.d32(data_va + IME_FDEN); o.raw(b'\x60\x00\x00\x00')


def _awrfix_stub(stub_va, data_va, tr_va):
    """GetWindowDpiAwarenessContext 钩子（感知修正件）：
    命中 textinputframework 两处调用点 → 回答伪造 PMv2 句柄（物理感知）；
    其余 → 原样转真函数（stub 顶部即返回真值，不改变任何行为）。"""
    o = _IB(stub_va + 6)
    o.raw(b'\x60')
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')
    o.raw(b'\x8B\x83'); o.d32(data_va + AWRFIX_TIB)         # mov eax,[TIB]
    o.raw(b'\x85\xC0')
    o.j8(0x75, 'haveb')
    o.raw(b'\x8D\x83'); o.d32((data_va - IME_BASE_OFF) + AWRFIX_STR_TIB); o.raw(b'\x50')
    o.raw(b'\xFF\x93'); o.d32(GMH_IAT)                      # GMH("textinputframework.dll")
    o.raw(b'\x89\x83'); o.d32(data_va + AWRFIX_TIB)
    o.mark('haveb')
    o.raw(b'\x8B\x83'); o.d32(data_va + AWRFIX_TIB)
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'plain')
    o.raw(b'\x8B\x54\x24\x20')                              # ret addr
    o.raw(b'\x8D\x88' + struct.pack('<i', AWRFIX_SITE1))    # lea ecx,[eax+SITE1]
    o.raw(b'\x3B\xD1'); o.j8(0x74, 'fake')
    o.raw(b'\x8D\x88' + struct.pack('<i', AWRFIX_SITE2))
    o.raw(b'\x3B\xD1'); o.j8(0x75, 'plain')
    o.mark('fake')
    o.raw(b'\x8B\x83'); o.d32(data_va + AWRFIX_PMV2)        # mov eax,[PMV2]
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'plain')                                     # 未取得句柄 → 走真函数
    o.raw(b'\x89\x44\x24\x1C')                              # 返回值 = 伪造句柄
    o.raw(b'\x61\xC2\x04\x00')                              # popad; ret 4
    o.mark('plain')
    o.raw(b'\xFF\x74\x24\x24')                              # push hwnd
    o.raw(b'\x8D\x83'); o.d32(tr_va)
    o.raw(b'\xFF\xD0')
    o.raw(b'\x89\x44\x24\x1C')                              # 真返回值
    o.raw(b'\x61\xC2\x04\x00')
    return o.finish()


def _awrfix_install_build(ta_va, data_va, cv):
    o = _IB(ta_va + 6)

    def d(off):
        return data_va + off

    o.raw(b'\x60')
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')
    o.raw(b'\x80\xBB'); o.d32(d(IME_HOSTOFF)); o.raw(b'\x00')
    o.j8(0x74, 'go')                            # unaware 宿主（如 MATERIA）→ 不装
    o.raw(b'\x61\xC3')
    o.mark('go')
    o.raw(b'\x8B\x83'); o.d32(d(AWRFIX_PTR))
    o.raw(b'\x85\xC0')
    o.jc32(0x85, 'resolved')
    o.raw(b'\x8D\x83'); o.d32(cv + IME_STR + IME_S_USER32); o.raw(b'\x50')
    o.raw(b'\xFF\x93'); o.d32(GMH_IAT); o.raw(b'\x8B\xF0\x85\xF6')
    o.jc32(0x84, 'done')
    for name_va, slot in ((cv + AWRFIX_STR_AWARE, AWRFIX_PTR),
                          (cv + AWRFIX_STR_GTC, AWRFIX_GTC)):
        o.raw(b'\x8D\x83'); o.d32(name_va); o.raw(b'\x50\x56')
        o.raw(b'\xFF\x93'); o.d32(GPA_IAT)
        o.raw(b'\x89\x83'); o.d32(d(slot))
    o.mark('resolved')
    # 取得 PMv2 规范句柄：old = STDA(-4); h = GetThreadDpiAwarenessContext(); STDA(old)
    o.raw(b'\x8B\x83'); o.d32(d(AWRFIX_GTC)); o.raw(b'\x85\xC0')
    o.jc32(0x84, 'nopmv2')
    o.raw(b'\x8B\x83'); o.d32(d(IME_PTR_STDA)); o.raw(b'\x85\xC0')
    o.jc32(0x84, 'nopmv2')
    o.raw(b'\x6A\xFC')                                  # push -4（PMv2）
    o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_STDA))
    o.raw(b'\x89\x83'); o.d32(d(AWRFIX_TMP))
    o.raw(b'\xFF\x93'); o.d32(d(AWRFIX_GTC))            # call GetThreadDpiAwarenessContext
    o.raw(b'\x89\x83'); o.d32(d(AWRFIX_PMV2))
    o.raw(b'\x8B\x83'); o.d32(d(AWRFIX_TMP)); o.raw(b'\x50')
    o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_STDA))          # 还原旧上下文
    o.mark('nopmv2')
    # 装钩（单钩：GetWindowDpiAwarenessContext）
    o.raw(b'\x80\xBB'); o.d32(d(AWRFIX_FLAG)); o.raw(b'\x00')
    o.j8(0x75, 'dk')
    o.raw(b'\x8B\x83'); o.d32(d(AWRFIX_PTR)); o.raw(b'\x85\xC0')
    o.j8(0x74, 'dk')
    o.raw(b'\x8B\xF0')                     # esi = target
    o.raw(b'\x81\x3E\x8B\xFF\x55\x8B')     # cmp dword [esi], 0x8B55FF8B（前 4 字节 = 8B FF 55 8B）
    o.j8(0x75, 'dk')                       # 非标准序言 → 跳过
    o.raw(b'\x8D\xBB'); o.d32(cv + AWRFIX_STUB)
    o.raw(b'\x8D\x93'); o.d32(d(AWRFIX_ORIG))
    o.raw(b'\x8D\x8B'); o.d32(d(AWRFIX_T5))
    o.raw(b'\x8D\xAB'); o.d32(d(AWRFIX_FLAG))
    o.call_va(cv + AWRFIX_HOOK1)
    o.mark('dk')
    o.mark('done')
    o.raw(b'\x61\xC3')
    return o.finish()


def _awrfix_restore_build(ta_va, data_va, cv):
    o = _IB(ta_va + 6)

    def d(off):
        return data_va + off

    o.raw(b'\x60')
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')
    o.raw(b'\x80\xBB'); o.d32(d(AWRFIX_FLAG)); o.raw(b'\x00')
    o.j8(0x74, 'rk')
    o.raw(b'\x8B\x83'); o.d32(d(AWRFIX_PTR)); o.raw(b'\x85\xC0')
    o.j8(0x74, 'rk')
    o.raw(b'\x8B\xF0')
    o.raw(b'\x8D\xBB'); o.d32(d(AWRFIX_ORIG))
    o.raw(b'\x8D\xAB'); o.d32(d(AWRFIX_FLAG))
    o.call_va(cv + AWRFIX_UNHOOK1)
    o.mark('rk')
    o.raw(b'\x61\xC3')
    return o.finish()


def _layer_chain(chain_va, a_va, b_va):
    """链例程：push ebx 保留调用者基址 → 依次 call a、b → 还原 ebx。"""
    o = _IB(chain_va + 6)
    o.raw(b'\x53')
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')
    o.raw(b'\x8D\x83'); o.d32(a_va); o.raw(b'\xFF\xD0')
    o.raw(b'\x8D\x83'); o.d32(b_va); o.raw(b'\xFF\xD0')
    o.raw(b'\x5B\xC3')
    return o.finish()


def _ime_cts_stub(stub_va, data_va, tr_va):
    """钩1：user32!ClientToScreen 入口——返回地址 ∈ msctf CT1/2/3 时，
    门控（焦点窗 GetDpiForWindow==96）后刷新动态因子 f（三重修复的公共来源）。"""
    o = _IB(stub_va + 6)
    o.raw(b'\x60')                                  # pushad
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')              # call$+5; pop ebx
    o.raw(b'\x8B\x54\x24\x20')                      # edx = 返回地址
    for slot in (IME_CT1, IME_CT2, IME_CT3):
        o.raw(b'\x3B\x93'); o.d32(data_va + slot)
        o.j8(0x74, 'match')
    o.j32('plain')
    o.mark('match')
    o.raw(b'\xFF\x93'); o.d32(data_va + IME_PTR_FOCUS)
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'plain')
    o.raw(b'\x50')
    o.raw(b'\xFF\x93'); o.d32(data_va + IME_PTR_GDPI)
    o.raw(b'\x83\xF8\x60')
    o.j8(0x75, 'plain')
    _ime_factor_refresh(o, data_va)
    o.mark('plain')
    o.raw(b'\xFF\x74\x24\x28')                      # 重推 &pt
    o.raw(b'\xFF\x74\x24\x28')                      # 重推 hwnd
    o.raw(b'\x8D\x83'); o.d32(tr_va)
    o.raw(b'\xFF\xD0')                              # call TR（原序言 + 跳入原体）
    o.raw(b'\x89\x44\x24\x1C')                      # 存返回值
    o.raw(b'\x61\xC2\x08\x00')                      # popad; ret 8
    return o.finish()

def _ime_sel_stub(stub_va, data_va, tr_va):
    o = _IB(stub_va + 6)
    o.raw(b'\x60')
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')
    # 桩顶：因子刷新 + 缓存失效（倍率变化后即使命中旧源句柄也能重建新字号）
    _ime_factor_refresh(o, data_va, '_t')
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_FNUM)
    o.raw(b'\x3B\x83'); o.d32(data_va + IME_CACHEF)
    o.j8(0x74, 'cf_top')
    o.raw(b'\x89\x83'); o.d32(data_va + IME_CACHEF)
    o.raw(b'\xC7\x83'); o.d32(data_va + IME_SRC1); o.raw(b'\x00\x00\x00\x00')
    o.raw(b'\xC7\x83'); o.d32(data_va + IME_DST1); o.raw(b'\x00\x00\x00\x00')
    o.raw(b'\xC7\x83'); o.d32(data_va + IME_SRC2); o.raw(b'\x00\x00\x00\x00')
    o.raw(b'\xC7\x83'); o.d32(data_va + IME_DST2); o.raw(b'\x00\x00\x00\x00')
    o.mark('cf_top')
    o.raw(b'\x8B\x54\x24\x20')
    for slot in (IME_C1, IME_C2, IME_C3, IME_C4):
        o.raw(b'\x3B\x93'); o.d32(data_va + slot)
        o.j8(0x74, 'hit')
    o.jc32(0x85, 'hit3')
    o.mark('hit')
    o.raw(b'\x8B\x74\x24\x28')
    o.raw(b'\x85\xF6')
    o.jc32(0x84, 'plain')
    o.raw(b'\x3B\xB3'); o.d32(data_va + IME_SRC1)
    o.jc32(0x84, 'hit2')
    o.raw(b'\x3B\xB3'); o.d32(data_va + IME_SRC2)
    o.jc32(0x84, 'hit2')
    o.j32('gate')
    o.mark('hit3')
    o.raw(b'\x8B\x74\x24\x28')
    o.raw(b'\x85\xF6')
    o.jc32(0x84, 'plain')
    o.raw(b'\x3B\xB3'); o.d32(data_va + IME_SRC1)
    o.jc32(0x84, 'hit2')
    o.raw(b'\x3B\xB3'); o.d32(data_va + IME_SRC2)
    o.jc32(0x84, 'hit2')
    o.j32('plain')
    o.mark('hit2')
    o.raw(b'\x3B\xB3'); o.d32(data_va + IME_SRC1)
    o.jc32(0x85, 'h2b')
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_DST1)
    o.j32('dopush')
    o.mark('h2b')
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_DST2)
    o.j32('dopush')
    o.mark('gate')
    o.raw(b'\xFF\x93'); o.d32(data_va + IME_PTR_FOCUS)
    o.raw(b'\x85\xC0')
    o.jc32(0x84, 'plain')
    o.raw(b'\x50')
    o.raw(b'\xFF\x93'); o.d32(data_va + IME_PTR_GDPI)
    o.raw(b'\x83\xF8\x60')
    o.jc32(0x85, 'plain')
    _ime_factor_refresh(o, data_va)
    # 因子变化 → 字体缓存失效（重建为新因子字号）
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_FNUM)
    o.raw(b'\x3B\x83'); o.d32(data_va + IME_CACHEF)
    o.j8(0x74, 'cf_ok')
    o.raw(b'\x89\x83'); o.d32(data_va + IME_CACHEF)
    o.raw(b'\xC7\x83'); o.d32(data_va + IME_SRC1); o.raw(b'\x00\x00\x00\x00')
    o.raw(b'\xC7\x83'); o.d32(data_va + IME_DST1); o.raw(b'\x00\x00\x00\x00')
    o.raw(b'\xC7\x83'); o.d32(data_va + IME_SRC2); o.raw(b'\x00\x00\x00\x00')
    o.raw(b'\xC7\x83'); o.d32(data_va + IME_DST2); o.raw(b'\x00\x00\x00\x00')
    o.mark('cf_ok')
    o.raw(b'\x8B\x74\x24\x28')
    o.raw(b'\x85\xF6')
    o.jc32(0x84, 'plain')
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_SRC1)
    o.raw(b'\x3B\xC6')
    o.j8(0x75, 'chk2')
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_DST1)
    o.j32('dopush')
    o.mark('chk2')
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_SRC2)
    o.raw(b'\x3B\xC6')
    o.j8(0x75, 'create')
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_DST2)
    o.j32('dopush')
    o.mark('create')
    o.raw(b'\x8D\x83'); o.d32(data_va + IME_BUF)
    o.raw(b'\x50\x6A\x5C\x56')
    o.raw(b'\xFF\x93'); o.d32(data_va + IME_PTR_GOBJ)
    o.raw(b'\x85\xC0')
    o.jc32(0x84, 'plain')
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_BUF)
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'sz1')
    o.raw(b'\x0F\xAF\x83'); o.d32(data_va + IME_FNUM)
    o.raw(b'\x99\x8B\x8B'); o.d32(data_va + IME_FDEN); o.raw(b'\xF7\xF9')
    o.raw(b'\x89\x83'); o.d32(data_va + IME_BUF)
    o.mark('sz1')
    o.raw(b'\x8B\x83'); o.d32(data_va + IME_BUF + 4)
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'sz2')
    o.raw(b'\x0F\xAF\x83'); o.d32(data_va + IME_FNUM)
    o.raw(b'\x99\x8B\x8B'); o.d32(data_va + IME_FDEN); o.raw(b'\xF7\xF9')
    o.raw(b'\x89\x83'); o.d32(data_va + IME_BUF + 4)
    o.mark('sz2')
    o.raw(b'\x8D\x83'); o.d32(data_va + IME_BUF)
    o.raw(b'\x50')
    o.raw(b'\xFF\x93'); o.d32(data_va + IME_PTR_CFIW)
    o.raw(b'\x85\xC0')
    o.jc32(0x84, 'plain')
    o.raw(b'\xFF\x83'); o.d32(data_va + IME_SELCTR)
    o.raw(b'\xF6\x83'); o.d32(data_va + IME_SELCTR); o.raw(b'\x01')
    o.j8(0x74, 'ev1')
    o.raw(b'\x89\x83'); o.d32(data_va + IME_DST2)
    o.raw(b'\x89\xB3'); o.d32(data_va + IME_SRC2)
    o.j32('dopush')
    o.mark('ev1')
    o.raw(b'\x89\x83'); o.d32(data_va + IME_DST1)
    o.raw(b'\x89\xB3'); o.d32(data_va + IME_SRC1)
    o.mark('dopush')
    o.raw(b'\x50')
    o.raw(b'\xFF\x74\x24\x28')
    o.j32('callit')
    o.mark('plain')
    o.raw(b'\xFF\x74\x24\x28')
    o.raw(b'\xFF\x74\x24\x28')
    o.mark('callit')
    o.raw(b'\x8D\x83'); o.d32(tr_va)
    o.raw(b'\xFF\xD0')
    o.raw(b'\x89\x44\x24\x1C')
    o.raw(b'\x61\xC2\x08\x00')
    return o.finish()


def _ime_caret_stub(stub_va, data_va, cv):
    """修复①：候选框空态位置（msctf+0x5495C，原 call 0x5496E 处）。
    F1 pushad → 门控（unaware 宿主 / [ecx]!=0 / GetDpiForWindow([ecx])==96）→
    把 TGT/RET 写进 F1 的 EDI/ESI 槽、桩基址写进 EBX 槽、obj/prc 存数据槽 → popad（栈恢复原样）
    → call edi（原函数，参数在正确位置）→ F2 pushad → prc' = win + f·(prc−win)
    （win = ClientToScreen([obj],(0,0))）→ popad → jmp esi 回 0x54961。
    门控失败：同上只调原函数。"""
    o = _IB(stub_va + 6)

    def d(off):
        return data_va + off

    o.raw(b'\x60')                                  # F1 pushad
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')              # call$+5; pop ebx
    o.raw(b'\x80\xBB'); o.d32(d(IME_HOSTOFF)); o.raw(b'\x00')
    o.jc32(0x85, 'cf_nofix')
    o.raw(b'\x8B\x01')                              # mov eax,[ecx]（hwnd）
    o.raw(b'\x85\xC0')
    o.jc32(0x84, 'cf_nofix')
    o.raw(b'\x50')
    o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_GDPI))
    o.raw(b'\x83\xF8\x60')
    o.jc32(0x85, 'cf_nofix')
    o.raw(b'\x8B\x44\x24\x18')                      # eax = F1.ECX 槽（=入口 ecx = obj）
    o.raw(b'\x89\x83'); o.d32(d(IME_CARET_OBJ))
    o.raw(b'\x8B\x44\x24\x10')                      # eax = F1.EBX 槽（=入口 ebx = prc）
    o.raw(b'\x89\x83'); o.d32(d(IME_CARET_PRC))
    o.raw(b'\x8B\xC3')                              # eax = ebx（桩基址）
    o.raw(b'\x89\x44\x24\x10')                      # F1.EBX 槽 := 基址
    o.raw(b'\x8B\x83'); o.d32(d(IME_CARET_TGT)); o.raw(b'\x89\x04\x24')      # EDI 槽 := TGT
    o.raw(b'\x8B\x83'); o.d32(d(IME_CARET_RET)); o.raw(b'\x89\x44\x24\x04')  # ESI 槽 := RET
    o.raw(b'\x61')                                  # popad（ebx=基址, edi=TGT, esi=RET）
    o.raw(b'\xFF\xD7')                              # call edi（原函数；栈=原参）
    o.raw(b'\x60')                                  # F2 pushad
    o.raw(b'\x8B\x83'); o.d32(d(IME_CARET_OBJ))
    o.raw(b'\x8B\x00')                              # eax = [obj] = hwnd
    o.raw(b'\x85\xC0')
    o.jc32(0x84, 'cf_nowin')
    o.raw(b'\xC7\x44\x24\x14\x00\x00\x00\x00')
    o.raw(b'\xC7\x44\x24\x18\x00\x00\x00\x00')
    o.raw(b'\x8D\x4C\x24\x14')                      # lea ecx,[esp+0x14]
    o.raw(b'\x51')
    o.raw(b'\x50')
    o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_CTS))         # ClientToScreen(hwnd,&pt)
    for k in range(4):
        o.raw(b'\x8B\xB3'); o.d32(d(IME_CARET_PRC))             # esi = prc
        o.raw(b'\x8B\x86' + struct.pack('<I', 4 * k))           # eax = prc[k]
        o.raw(b'\x8B\x4C\x24' + bytes([0x14 + (0 if k in (0, 2) else 4)]))
        o.raw(b'\x2B\xC1')                                      # eax -= win_k
        o.raw(b'\x8B\x8B'); o.d32(d(IME_FNUM))
        o.raw(b'\x2B\x8B'); o.d32(d(IME_FDEN))
        o.raw(b'\x0F\xAF\xC1')                      # imul eax,ecx
        o.raw(b'\x8B\x8B'); o.d32(d(IME_FDEN))
        o.raw(b'\x99\xF7\xF9')                      # cdq; idiv ecx
        o.raw(b'\x8B\xB3'); o.d32(d(IME_CARET_PRC))             # esi = prc
        o.raw(b'\x01\x86' + struct.pack('<I', 4 * k))           # prc[k] += eax
    o.mark('cf_nowin')
    o.raw(b'\x61')                                  # popad（esi=RET、eax=原函数返回值）
    o.raw(b'\xFF\xE6')                              # jmp esi → 0x54961

    o.mark('cf_nofix')
    o.raw(b'\x8B\x83'); o.d32(d(IME_CARET_TGT)); o.raw(b'\x89\x04\x24')
    o.raw(b'\x8B\x83'); o.d32(d(IME_CARET_RET)); o.raw(b'\x89\x44\x24\x04')
    o.raw(b'\x61')                                  # popad
    o.raw(b'\xFF\xD7')                              # call edi
    o.raw(b'\xFF\xE6')                              # jmp esi
    return o.finish()


def _ime_cw_stub(stub_va, data_va, cv):
    """修复②：组字窗位置（msctf+0x47749，摆位链锚点 ClientToScreen 前）。
    门控（焦点窗 GDISCALED/96）→ pt ×f → 手动调用原 ClientToScreen（msctf IAT +0x105018）→ 跳回 site+6。"""
    o = _IB(stub_va + 0x0A)
    o.raw(b'\x53\x57\x52\x51\x50')              # push ebx/edi/edx/ecx/eax
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')          # call$+5; pop ebx
    o.raw(b'\xFF\x93'); o.d32(data_va + IME_PTR_FOCUS)
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'cw_ns')
    o.raw(b'\x50')
    o.raw(b'\xFF\x93'); o.d32(data_va + IME_PTR_GDPI)
    o.raw(b'\x83\xF8\x60')
    o.j8(0x75, 'cw_ns')
    o.raw(b'\x8B\x4C\x24\x18')                  # &pt
    for pt_off in (b'\x00', b'\x04'):
        o.raw(b'\x8B\x41' + pt_off)               # eax=pt[k]
        o.raw(b'\x0F\xAF\x83'); o.d32(data_va + IME_FNUM)
        o.raw(b'\x8B\x8B'); o.d32(data_va + IME_FDEN)
        o.raw(b'\x8B\xD1\xD1\xFA\x03\xC2')      # edx=ecx; sar edx,1; add eax,edx
        o.raw(b'\x99\xF7\xF9')                    # cdq; idiv ecx
        o.raw(b'\x8B\x4C\x24\x18')                  # 重载 &pt
        o.raw(b'\x89\x41' + pt_off)
    o.mark('cw_ns')
    o.raw(b'\x8B\x8B'); o.d32(data_va + IME_MSCTF_BASE)
    o.raw(b'\x81\xC1'); o.raw(struct.pack('<I', 0x105018))
    o.raw(b'\xFF\x74\x24\x18')                  # push &pt
    o.raw(b'\xFF\x74\x24\x18')                  # push hwnd
    o.raw(b'\xFF\x11')                            # call [ecx]
    o.raw(b'\x8B\x93'); o.d32(data_va + IME_MSCTF_BASE)
    o.raw(b'\x81\xC2'); o.raw(struct.pack('<I', IME_SITE_CW + 6))
    o.raw(b'\x58\x59\x83\xC4\x04\x5F\x5B\x83\xC4\x08\xFF\xE2')
    return o.finish()

def _ime_hook1(base_va, data_va):
    """共享装钩子（安装例程 call；ebx=安装例程锚点）。
    入：esi=目标VA、edi=桩VA、edx=原序言槽VA、ecx=T5槽VA、ebp=标志字节VA。"""
    cv = data_va - IME_BASE_OFF
    o = _IB(base_va)

    def d(off):
        return data_va + off

    o.raw(b'\x8B\x06\x89\x02')                          # mov eax,[esi]; mov [edx],eax
    o.raw(b'\x8B\x46\x04\x89\x42\x04')                  # mov eax,[esi+4]; mov [edx+4],eax
    o.raw(b'\x8B\xC6\x83\xC0\x05')                      # mov eax,esi; add eax,5（T5 槽 := site+5）
    o.raw(b'\x89\x01')                                  # mov [ecx],eax
    o.raw(b'\x8D\x83'); o.d32(d(IME_VPOLD))
    o.raw(b'\x50\x6A\x40\x6A\x08\x56')                  # push &old; push 0x40; push 8; push esi
    o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_VPROT))
    o.raw(b'\x8B\xC7\x2B\xC6\x83\xE8\x05')              # mov eax,edi; sub eax,esi; sub eax,5
    o.raw(b'\x89\x46\x01')                              # mov [esi+1],eax
    o.raw(b'\xC6\x06\xE9')                              # mov byte [esi],0xE9
    o.raw(b'\x8D\x83'); o.d32(d(IME_VPOLD))
    o.raw(b'\x50')                                      # push &vpold（第4参 lpflOldProtect）
    o.raw(b'\xFF\xB3'); o.d32(d(IME_VPOLD))            # push [vpold]（第3参 flNewProtect=旧值）
    o.raw(b'\x6A\x08\x56')                            # push 8; push esi
    o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_VPROT))
    o.raw(b'\xC6\x45\x00\x01')                          # mov byte [ebp],1
    o.raw(b'\xC3')
    return o.finish()


def _ime_unhook1(base_va, data_va):
    """共享还原子（还原例程 call）。入：esi=目标、edi=原序言槽VA、ebp=标志VA。"""
    o = _IB(base_va)

    def d(off):
        return data_va + off

    o.raw(b'\x80\x3E\xE9')
    o.j8(0x75, 'done')
    o.raw(b'\x8D\x83'); o.d32(d(IME_VPOLD))
    o.raw(b'\x50\x6A\x40\x6A\x08\x56')
    o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_VPROT))
    o.raw(b'\x8B\x07\x8B\x4F\x04\xC1\xE8\x08\xC1\xE1\x18\x0B\xC1')
    o.raw(b'\x89\x46\x01')
    o.raw(b'\x8B\x07\x88\x06')                          # mov eax,[edi]; mov [esi],al
    o.raw(b'\x8D\x83'); o.d32(d(IME_VPOLD))
    o.raw(b'\x50')                                      # push &vpold（第4参 lpflOldProtect）
    o.raw(b'\xFF\xB3'); o.d32(d(IME_VPOLD))            # push [vpold]（第3参 flNewProtect=旧值）
    o.raw(b'\x6A\x08\x56')                            # push 8; push esi
    o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_VPROT))
    o.raw(b'\xC6\x45\x00\x00')
    o.mark('done')
    o.raw(b'\xC3')
    return o.finish()


def _ime_install_build(ta_va, data_va):
    """安装例程。ta_va = 例程自身 VA；ebx=锚点。"""
    cv = data_va - IME_BASE_OFF
    o = _IB(ta_va + 6)

    def d(off):
        return data_va + off

    def gmh(name_rel):
        o.raw(b'\x8D\x83'); o.d32(cv + IME_STR + name_rel)
        o.raw(b'\x50')
        o.raw(b'\xFF\x93'); o.d32(GMH_IAT)
        o.raw(b'\x8B\xF0\x85\xF6')

    def ro(name_rel, slot):
        o.raw(b'\x8D\x83'); o.d32(cv + IME_STR + name_rel)
        o.raw(b'\x50\x56')
        o.raw(b'\xFF\x93'); o.d32(GPA_IAT)
        o.raw(b'\x89\x83'); o.d32(d(slot))

    o.raw(b'\x60')
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')
    o.raw(b'\x8B\x83'); o.d32(d(IME_PTR_CTS))
    o.raw(b'\x85\xC0')
    o.jc32(0x85, 'resolved')
    gmh(IME_S_USER32); o.j8(0x74, 'no_u32')
    ro(IME_S_CTS, IME_PTR_CTS)
    ro(IME_S_FOCUS, IME_PTR_FOCUS)
    ro(IME_S_GDPI, IME_PTR_GDPI)
    ro(IME_S_STDA, IME_PTR_STDA)
    ro(IME_S_MFP, IME_PTR_MFP)
    o.mark('no_u32')
    gmh(IME_S_GDI32); o.j8(0x74, 'no_g32')
    ro(IME_S_SEL, IME_PTR_SEL)
    ro(IME_S_GOBJ, IME_PTR_GOBJ)
    ro(IME_S_CFIW, IME_PTR_CFIW)
    o.mark('no_g32')
    gmh(IME_S_MSCTF); o.j8(0x74, 'no_m32')
    o.raw(b'\x89\xB3'); o.d32(d(IME_MSCTF_BASE))
    o.raw(b'\x8B\x83'); o.d32(d(IME_MSCTF_BASE))
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'no_ct')
    for _slot, _rva in ((IME_CT1, 0xE55D1), (IME_CT2, 0xE55DD), (IME_CT3, 0xEB083),
                        (IME_C1, 0xE40ED), (IME_C2, 0xE413C), (IME_C3, 0xE3EEA), (IME_C4, 0xE3F0F)):
        o.raw(b'\x8B\xC8')                              # mov ecx,eax
        o.raw(b'\x81\xC1'); o.raw(struct.pack('<I', _rva))   # add ecx,rva
        o.raw(b'\x89\x8B'); o.d32(d(_slot))             # mov [slot],ecx
    o.mark('no_ct')
    o.mark('no_m32')
    gmh(IME_S_KERNEL); o.j8(0x74, 'no_k32')
    ro(IME_S_VPROT, IME_PTR_VPROT)
    ro(IME_S_LL, IME_PTR_LL)
    gmh(IME_S_SHCORE)
    o.j8(0x75, 'got_h')
    o.raw(b'\x8D\x83'); o.d32(cv + IME_STR + IME_S_SHCORE)
    o.raw(b'\x50')
    o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_LL))
    o.raw(b'\x8B\xF0\x85\xF6')
    o.j8(0x74, 'no_shcore')
    o.mark('got_h')
    o.raw(b'\x8D\x83'); o.d32(cv + IME_STR + IME_S_GPFM)
    o.raw(b'\x50\x56')
    o.raw(b'\xFF\x93'); o.d32(GPA_IAT)
    o.raw(b'\x89\x83'); o.d32(d(IME_PTR_GPFM))
    o.raw(b'\x8D\x83'); o.d32(cv + IME_STR + IME_S_GPDA)
    o.raw(b'\x50\x56')
    o.raw(b'\xFF\x93'); o.d32(GPA_IAT)
    o.raw(b'\x89\x83'); o.d32(d(IME_PTR_GPDA))
    o.mark('no_shcore')
    o.mark('no_k32')
    o.mark('resolved')
    # —— 宿主判别：进程感知 == UNAWARE(0) → 原生场景（MATERIA 等）→ IME 层整体 no-op ——
    o.raw(b'\x8B\x83'); o.d32(d(IME_PTR_GPDA))
    o.raw(b'\x85\xC0')
    o.j8(0x74, 'hostaware')                     # 解析失败 → 当作 aware
    o.raw(b'\x8D\x93'); o.d32(d(IME_HOSTVAL))
    o.raw(b'\x52\x6A\xFF')                      # push &val; push -1（当前进程）
    o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_GPDA))
    o.raw(b'\x8B\x83'); o.d32(d(IME_HOSTVAL))
    o.raw(b'\x85\xC0')
    o.j8(0x75, 'hostaware')                     # !=0 → aware → 正常装钩
    o.raw(b'\xC6\x83'); o.d32(d(IME_HOSTOFF)); o.raw(b'\x01')   # 标记 unaware 宿主
    o.j32('done')                               # 跳过全部装钩（含因子刷新与感知修正）
    o.mark('hostaware')
    o.raw(b'\xC7\x83'); o.d32(d(IME_FNUM)); o.raw(b'\x60\x00\x00\x00')
    o.raw(b'\xC7\x83'); o.d32(d(IME_FDEN)); o.raw(b'\x60\x00\x00\x00')
    _ime_factor_refresh(o, data_va)

    def emit_hook(idx, flag_off, ptr_slot, stub_off, orig_off, t5_off):
        mk = 'sk%d' % idx
        o.raw(b'\x80\xBB'); o.d32(d(flag_off)); o.raw(b'\x00')
        o.j8(0x75, mk)
        o.raw(b'\x8B\x83'); o.d32(d(ptr_slot))
        o.raw(b'\x85\xC0')
        o.j8(0x74, mk)
        o.raw(b'\x8B\xF0')
        o.raw(b'\x8D\xBB'); o.d32(cv + stub_off)
        o.raw(b'\x8D\x93'); o.d32(d(orig_off))
        o.raw(b'\x8D\x8B'); o.d32(d(t5_off))
        o.raw(b'\x8D\xAB'); o.d32(d(flag_off))
        o.call_va(cv + IME_HOOK1)
        o.mark(mk)

    emit_hook(0, IME_FLAGS + 0, IME_PTR_CTS, IME_STUB_CTS, IME_ORIG_CTS, IME_T5_CTS)
    emit_hook(1, IME_FLAGS + 1, IME_PTR_SEL, IME_STUB_SEL, IME_ORIG_SEL, IME_T5_SEL)
    # —— 调用点型修复钩（保存运行时原字节 + 写 E9）：组字窗 + 空态 ——
    for _site, _stub, _orig, _flag, _b0 in ((IME_SITE_CW, IME_STUB_CW, IME_ORIG_CW, IME_FLAG_CW, 0xFF),
                                            (IME_SITE_CARET, IME_STUB_CARET, IME_ORIG_CARET, IME_FLAG_CARET, 0xE8)):
        _mk = 'ch%d' % _site
        o.raw(b'\x8B\xB3'); o.d32(d(IME_MSCTF_BASE))
        o.raw(b'\x85\xF6')
        o.j8(0x74, _mk)
        o.raw(b'\x81\xC6'); o.raw(struct.pack('<I', _site))
        o.raw(b'\x80\x3E' + bytes([_b0]))
        o.jc32(0x85, _mk)
        if _site == IME_SITE_CARET:
            o.raw(b'\x8D\x86'); o.raw(struct.pack('<I', 0x12))  # lea eax,[esi+0x12] = 原函数 0x5496E
            o.raw(b'\x89\x83'); o.d32(d(IME_CARET_TGT))
            o.raw(b'\x8D\x86'); o.raw(struct.pack('<I', 5))     # lea eax,[esi+5] = 原 call 下一条 0x54961
            o.raw(b'\x89\x83'); o.d32(d(IME_CARET_RET))
        o.raw(b'\x8B\x06\x89\x83'); o.d32(d(_orig))
        o.raw(b'\x66\x8B\x46\x04\x66\x89\x83'); o.d32(d(_orig) + 4)
        o.raw(b'\x8D\x83'); o.d32(d(IME_VPOLD)); o.raw(b'\x50')
        o.raw(b'\x6A\x40\x6A\x06\x56')
        o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_VPROT))
        o.raw(b'\x8D\x83'); o.d32(cv + _stub)
        o.raw(b'\x2B\xC6\x83\xE8\x05')
        o.raw(b'\xC6\x06\xE9\x89\x46\x01')
        o.raw(b'\x8D\x83'); o.d32(d(IME_VPOLD)); o.raw(b'\x50')
        o.raw(b'\xFF\xB3'); o.d32(d(IME_VPOLD))
        o.raw(b'\x6A\x06\x56')
        o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_VPROT))
        o.raw(b'\xC6\x83'); o.d32(d(_flag)); o.raw(b'\x01')
        o.mark(_mk)
    o.mark('done')
    o.raw(b'\x61\xC3')
    return o.finish()


def _ime_restore_build(ta_va, data_va):
    cv = data_va - IME_BASE_OFF
    o = _IB(ta_va + 6)

    def d(off):
        return data_va + off

    o.raw(b'\x60')
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')

    def emit_unhook(idx, flag_off, ptr_slot, orig_off):
        mk = 'rk%d' % idx
        o.raw(b'\x80\xBB'); o.d32(d(flag_off)); o.raw(b'\x00')
        o.j8(0x74, mk)
        o.raw(b'\x8B\x83'); o.d32(d(ptr_slot))
        o.raw(b'\x85\xC0')
        o.j8(0x74, mk)
        o.raw(b'\x8B\xF0')
        o.raw(b'\x8D\xBB'); o.d32(d(orig_off))
        o.raw(b'\x8D\xAB'); o.d32(d(flag_off))
        o.call_va(cv + IME_UNHOOK1)
        o.mark(mk)

    emit_unhook(0, IME_FLAGS + 0, IME_PTR_CTS, IME_ORIG_CTS)
    emit_unhook(1, IME_FLAGS + 1, IME_PTR_SEL, IME_ORIG_SEL)
    # —— 调用点型修复钩还原（写回安装时保存的运行时原字节）——
    for _site, _orig, _flag in ((IME_SITE_CW, IME_ORIG_CW, IME_FLAG_CW),
                                (IME_SITE_CARET, IME_ORIG_CARET, IME_FLAG_CARET)):
        _mk = 'cr%d' % _site
        o.raw(b'\x80\xBB'); o.d32(d(_flag)); o.raw(b'\x00')
        o.j8(0x74, _mk)
        o.raw(b'\x8B\xB3'); o.d32(d(IME_MSCTF_BASE))
        o.raw(b'\x85\xF6')
        o.j8(0x74, _mk)
        o.raw(b'\x81\xC6'); o.raw(struct.pack('<I', _site))
        o.raw(b'\x80\x3E\xE9')
        o.j8(0x75, _mk)
        o.raw(b'\x8D\x83'); o.d32(d(IME_VPOLD)); o.raw(b'\x50')
        o.raw(b'\x6A\x40\x6A\x06\x56')
        o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_VPROT))
        o.raw(b'\x8B\x83'); o.d32(d(_orig)); o.raw(b'\x89\x06')
        o.raw(b'\x66\x8B\x83'); o.d32(d(_orig) + 4); o.raw(b'\x66\x89\x46\x04')
        o.raw(b'\x8D\x83'); o.d32(d(IME_VPOLD)); o.raw(b'\x50')
        o.raw(b'\xFF\xB3'); o.d32(d(IME_VPOLD))
        o.raw(b'\x6A\x06\x56')
        o.raw(b'\xFF\x93'); o.d32(d(IME_PTR_VPROT))
        o.raw(b'\xC6\x83'); o.d32(d(_flag)); o.raw(b'\x00')
        o.mark(_mk)
    o.raw(b'\x61\xC3')
    return o.finish()


def _ime_wrap_build(ta_va, call_va, jmp_va):
    o = _IB(ta_va + 6)
    o.raw(b'\x60')
    o.raw(b'\xE8\x00\x00\x00\x00\x5B')
    o.raw(b'\x8D\x83'); o.d32(call_va)
    o.raw(b'\xFF\xD0')
    o.raw(b'\x8D\x83'); o.d32(jmp_va)
    o.raw(b'\x89\x44\x24\x1C')
    o.raw(b'\x61\xFF\xE0')
    return o.finish()


# ====================== 接线（并入 patch_dll.py 后由其调用）======================
def _ime_tr_body(cv, stub_off, t5_off):
    """系统函数跳板：原序言 5B + push [T5 槽] + ret（T5 由安装例程填 target+5）。"""
    anchor = cv + stub_off + 6
    disp = (cv + IME_BASE_OFF + t5_off) - anchor
    return b'\x8B\xFF\x55\x8B\xEC' + b'\xFF\xB3' + struct.pack('<i', disp) + b'\xC3'


def build_ime_layer(data):
    """构建全部 IME 组件并接线（返回 data）。宿主需提供 _u32/_u16/struct/data。"""
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt = e + 24
    opt_size = _u16(data, e + 20)
    sec = opt + opt_size
    cave_rva = cave_raw = None
    for i in range(nsec):
        off = sec + 40 * i
        if bytes(data[off:off + 5]) == b'.cave':
            cave_rva = _u32(data, off + 12)
            cave_raw = _u32(data, off + 20)
    if cave_raw is None:
        raise RuntimeError('IME：找不到 .cave')
    cave_va = 0x400000 + cave_rva
    data_va = cave_va + IME_BASE_OFF

    # 零区预检（0x2810-0x3900）
    if any(data[cave_raw + IME_BASE_OFF: cave_raw + IME_STR]):
        raise RuntimeError('IME：数据/桩区非零，疑似布局冲突')
    if any(data[cave_raw + IME_STR: cave_raw + 0x3A00]):
        raise RuntimeError('IME：字符串区非零')

    def put(off, blob):
        data[cave_raw + off: cave_raw + off + len(blob)] = blob

    def put_ck(off, limit_off, blob, tag):
        """带槽位长度断言的写入（防止加长后静默覆盖相邻例程）。"""
        if len(blob) > limit_off - off:
            raise RuntimeError('IME：%s 过长 %dB > 槽位 %dB（cave+0x%X 起，止于 +0x%X）'
                               % (tag, len(blob), limit_off - off, off, limit_off))
        put(off, blob)

    put_ck(IME_STR, 0x3A00, IME_STR_BLOB, '字符串区')
    put_ck(IME_STUB_CTS, IME_STUB_SEL, _ime_cts_stub(cave_va + IME_STUB_CTS, data_va, cave_va + IME_TR_CTS), 'CTS 桩')
    put_ck(IME_STUB_SEL, 0x3000, _ime_sel_stub(cave_va + IME_STUB_SEL, data_va, cave_va + IME_TR_SEL), 'SEL 桩')
    if any(data[cave_raw + IME_STUB_CW: cave_raw + IME_RESTORE]) or \
       any(data[cave_raw + IME_RESTORE: cave_raw + 0x6400]) or \
       any(data[cave_raw + IME_STUB_CARET: cave_raw + 0x7400]):
        raise RuntimeError('IME：修复桩区非零，疑似布局冲突')
    put_ck(IME_STUB_CW, IME_RESTORE,
           _ime_cw_stub(cave_va + IME_STUB_CW, data_va, cave_va), '组字窗桩')
    put_ck(IME_STUB_CARET, 0x7400,
           _ime_caret_stub(cave_va + IME_STUB_CARET, data_va, cave_va), '空态桩')
    put(IME_TR_CTS, _ime_tr_body(cave_va, IME_STUB_CTS, IME_T5_CTS))
    put(IME_TR_SEL, _ime_tr_body(cave_va, IME_STUB_SEL, IME_T5_SEL))
    put_ck(IME_HOOK1, IME_UNHOOK1, _ime_hook1(cave_va + IME_INSTALL + 6, data_va), 'hook1')
    put_ck(IME_UNHOOK1, IME_WRAP_LOAD, _ime_unhook1(cave_va + IME_RESTORE + 6, data_va), 'unhook1')
    put_ck(IME_INSTALL, IME_HOOK1, _ime_install_build(cave_va + IME_INSTALL, data_va), '安装例程')
    put_ck(IME_RESTORE, 0x6400, _ime_restore_build(cave_va + IME_RESTORE, data_va), '还原例程')
    # 感知修正层（根治件）：独立子层，链入 load/unload
    if any(data[cave_raw + AWRFIX_STUB: cave_raw + 0x5DC0]):
        raise RuntimeError('IME：感知修正层区域非零，疑似布局冲突')
    put(AWRFIX_STR_AWARE, b'GetWindowDpiAwarenessContext\x00')
    put(AWRFIX_STR_TIB, b'textinputframework.dll\x00')
    put(AWRFIX_STR_GTC, b'GetThreadDpiAwarenessContext\x00')
    put_ck(AWRFIX_STUB, AWRFIX_TR, _awrfix_stub(cave_va + AWRFIX_STUB, data_va, cave_va + AWRFIX_TR), '感知修正桩')
    put(AWRFIX_TR, _ime_tr_body(cave_va, AWRFIX_STUB, AWRFIX_T5))
    put_ck(AWRFIX_INSTALL, AWRFIX_RESTORE, _awrfix_install_build(cave_va + AWRFIX_INSTALL, data_va, cave_va), '感知修正安装')
    put_ck(AWRFIX_RESTORE, AWRFIX_STR_AWARE, _awrfix_restore_build(cave_va + AWRFIX_RESTORE, data_va, cave_va), '感知修正还原')
    put_ck(AWRFIX_HOOK1, AWRFIX_UNHOOK1, _ime_hook1(cave_va + AWRFIX_INSTALL + 6, data_va), '感知修正 hook1')
    put_ck(AWRFIX_UNHOOK1, AWRFIX_CHAIN_LOAD, _ime_unhook1(cave_va + AWRFIX_RESTORE + 6, data_va), '感知修正 unhook1')
    put_ck(AWRFIX_CHAIN_LOAD, AWRFIX_CHAIN_UNLOAD,
           _layer_chain(cave_va + AWRFIX_CHAIN_LOAD, cave_va + IME_INSTALL, cave_va + AWRFIX_INSTALL), '装载链')
    put_ck(AWRFIX_CHAIN_UNLOAD, IME_CAVE_SIZE,
           _layer_chain(cave_va + AWRFIX_CHAIN_UNLOAD, cave_va + AWRFIX_RESTORE, cave_va + IME_RESTORE), '卸载链')
    put_ck(IME_WRAP_LOAD, IME_WRAP_UNLOAD, _ime_wrap_build(cave_va + IME_WRAP_LOAD,
                                                           cave_va + AWRFIX_CHAIN_LOAD,
                                                           cave_va + IME_LOAD_WRAP_ORIG), '载入包装')
    put_ck(IME_WRAP_UNLOAD, IME_STR, _ime_wrap_build(cave_va + IME_WRAP_UNLOAD,
                                                     cave_va + AWRFIX_CHAIN_UNLOAD,
                                                     cave_va + IME_UNLOAD_STUB_ORIG), '卸载包装')

    # EAT 重定向：load（既有断言：索引 1 = cave+0x1F7C）；unload（找 = cave+0x2400）
    _eo = _u32(data, opt + 96)
    _efo_off = None
    for i in range(nsec):
        off2 = sec + 40 * i
        va2 = _u32(data, off2 + 12)
        vs2 = _u32(data, off2 + 8)
        if va2 <= _eo < va2 + vs2:
            _efo_off = _u32(data, off2 + 20) + (_eo - va2)
    _af = _u32(data, _efo_off + 28)
    _fun = None
    for i in range(nsec):
        off2 = sec + 40 * i
        va2 = _u32(data, off2 + 12)
        vs2 = _u32(data, off2 + 8)
        if va2 <= _af < va2 + vs2:
            _fun = _u32(data, off2 + 20) + (_af - va2)
    nfun = _u32(data, _efo_off + 20)
    if _u32(data, _fun + 4) != cave_rva + IME_LOAD_WRAP_ORIG:
        raise RuntimeError('IME：load EAT 不符（应=cave+0x1F7C）')
    struct.pack_into('<I', data, _fun + 4, cave_rva + IME_WRAP_LOAD)
    for i in range(nfun):
        if _u32(data, _fun + 4 * i) == cave_rva + IME_UNLOAD_STUB_ORIG:
            struct.pack_into('<I', data, _fun + 4 * i, cave_rva + IME_WRAP_UNLOAD)
            break
    else:
        raise RuntimeError('IME：未找到 unload EAT（=cave+0x2400）')
    print('IME 层已应用: 三重修复(空态/组字窗位置/字号) + 感知修正层 + 安装/还原 + 载入/卸载包装 + EAT 重定向'
          '（cave+0x%X..0x%X）' % (IME_BASE_OFF, AWRFIX_CHAIN_UNLOAD + 0x20))
    return data


# ============================================================================
# 键盘修复层（Tab 切换 / Alt+助记符）：复刻宿主泵缺失的 VCL 键预处理
# ----------------------------------------------------------------------------
# 背景：ssp.exe 的消息泵 JWinThread::TranslateDispatchMessage(0x5A72A0) 只有
#   TranslateMessage + DispatchMessageW；DLL 自带 VCL 泵（TApplication.ProcessMessage
#   0x44FA38）在 SSP 下不运行。其键处理链为 IsHintMsg → IsMDIMsg → IsKeyMsg → IsDlgMsg：
#   - IsKeyMsg(0x44F8E4)：把 WM_KEYxxx(0x100..0x108) 转成 CN_xxx(+0xBC00；
#     0xBD00=CN_KEYDOWN、0xBD04=CN_SYSKEYDOWN) SendMessageA 给消息窗（或其 VCL 祖先）
#     → 控件 CNKeyDown → CM_DIALOGKEY(0xB01E) → 窗体 CMDlgKey → SelectNext
#     （VCL 控件树自身顺序，跨容器/助记符天然正确）；命中返回非 0；
#   - IsDlgMsg(0x44F870) = IsDialogMessageA([App+0xA0], MSG)：SSP 宿主不维护
#     [App+0xA0]（MATERIA 由宿主 0xB031 通知维护），这里补写消息窗根窗体兜底。
# 方案：挂钩原 load 入口(0xAA374) → 安装桩解析真 user32 函数（绕 SSP 对 ghost DLL
#   IAT 的假桩）→ 宿主门控（SSP 主窗存在且属本进程，否则整层 no-op）→ 取主窗线程
#   装 WH_GETMESSAGE 钩子。钩子对 "PM_REMOVE + 0x100..0x109 + 消息窗类属本模块"
#   的消息按泵序补做 IsKeyMsg →（未命中）IsDlgMsg；处理成功则把消息号清为 WM_NULL
#   （宿主裸派发也不再生效），其余一律 CallNextHookEx 放行。
# 卸载安全：退出修复存根（_exitfix_stub）开头先 UnhookWindowsHookEx 再走原流程。
# 布局（.cave）：0x6400 钩子(≤0x300) / 0x6700 安装桩(≤0x300) / 0x6A00 字符串组(0x77) /
#   0x6A80 hhk / 0x6A84 真 UnhookWindowsHookEx / 0x6A88 GetParent /
#   0x6A8C GetClassLongA / 0x6A90 本进程 pid 暂存 / 0x6A94 主窗 pid 暂存 /
#   0x6A98 链返回值暂存
# ============================================================================
KBD_ENABLE = True
KBDFIX_LOAD_RVA = 0xAA374
KBDFIX_LOAD_PROLOG = bytes.fromhex('55 8B EC B9 07 00 00 00')
KBD_HOOK_OFF = 0x6400
KBD_SETUP_OFF = 0x6700
KBD_STR_OFF = 0x6A00
KBD_HHK = 0x6A80
KBD_UNHOOK = 0x6A84
KBD_GA = 0x6A88
KBD_GCL = 0x6A8C
KBD_PID_SELF = 0x6A90
KBD_PID_WIN = 0x6A94
KBD_CRET = 0x6A98

KBD_IAT_CNHE = 0x4B3790        # user32!CallNextHookEx
KBD_IAT_GMH = 0x4B31E4         # kernel32!GetModuleHandleA
KBD_IAT_GPA = 0x4B31E0         # kernel32!GetProcAddress
KBD_IAT_GCPI = 0x4B339C        # kernel32!GetCurrentProcessId
KBD_IAT_FW = 0x4B3704          # user32!FindWindowA
KBD_IAT_GWTPID = 0x4B3654      # user32!GetWindowThreadProcessId
KBD_MAIN_CLASS = b'SSPMAIN-3145fdab-2ee0-4158-a1ce-832b553ad790\x00'


def _kbd_strs():
    return [(0x00, b'user32.dll\x00'), (0x0B, b'SetWindowsHookExA\x00'),
            (0x1D, b'UnhookWindowsHookEx\x00'), (0x31, b'GetParent\x00'),
            (0x3B, KBD_MAIN_CLASS), (0x69, b'GetClassLongA\x00')]


def _build_kbd_hook(hook_va, cave_va, hook_rva):
    b = bytearray()
    ji = []
    marks = {}

    def op(*xs):
        for x in xs:
            b.append(x) if isinstance(x, int) else b.extend(x)

    def d32(va):
        return struct.pack('<i', va - hook_va)

    def C(off):
        return d32(cave_va + off)

    def mark(n):
        marks[n] = len(b)

    def jcc(cc, n):
        op(0x0F, cc); ji.append((len(b), n)); op(0, 0, 0, 0)

    def jmp(n):
        op(0xE9); ji.append((len(b), n)); op(0, 0, 0, 0)

    op(0x53, 0x55, 0x56, 0x57)                      # push ebx/ebp/esi/edi（定栈帧）

    def reanchor():                                  # 外部调用后重算锚点（不假设 API 保 EBX）
        o = len(b)
        op(0xE8, 0, 0, 0, 0, 0x5B)
        op(0x81, 0xEB); op(struct.pack('<I', o + 5))

    def callabs(va):                                 # 同模块内 call（构建期 rel32，重定位安全）
        o = len(b)
        op(0xE8); op(struct.pack('<i', va - (hook_va + o + 5)))

    op(0xE8, 0, 0, 0, 0, 0x5B)                      # call$+5; pop ebx
    op(0x81, 0xEB, 9, 0, 0, 0)                      # sub ebx,9（4 压栈 + 返回址）→ ebx=hook_va
    # —— 先把消息放行给链上其余钩子（msctf/TSF 的输入法键处理、宿主钩子），再对
    #    "处理之后"的消息复刻 VCL 泵——与 MATERIA 同序（所有钩子先于泵执行）。
    #    组字中的按键会被 TSF 标记/消费，这里自然跳过，不干扰输入法。 ——
    op(0x8B, 0x44, 0x24, 0x1C)                      # eax=lParam
    op(0x8B, 0x4C, 0x24, 0x18)                      # ecx=wParam
    op(0x8B, 0x54, 0x24, 0x14)                      # edx=nCode
    op(0x50, 0x51, 0x52, 0x6A, 0x00)
    op(0xFF, 0x93); op(d32(KBD_IAT_CNHE))           # CallNextHookEx（链先跑）
    reanchor()
    op(0x89, 0x83); op(C(KBD_CRET))                 # 暂存链返回值（最终原样返回）
    op(0x8B, 0x7C, 0x24, 0x1C)                      # edi=[lParam]=MSG*
    op(0x83, 0x7F, 0x04, 0x00); jcc(0x84, 'done')   # 已被链上钩子消费（message==0）
    op(0x8B, 0x44, 0x24, 0x14)                      # eax=nCode
    op(0x85, 0xC0); jcc(0x88, 'done')               # nCode<0
    op(0x83, 0x7C, 0x24, 0x18, 0x01)                # cmp [wParam],PM_REMOVE
    jcc(0x85, 'done')
    op(0x8B, 0x47, 0x04)                            # eax=msg.message（链处理后重读）
    op(0x2D, 0, 1, 0, 0)                            # sub eax,0x100
    op(0x83, 0xF8, 9); jcc(0x87, 'done')            # 不在 0x100..0x109
    op(0x8B, 0x37)                                  # esi=msg.hwnd
    op(0x8B, 0x83); op(C(KBD_GCL))                  # [GetClassLongA] 未解析 → 放行
    op(0x85, 0xC0); jcc(0x84, 'done')
    op(0x6A, 0xF0, 0x56)                            # push -16(GCL_HMODULE); push hwnd
    op(0xFF, 0x93); op(C(KBD_GCL))                  # call GetClassLongA
    reanchor()
    op(0x89, 0xDA); op(0x81, 0xEA); op(struct.pack('<I', hook_rva))
    op(0x3B, 0xC2); jcc(0x84, 'wndok')              # 类属本模块 → 通过
    # 退路：父窗的类也属本模块也算（系统类子控件/嵌套容器）
    op(0x8B, 0x83); op(C(KBD_GA))                   # [GetParent] 未解析 → 放行
    op(0x85, 0xC0); jcc(0x84, 'done')
    op(0x56); op(0xFF, 0x93); op(C(KBD_GA))         # call GetParent(hwnd)
    reanchor()
    op(0x85, 0xC0); jcc(0x84, 'done')
    op(0x6A, 0xF0, 0x50); op(0xFF, 0x93); op(C(KBD_GCL))  # GCL(parent,-16)
    reanchor()
    op(0x89, 0xDA); op(0x81, 0xEA); op(struct.pack('<I', hook_rva))
    op(0x3B, 0xC2); jcc(0x85, 'done')              # jne → 放行
    mark('wndok')
    # —— 复刻 VCL 泵的键处理（IsKeyMsg → IsDlgMsg）——
    op(0x8B, 0x83); op(d32(0x4AF8EC))               # eax=[TApplication 全局]
    op(0x85, 0xC0); jcc(0x84, 'trydlg')             # 未初始化 → 兜底
    op(0x8B, 0xD7)                                  # edx=MSG*
    callabs(0x44F8E4)                               # TApplication.IsKeyMsg
    reanchor()
    op(0x84, 0xC0); jcc(0x84, 'trydlg')
    jmp('eat')                                      # 命中 → 吃掉
    mark('trydlg')
    # IsDlgMsg 兜底：仅当 VCL 自己维护了对话框句柄（[App+0xA0]!=0）时才有意义，
    # 绝不代写该句柄——强设句柄会让 IsDialogMessageA 接管该窗口全部按键的分发
    # （绕开 TranslateMessage），输入法组字被破坏（实测：所有键被 DLGHIT 吞掉）。
    op(0x8B, 0x83); op(d32(0x4AF8EC))               # eax=[TApplication 全局]
    op(0x85, 0xC0); jcc(0x84, 'done')               # 未初始化 → 放行
    op(0x8B, 0xD7)                                  # edx=MSG*
    callabs(0x44F870)                               # TApplication.IsDlgMsg（句柄 0 时内部空转）
    op(0x84, 0xC0); jcc(0x84, 'done')
    mark('eat')
    op(0xC7, 0x47, 0x04, 0, 0, 0, 0)                # [MSG+4]=WM_NULL（消息作废）
    mark('done')
    op(0x8B, 0x83); op(C(KBD_CRET))                 # eax=链返回值
    op(0x5F, 0x5E, 0x5D, 0x5B)                      # pop edi/esi/ebp/ebx
    op(0xC2, 0x0C, 0x00)
    for pos, n in ji:
        struct.pack_into('<i', b, pos, marks[n] - (pos + 4))
    assert len(b) <= 0x300, len(b)
    return bytes(b)


def _build_kbd_setup(setup_va, cave_va, hook_va, setup_rva, load_rva):
    b = bytearray()
    ji = []
    marks = {}

    def op(*xs):
        for x in xs:
            b.append(x) if isinstance(x, int) else b.extend(x)

    def d32(va):
        return struct.pack('<i', va - setup_va)

    def C(off):
        return d32(cave_va + off)

    def mark(n):
        marks[n] = len(b)

    def jcc(cc, n):
        op(0x0F, cc); ji.append((len(b), n)); op(0, 0, 0, 0)

    def jmp(n):
        op(0xE9); ji.append((len(b), n)); op(0, 0, 0, 0)

    def reanchor():                                  # 外部调用后重算锚点（不假设 API 保 EBX）
        o = len(b)
        op(0xE8, 0, 0, 0, 0, 0x5B)
        op(0x81, 0xEB); op(struct.pack('<I', o + 5))

    op(0x53, 0x56, 0x57)                            # push ebx/esi/edi
    op(0xE8, 0, 0, 0, 0, 0x5B)                      # call$+5; pop ebx
    op(0x81, 0xEB, 8, 0, 0, 0)                      # ebx=setup_va
    op(0x8B, 0x83); op(C(KBD_HHK))                  # eax=[hhk]
    op(0x85, 0xC0); jcc(0x85, 'replay')             # 已装 → replay
    op(0x8D, 0x83); op(C(KBD_STR_OFF)); op(0x50)    # GMH("user32.dll")
    op(0xFF, 0x93); op(d32(KBD_IAT_GMH))
    reanchor()
    op(0x85, 0xC0); jcc(0x84, 'replay')
    op(0x8B, 0xF0)                                  # esi=user32 hmod
    op(0x8D, 0x83); op(C(KBD_STR_OFF + 0x0B)); op(0x50, 0x56)
    op(0xFF, 0x93); op(d32(KBD_IAT_GPA))
    reanchor()
    op(0x85, 0xC0); jcc(0x84, 'replay')
    op(0x8B, 0xF8)                                  # edi=真 SetWindowsHookExA
    op(0x8D, 0x83); op(C(KBD_STR_OFF + 0x1D)); op(0x50, 0x56)
    op(0xFF, 0x93); op(d32(KBD_IAT_GPA))
    reanchor()
    op(0x89, 0x83); op(C(KBD_UNHOOK))               # 真 UnhookWindowsHookEx
    op(0x8D, 0x83); op(C(KBD_STR_OFF + 0x31)); op(0x50, 0x56)
    op(0xFF, 0x93); op(d32(KBD_IAT_GPA))
    reanchor()
    op(0x89, 0x83); op(C(KBD_GA))                   # 真 GetParent
    op(0x8D, 0x83); op(C(KBD_STR_OFF + 0x69)); op(0x50, 0x56)
    op(0xFF, 0x93); op(d32(KBD_IAT_GPA))
    reanchor()
    op(0x89, 0x83); op(C(KBD_GCL))                  # 真 GetClassLongA
    # —— 宿主门控：SSP 主窗存在且属本进程；否则整层 no-op（如 MATERIA）——
    op(0x6A, 0x00)                                  # FindWindowA(class, NULL)
    op(0x8D, 0x83); op(C(KBD_STR_OFF + 0x3B)); op(0x50)
    op(0xFF, 0x93); op(d32(KBD_IAT_FW))
    reanchor()
    op(0x85, 0xC0); jcc(0x84, 'replay')
    op(0x89, 0xC6)                                  # esi=hwnd（user32 句柄已无用）
    op(0xFF, 0x93); op(d32(KBD_IAT_GCPI))           # GetCurrentProcessId
    reanchor()
    op(0x89, 0x83); op(C(KBD_PID_SELF))             # [本进程 pid]
    op(0x8D, 0x93); op(C(KBD_PID_WIN)); op(0x52)    # push &窗口 pid（arg2）
    op(0x56)                                        # push hwnd（arg1）
    op(0xFF, 0x93); op(d32(KBD_IAT_GWTPID))         # GetWindowThreadProcessId → eax=tid
    reanchor()
    op(0x85, 0xC0); jcc(0x84, 'replay')             # tid==0 → no-op
    op(0x8B, 0x93); op(C(KBD_PID_SELF))             # edx=本进程 pid
    op(0x39, 0x93); op(C(KBD_PID_WIN))              # cmp [窗口 pid],edx
    jcc(0x85, 'replay')                             # 异进程 → no-op
    op(0x50)                                        # push tid
    op(0x89, 0xD8); op(0x2D); op(struct.pack('<I', setup_rva))  # eax=模块运行时基址
    op(0x50)                                        # hMod
    op(0x8D, 0x83); op(d32(hook_va)); op(0x50)      # lpfn
    op(0x6A, 0x03)                                  # WH_GETMESSAGE
    op(0xFF, 0xD7)                                  # call 真 SetWindowsHookExA
    reanchor()
    op(0x89, 0x83); op(C(KBD_HHK))                  # [hhk]=句柄
    mark('replay')
    op(0x5F, 0x5E, 0x5B)                            # pop edi/esi/ebx
    op(0x55, 0x8B, 0xEC, 0xB9, 0x07, 0, 0, 0)       # 重放 load 序言
    here = len(b)
    op(0xE9)
    b += struct.pack('<i', (0x400000 + load_rva + 8) - (setup_va + here + 5))
    for pos, n in ji:
        struct.pack_into('<i', b, pos, marks[n] - (pos + 4))
    assert len(b) <= 0x300, len(b)
    return bytes(b)


def patch_kbd_fix(data: bytearray) -> bytearray:
    """键盘修复层：写钩子/安装桩/字符串，挂钩原 load 入口（0xAA374）。"""
    e = _u32(data, 0x3C)
    nsec = _u16(data, e + 6)
    opt = e + 24
    opt_size = _u16(data, e + 20)
    sec = opt + opt_size
    cave_rva = cave_raw = None
    for i in range(nsec):
        off = sec + 40 * i
        if bytes(data[off:off + 5]) == b'.cave':
            cave_rva = _u32(data, off + 12)
            cave_raw = _u32(data, off + 20)
    if cave_rva is None:
        raise RuntimeError('kbdfix：未找到 .cave 段')

    def rva_off(rva):
        for i in range(nsec):
            off = sec + 40 * i
            va = _u32(data, off + 12)
            vs = _u32(data, off + 8)
            raw = _u32(data, off + 20)
            if va <= rva < va + vs:
                return raw + (rva - va)
        raise RuntimeError('kbdfix：RVA 0x%X 不在任何节' % rva)

    cave_va = 0x400000 + cave_rva
    if any(data[cave_raw + KBD_HOOK_OFF:cave_raw + 0x7000]):
        raise RuntimeError('kbdfix：cave 0x6400-0x7000 非零（区域冲突）')
    hook = _build_kbd_hook(cave_va + KBD_HOOK_OFF, cave_va, cave_rva + KBD_HOOK_OFF)
    setup = _build_kbd_setup(cave_va + KBD_SETUP_OFF, cave_va,
                             cave_va + KBD_HOOK_OFF, cave_rva + KBD_SETUP_OFF, KBDFIX_LOAD_RVA)
    strs_len = max(off + len(s) for off, s in _kbd_strs())
    if KBD_HOOK_OFF + len(hook) > KBD_SETUP_OFF:
        raise RuntimeError('kbdfix：钩子越界')
    if KBD_SETUP_OFF + len(setup) > KBD_STR_OFF:
        raise RuntimeError('kbdfix：安装桩越界压字符串区')
    if KBD_STR_OFF + strs_len > KBD_HHK:
        raise RuntimeError('kbdfix：字符串区越界压槽位')
    data[cave_raw + KBD_HOOK_OFF:cave_raw + KBD_HOOK_OFF + len(hook)] = hook
    data[cave_raw + KBD_SETUP_OFF:cave_raw + KBD_SETUP_OFF + len(setup)] = setup
    for off, s in _kbd_strs():
        data[cave_raw + KBD_STR_OFF + off:cave_raw + KBD_STR_OFF + off + len(s)] = s
    fo = rva_off(KBDFIX_LOAD_RVA)
    if bytes(data[fo:fo + 8]) != KBDFIX_LOAD_PROLOG:
        raise RuntimeError('kbdfix：load 入口序言不符 %s' % data[fo:fo + 8].hex())
    rel = (cave_va + KBD_SETUP_OFF) - (0x400000 + KBDFIX_LOAD_RVA + 5)
    data[fo] = 0xE9
    struct.pack_into('<i', data, fo + 1, rel)
    print('键盘修复层已应用: 钩子@cave+0x%X(%dB) 安装桩@cave+0x%X(%dB) load 入口挂钩'
          % (KBD_HOOK_OFF, len(hook), KBD_SETUP_OFF, len(setup)))
    return data


BASE = os.path.dirname(os.path.abspath(__file__))
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

    if typ == 'answer':
        # 存储格式：答案文本字节 -> 大写 hex -> 整串反转
        stored = raw.hex().upper()[::-1].encode('ascii')
        if len(stored) > length:
            print(f'答案超长: off=0x{off:X} 原={length} 新={len(stored)} {enc} text={repr(text)}')
            skip += 1; continue
        data[off : off + len(stored)] = stored
        data[off - 4 : off] = struct.pack('<I', len(stored))
        if length > len(stored):
            data[off + len(stored) : off + length] = b'\x00' * (length - len(stored))
        ok += 1
        continue

    if typ == 'pchar':
        # 无长度头的 PChar 字面量（前面不是字符串头！不能写 off-4）：
        # 只写内容+NUL，容量 = 原长 + 3（原字面量后的 3 个补零字节须存在）。
        cap = length + 3
        if data[off + length : off + cap] != b'\x00' * 3:
            print(f'PChar尾部非零: off=0x{off:X} text={repr(text)}')
            skip += 1; continue
        if len(raw) + 1 > cap:
            print(f'PChar超长: off=0x{off:X} 容量={cap} 新={len(raw)} text={repr(text)}')
            skip += 1; continue
        data[off : off + len(raw)] = raw
        data[off + len(raw) : off + cap] = b'\x00' * (cap - len(raw))
        ok += 1
        continue

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
    # rsrc / font：不更新长度字段，直接补 \x00
    if rest:
        data[off + len(raw) : off + length] = b'\x00' * rest
    ok += 1

data = bytearray(apply_patches(bytes(data)))
print('兼容补丁已应用: NOTIFY 分发 (0x719E9) + 诱导模式字符串清零 (0x79E08)')

# 必须在文本翻译写入之后再移位（翻译随 DFM 区块一起平移，结构保持自洽）
data = patch_dfm_charset(data)
print('DFM Font.Charset 已改: Tfirstconfigform/Tnotifyform SHIFTJIS→GB2312')

data = patch_extra_link(data)

# 高分屏缩放：load/request 导出包装（请求期间线程置 UNAWARE_GDISCALED）
if DPI_WRAP_ENABLE:
    data = patch_dpi_wrap(data)
    # 信息窗拖动：SC_DRAGMOVE 的 SendMessage 包装
    data = patch_dpi_drag(data)
    # 系统字体初始化包装（状态栏提示字等系统字体取 96dpi 规格）
    if DPI_SYSFONT_ENABLE:
        data = patch_dpi_sysfont(data)
    # 状态栏类补 CS_HREDRAW（仅 TStatusBar，CreateParams 调用点透明桩）
    if CRPARAMS_HREDRAW_ENABLE:
        data = patch_createparams_hredraw(data)

data = patch_aitxt(data)


data = patch_exit_fix(data)

data = patch_ctx_return_on_detach(data)

# ULW 层：≠100% 拖动命中掩码（=100% 不做任何修改）+ 跨 100% 缩放刷新修复（子类化/cloak）
data = patch_ulw_hitmask(data)

# IME 修复层（微软拼音候选/组字窗/字体；详见《窗口分析及修复.md》§9）
if IME_ENABLE:
    data = build_ime_layer(data)

# 键盘修复层（Tab 切换 / Alt+助记符；详见《窗口分析及修复.md》§11）
if KBD_ENABLE:
    data = patch_kbd_fix(data)

os.makedirs(os.path.dirname(DLL_OUT), exist_ok=True)
with open(DLL_OUT, 'wb') as f:
    f.write(data)

print(f'first.dll 写入完成 → {DLL_OUT}')

# ---- 部署：命令行第一个参数 = ghost master 目录（或其下任一 dll 文件路径）----
if len(sys.argv) >= 2:
    dst = sys.argv[1]
    target_dir = dst if os.path.isdir(dst) else os.path.dirname(os.path.abspath(dst))
    first_dst = os.path.join(target_dir, 'first.dll')
    shutil.copy2(DLL_OUT, first_dst)
    print(f'已复制 → {first_dst}')

print(f'  写入: {ok}  截断: {trunc}  跳过: {skip}')