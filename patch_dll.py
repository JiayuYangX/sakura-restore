#!/usr/bin/env python3
"""
一次完成 first.dll + misaki.dll 的全部补丁，输出到 output/。
可选：命令行第一个参数 = 部署目标（ghost master 目录，或该目录下任一 dll 路径），
两个 dll 会一起复制过去。

first.dll：

  文本翻译（CSV）：
    Offset = 写入位置，Length = 最大字节数，Type = code|answer|rsrc|font。
    - code：off-4 处为 4 字节小端长度，写入后更新长度并清零剩余
    - answer：文本按编码规则（默认 GBK）转为「反转大写 hex」再写入
      （off-4 处长度同步更新；新字节数不得超过原长，否则报「答案超长」跳过）
    - rsrc：off-1 处为 1 字节长度，写入后更新长度并清零剩余
    - font：无长度前缀，用 \\x00 补齐
    - pchar：无长度前缀的 PChar 字面量（前面不是字符串头，禁写 off-4），
      只写内容+NUL，容量 = 原长+3

  兼容补丁（写入前逐字节校验原值）：
    1. NOTIFY -> 按 GET 分发（把 0x719E9 处的 jne 填成 NOP）
       first.dll 只实现了 GET；SSP 2.5.33+ 在 cantalk=0 时会把后台事件
       以 NOTIFY 发来，之前会收到 400 并卡死状态机。
    2. r"\\![enter,inductionmode]" 字符串长度 23 -> 0（0x79E08）
       诱导模式会让 cantalk 永远保持 false，导致后台事件走 NOTIFY
       （响应被忽略）、泡澡结束的对话不可见。
    3. Tcpuloadform 布局两处写死高度 128 -> 64（0x46891A / 0x468AC0）：
       信息窗只保留文字区，整窗命中即文字区命中（配合 misaki 的整层 alpha 填充）。

  AITXT（词库）：aitxt_translated.txt（UTF-8）-> GBK -> 加密数据块，覆盖 PE 资源
    目录中定位到的 AITXT 资源，并更新资源数据项的 Size 字段。写入前 round-trip 校验。

  链接化补丁（海原雄山）：「自动加链接」名单由 7 段固定序列注册，最后一段（木野さん）
    的 call 重定向到 .cave 小桩：先补完原调用，再对「海原雄山」常量调用一次注册。

  RSS 链接补丁（OnAnchorSelect 打开浏览器）：兜底分支入口重定向到 .cave 第二桩：
    Ref0 以 "http" 开头则在节内缓冲拼出 "\\![open,browser,<URL>]" 返回。

  游戏 / 双击相关补丁（.cave 内若干桩，详见各构建函数文档串）：
    - 桩A（响应监控，挂 0x47A399）：输入框标志、视力游戏状态（MARK/EYEBUSY）、
      游戏退出收尾（PENDING+GAMELEFT）、游戏菜单缓存、视力取消输入框按“空提交”。
    - 桩B（双击判定，挂 0x4782BD）：菜单/输入框/视力答题中双击吞掉；打字/问答
      双击重放该游戏菜单；视力已进入但未弹框时置 PENDING + 调关窗小段。
    - 关窗体辅助桩：FindWindowA + WM_CLOSE 关 Ttypinggameform / Teyesightform /
      Tcountdownform；CloseQuery 跳板保证 GAMELEFT 期间打字框可关。

  高分屏缩放与拖动：
    - load / request 导出包装：请求期间线程置 UNAWARE_GDISCALED(-5)，把 DLL 自建
      窗口交给系统按屏幕缩放（含 GDI 自绘文字）；请求之外 SSP 自身界面不受影响。
    - 拖动修复：6 处 FormMouseMove 里 SC_DRAGMOVE 的 SendMessageA 调用改为经过
      .cave 包装桩（拖动模态循环期间线程 GDISCALED），另有 6 处锚点坐标换算
      （物理像素 → 96dpi 虚拟坐标）。

  状态栏重影修复（原版缺陷）：TStatusBar 的窗口类缺 CS_HREDRAW，拖动改变 Todo/Notify
    宽度时系统不做整窗失效、旧像素残留（文字重影）。在 TWinControl.CreateWnd 调用虚拟
    CreateParams 的指令处（0x4352C1）挂透明桩：仅当栈帧里类名为 "TStatusBar" 时给
    Params.WindowClass.style 补 CS_HREDRAW；其余类原样通过，不碰 RegisterClassA。

misaki.dll（透明窗命中区）：
  透明窗为 WS_EX_LAYERED + UpdateLayeredWindow 逐像素 alpha 窗口，系统按图层
  alpha 做命中判定（alpha==0 穿透）；DPI 虚拟化下只有字形能命中、拖不动。
  把 allclear 清零填充值改成 0x01（alpha=1/255，肉眼不可见但可命中），配合
  first.dll 的信息窗高度减半 → 信息窗整窗（=文字区）可拖、时钟整窗可拖。
"""
import csv, hashlib, os, sys, struct, shutil, unicodedata, zlib

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
    # 3. Tcpuloadform 布局里写死的高度 128 -> 64（两处：VA 0x46891A / 0x468AC0，
    #    文件偏移 0x67D1A / 0x67EC0：mov edx,0x80; call SetClientHeight）
    #    信息窗只保留文字区：整窗=文字区 → 命中/拖动/穿透自然正确，配合 misaki
    #    整层 alpha 填充也不会再有黑线。
    (0x67D1A, bytes.fromhex('80 00 00 00'), bytes.fromhex('40 00 00 00')),
    (0x67EC0, bytes.fromhex('80 00 00 00'), bytes.fromhex('40 00 00 00')),
]


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
PREFIX_OFF = 0x100                 # "\![open,browser," 常量
BUF_DATA_OFF = 0x200               # 响应缓冲（数据指针）

# ---- 游戏/双击相关补丁的 .cave 数据区 ----
# .cave 布局（0x2000 字节追加节；改动后应做区间重叠检查）：
#   0x000 链接化桩(26) | 0x020 RSS桩(134) | 0x100 "\![open,browser,"
#   0x110 类名串 + 空提交脚本(dstr @0x140/数据@0x148)
#   0x1F8-0x3FF 链接桩2 响应缓冲（string 头 + 数据，链接文档）
#   0x380 视力双击小段 | 0x400 关窗体辅助桩(0x400-0x486) | 0x500 双击判定桩B | 0x600 响应监控桩A
#   0x940 标志组(FLAG/MARK/PENDING/GAMELEFT/SWALLOW/EYEBUSY)
#   0x95C 菜单缓存头(rc@0x95C/长度@0x960/数据@0x964，cap 0x6E0)
#   0x1050 CLOSE_CMD(123) | 0x10D0 CloseQuery跳板 | 0x10F8 PENDING前置拼接缓冲(rc/len@0x10FC/数据@0x1100，
#          数据可达 0x1F7B，故 0x1F7C 之后才空)
#   高分屏包装桩：request@0x170（空闲段 0x167-0x1F7）| load@0x1F7C（尾段）| 数据@0xB0
#   （PSET/字符串，占用 0xA7-0xFF 空闲段）
#   拖动包装桩 @0x2000（需 cave ≥ 0x2200；调用点 6 处 SC_DRAGMOVE）
# .cave 节大小 0x2200（原 0x2000，+0x200 放拖动包装桩）。

CAVE_B_OFF = 0x500                 # 桩B：双击判定（读请求 Status + 游戏/输入框标志）
CAVE_A_OFF = 0x600                 # 桩A：响应监控（标志维护/退出收尾/菜单缓存）
FLAG_OFF = 0x940                   # 输入框标志（dword：1=有 SSP 输入框打开）
MARK_OFF = 0x944                   # 视力游戏标记（1=已进入视力游戏）
PENDING_OFF = 0x948                # 退出待处理字节（1=本次退出响应前置 CLOSE_CMD）
GAMELEFT_OFF = 0x94C               # 游戏已退出字节（1=吞掉残留游戏事件响应）
SWALLOW_OFF = 0x94D                # 本次响应待吞字节
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
    拼接 "\\![open,browser," + Ref0 + "]" 到节内缓冲并返回；否则原兜底。"""
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
    b += b'\x8D\x93' + struct.pack('<i', BUF_DATA_OFF - CAVE2_OFF - 16)  # 47: lea edx,[ebx+buf-8]
    b += b'\xC7\x02\xFF\xFF\xFF\xFF'                  # 53: mov dword [edx],-1
    b += b'\x8D\x41\x11'                              # 59: lea eax,[ecx+17]
    b += b'\x89\x42\x04'                              # 62: mov [edx+4],eax
    b += b'\x8D\xB3' + struct.pack('<i', PREFIX_OFF - CAVE2_OFF - 8)  # 65: lea esi,[ebx+prefix]
    b += b'\x8D\xBA\x08\x00\x00\x00'                  # 71: lea edi,[edx+8]
    b += b'\x51'                                      # 77: push ecx
    b += b'\xB9\x10\x00\x00\x00'                      # 78: mov ecx,16
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
    blob = bytearray(0x3700)
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
    data[raw + PREFIX_OFF:raw + PREFIX_OFF + 16] = b'\\![open,browser,'
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

    # 桩 B：双击判定（choosing / 输入框 / 视力游戏 → 无反应；其他游戏重放菜单）
    stubB = _build_dc_status_stub(cave_va + CAVE_B_OFF)
    data[raw + CAVE_B_OFF:raw + CAVE_B_OFF + len(stubB)] = stubB
    off = DC_ENTRY_VA - 0x400C00
    if bytes(data[off:off + 8]) != DC_ENTRY_ORIG:
        raise RuntimeError('双击补丁：入口原始字节不匹配')
    data[off:off + 8] = (b'\xE9' + struct.pack(
        '<i', cave_va + CAVE_B_OFF - (DC_ENTRY_VA + 5))) + b'\x90' * 3

    print(f'补丁已应用: 链接化/RSS/响应监控/关窗体/双击判定 @ RVA 0x{rva:X}')
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
def _build_dpi_wrap_stub(stub_va, data_va, target_va, insert_font_call=False):
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
    if insert_font_call and IME_FOCUS_DPI_ENABLE:
        # 借入口调用组字字体修复例程（此时线程仍是 SSP 上下文，DPI 读数正确）
        _fv = (data_va - DPI_WRAP_DATA_OFF) + IME_FONT_STUB_OFF
        buf += b'\xE8' + struct.pack('<i', _fv - (stub_va + len(buf) + 5))
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
    buf += b'\x6A\xFB'                    # push -5（UNAWARE_GDISCALED）
    buf += b'\xFF\x93'; d32(pset_va)      # call [ebx+pset-base]（省 2 字节）
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
DPI_GETDPI_STR = 0x34         # 数据区：+0x34 "GetDpiForWindow"
DPI_GETDPI_SLOT = 0x30        # 数据区：+0x30 GDWF 指针
DPI_GH_VA = 0x4380A4          # VCL GetHandle 辅助（eax=Self → eax=HWND）

# 拖动锚点换算（现行做法）：拖动方法里固定的一段
#   66 8B 55 08         mov dx, word [ebp+8]      ; X（客户区）
#   66 8B 45 0C         mov ax, word [ebp+0xC]    ; Y
#   E8 .. .. .. ..      call MAKELPARAM(0x407054)
# 共 13 字节，替换为 call <cave 助手> + 8×NOP。助手按 96/GetDpiForWindow 把 (X,Y)
# 换算成虚拟坐标并返回打包好的 lParam（物理像素→虚拟，修复判定/锚点空间不一致）。
DPI_ANCHOR_ENABLE = True
DPI_ANCHOR_HELPER_OFF = 0x2090
DPI_ANCHOR_PATTERN = bytes.fromhex('66 8B 55 08 66 8B 45 0C E8')
DPI_ANCHOR_MAKELPARAM = 0x407054
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


def _build_anchor_helper(stub_va, data_va):
    """锚点换算助手：入口 ebp = 拖动方法帧（[ebp+8]=X 字、[ebp+0xC]=Y 字），
    ebx = Self。返回 eax = lParam（虚拟客户区坐标打包）。换算失败时按原样返回。"""
    gdwf_va = data_va + DPI_GETDPI_SLOT
    str1_va = data_va + DPI_WRAP_USER32
    str2_va = data_va + DPI_GETDPI_STR
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

    buf += b'\x57'                        # push edi
    buf += b'\xE8\x00\x00\x00\x00'        # call $+5
    base = stub_va + len(buf)             # pop edi 的 VA
    buf += b'\x5F'                        # pop edi
    buf += b'\x8B\x87'; d32(gdwf_va)      # mov eax,[edi+gdwf-base]
    buf += b'\x85\xC0'
    buf += b'\x75'; r8('have')
    buf += b'\x8D\x87'; d32(str1_va)
    buf += b'\x50'
    buf += b'\xFF\x97'; d32(gmh_va)
    buf += b'\x85\xC0'
    buf += b'\x74'; r8('raw')
    buf += b'\x50'
    buf += b'\x8D\x87'; d32(str2_va)
    buf += b'\x50'
    buf += b'\xFF\x97'; d32(gpa_va)
    buf += b'\x89\x87'; d32(gdwf_va)
    mark('have')
    buf += b'\x8B\x87'; d32(gdwf_va)
    buf += b'\x85\xC0'
    buf += b'\x74'; r8('raw')
    buf += b'\x8B\xC3'                    # mov eax,ebx（Self）
    buf += b'\x8D\x97'; d32(DPI_GH_VA)    # lea edx,[edi+gh-base]
    buf += b'\xFF\xD2'                    # call edx → eax = hwnd
    buf += b'\x50'
    buf += b'\xFF\x97'; d32(gdwf_va)      # call [edi+gdwf-base] → eax = dpi
    buf += b'\x85\xC0'
    buf += b'\x74'; r8('raw')
    buf += b'\x8B\xC8'                    # mov ecx,eax（dpi）
    buf += b'\x0F\xB7\x45\x08'            # movzx eax, word [ebp+8]（X）
    buf += b'\x6B\xC0\x60'                # imul eax,eax,96
    buf += b'\x33\xD2'
    buf += b'\xF7\xF1'                    # div ecx
    buf += b'\x8B\xF8'                    # mov edi,eax（X'）
    buf += b'\x0F\xB7\x45\x0C'            # movzx eax, word [ebp+0xC]（Y）
    buf += b'\x6B\xC0\x60'
    buf += b'\x33\xD2'
    buf += b'\xF7\xF1'
    buf += b'\xC1\xE0\x10'                # shl eax,16
    buf += b'\x0B\xC7'                    # or eax,edi
    buf += b'\x5F'                        # pop edi
    buf += b'\xC3'
    mark('raw')
    buf += b'\x0F\xB7\x45\x08'            # movzx eax, word [ebp+8]
    buf += b'\x0F\xB7\x55\x0C'            # movzx edx, word [ebp+0xC]
    buf += b'\xC1\xE2\x10'                # shl edx,16
    buf += b'\x0B\xC2'                    # or eax,edx
    buf += b'\x5F'                        # pop edi
    buf += b'\xC3'

    for pos, va in disp:
        struct.pack_into('<i', buf, pos, va - base)
    for pos, mk in rel:
        buf[pos] = (marks[mk] - (pos + 1)) & 0xFF
    return bytes(buf)


def patch_dpi_anchor(data: bytearray) -> bytearray:
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
        raise RuntimeError('锚点换算：找不到 .cave 节')
    helper = _build_anchor_helper(DPI_WRAP_IB + cave_rva + DPI_ANCHOR_HELPER_OFF,
                                  DPI_WRAP_IB + cave_rva + DPI_WRAP_DATA_OFF)
    if len(helper) > 0x100:
        raise RuntimeError(f'锚点换算：助手过长 {len(helper)}')
    if any(data[cave_raw + DPI_ANCHOR_HELPER_OFF:
               cave_raw + DPI_ANCHOR_HELPER_OFF + len(helper)]):
        raise RuntimeError('锚点换算：助手位置非空')
    data[cave_raw + DPI_ANCHOR_HELPER_OFF:
         cave_raw + DPI_ANCHOR_HELPER_OFF + len(helper)] = helper
    helper_va = DPI_WRAP_IB + cave_rva + DPI_ANCHOR_HELPER_OFF
    patched = 0
    for site in DPI_DRAG_CALLS:
        fo = site - 0x400C00
        found = None
        for k in range(fo, fo - 0x40, -1):
            if bytes(data[k:k + 9]) == DPI_ANCHOR_PATTERN:
                rel = struct.unpack_from('<i', data, k + 9)[0]
                if (k + 0x400C00 + 13) + rel == DPI_ANCHOR_MAKELPARAM:
                    found = k
                    break
        if found is None:
            raise RuntimeError(f'锚点换算：0x{site:X} 附近找不到 MAKELPARAM 序列')
        data[found:found + 13] = b'\xE8' + struct.pack(
            '<i', helper_va - (found + 0x400C00 + 5)) + b'\x90' * 8
        patched += 1
    print(f'拖动锚点换算已应用: {patched} 处')
    return data


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


# 输入法组字修复：
#  1) 字体：组字窗用输入框的"逻辑字体"（96dpi）在 aware 窗口里按物理像素画 → 字小一号；
#  2) 位置：组字窗按"虚拟坐标"定位（正确位置 ÷ 1.5）→ 横向偏移、挡住文字。
# VCL 类窗口过程收不到消息（被实例过程接管），所以借用"每次脚本请求都会经过"的
# request 导出包装：在其入口调用本修复例程——取当前焦点窗口，把该窗口 IME 上下文的
# 组字字体按 真实DPI/96 放大（ImmSetCompositionFontA），并把光标客户端坐标同样放大后
# 写入 COMPOSITIONFORM（ImmSetCompositionWindow）。例程自保全部寄存器（pushad），
# 不改线程上下文，无副作用。
IME_FOCUS_DPI_ENABLE = True
IME_SUB_ENABLE = False           # 子类化实验停用：退出时可能转发到已释放的 VCL thunk 导致崩溃
IME_TIMER_ENABLE = False        # 50ms 定时器实验停用：退出时队列残留会派发到已卸载代码
IME_FONT_STUB_OFF = 0x2400      # 修复例程（cave 扩到 0x2A00 后的新区）
IME_FONT_CF_OFF = 0x3500        # COMPOSITIONFORM 缓冲（28 字节）
IME_FONT_SCRATCH_OFF = 0x3540   # 暂存区（0xA0）
IME_FONT_STR_OFF = 0x3600       # 字符串区
IME_TIMERPROC_OFF = 0x2960      # 50ms 定时器回调（高频修复，消除闪烁）
IME_CLEANUP_STUB_OFF = 0x2980   # 幽灵卸载清理桩（KillTimer，防野指针）
IME_CLEANUP_SITE = 0x43C684     # 幽灵卸载初始化例程入口
IME_CLEANUP_ORIG = bytes.fromhex('A1 7C E0 4A 00')   # mov eax,[0x4AE07C]
IME_CLEANUP_NEXT = 0x43C689     # 补完后继续处
IME_SUB_STUB_OFF = 0x2A00       # 子类窗口过程（接管焦点窗口：即时修复 + 上下文实验）
IME_CTX_OFF = 0x2B00            # 上下文实验数据：[0]=ctx_focus [4]=arm [8]=saved
IME_TAB_OFF = 0x2B20            # 子类表：8 × (hwnd, oldproc)
IAT_GETWINLONG = 0x4B3664
IAT_SETWINLONG = 0x4B3568
IAT_GETCLS = 0x4B36F0
IME_CLSBUF_OFF = 0x2B60     # GetClassNameA 缓冲（60）
AUDIT_STUB_OFF = 0x2C00     # 窗口过程审计：调用桩
AUDIT_CB_OFF = 0x2C80       # 审计回调
AUDIT_LOG_OFF = 0x2D00      # 日志：count(4) + 64 × (hwnd, proc, class16)
IAT_ENUMTW = 0x4B3718
IAT_GETTID = 0x4B3398
IAT_UEF = 0x4B3214          # UnhandledExceptionFilter（用来反推 kernel32 基址）
EXC_OFF = 0x33B0            # 异常记录：code/addr/ctx/eip/esp/old/inst/setuf
EXC_FILTER_OFF = 0x3400     # 异常过滤器桩
S_HWND, S_HIMC = 0x04, 0x00
S_HDC, S_DPI, S_IMM = 0x08, 0x0C, 0x10
S_GETCTX, S_SETFONT, S_RELCTX = 0x14, 0x18, 0x1C
S_LF = 0x20
S_SETWIN, S_GETCARET = 0x5C, 0x60
S_CNT_CALL, S_CNT_HWND = 0x64, 0x68
S_GETOBJRC, S_SETRC, S_SETWINRC, S_CFX, S_CFY = 0x6C, 0x70, 0x74, 0x78, 0x7C
S_TIMER_ID = 0x98               # SetTimer 返回的定时器 id（0=未装）
S_CAND_N, S_CAND_D = 0x80, 0x84           # 候选框位置缩放系数（运行中可热调）
S_SETCAND, S_CANDRC, S_CANDX, S_CANDY = 0x88, 0x8C, 0x90, 0x94
IAT_GETFOCUS = 0x4B36D0
IAT_GMH = 0x4B31E4
IAT_LOADLIB = 0x4B3324
IAT_GPA = 0x4B3370
IAT_SM = 0x4B35A8
IAT_GETOBJ = 0x4B349C
IAT_GETDC = 0x4B36DC
IAT_DEVCAPS = 0x4B34AC
IAT_RELDC = 0x4B35BC
IAT_SETTIMER = 0x4B356C
IAT_KILLTIMER = 0x4B3618
IME_FONT_STRS = (b'imm32.dll\x00', b'ImmGetContext\x00',
                 b'ImmSetCompositionFontA\x00', b'ImmReleaseContext\x00',
                 b'ImmSetCompositionWindow\x00', b'user32.dll\x00', b'GetCaretPos\x00',
                 b'ImmSetCandidateWindow\x00', b'SetUnhandledExceptionFilter\x00')


def _build_ime_font_stub(stub_va, scr_va, str_va, cf_va, timerproc_va):
    def S(x):
        return scr_va + x

    cave_va = cf_va - IME_FONT_CF_OFF
    sub_va = cave_va + IME_SUB_STUB_OFF
    tab_va = cave_va + IME_TAB_OFF
    ctx_va = cave_va + IME_CTX_OFF
    cls_va = cave_va + IME_CLSBUF_OFF

    str_pos = []
    str_off = 0
    for s in IME_FONT_STRS:
        str_pos.append(str_va + str_off)
        str_off += len(s)
    s_imm, s_get, s_set, s_rel, s_setwin, s_u32, s_caret, s_cand, s_setuf = str_pos

    buf = bytearray()
    disp = []
    rel8 = []
    rel32 = []
    marks = {}

    def d32(va):
        disp.append((len(buf), va))
        buf.extend(b'\x00' * 4)

    def r8(mk):
        rel8.append((len(buf), mk))
        buf.append(0)

    def r32(mk):
        rel32.append((len(buf), mk))
        buf.extend(b'\x00' * 4)

    def mark(mk):
        marks[mk] = len(buf)

    buf += b'\x60'                          # pushad（保护全部寄存器）
    buf += b'\xE8\x00\x00\x00\x00'          # call $+5
    base = stub_va + len(buf)
    buf += b'\x5B'                          # pop ebx
    buf += b'\xFF\x83'; d32(S(S_CNT_CALL))  # inc dword [cnt_call]
    if AUDIT_ENABLE:
        buf += b'\xE8' + struct.pack('<i', (cf_va - IME_FONT_CF_OFF + AUDIT_STUB_OFF) - (stub_va + len(buf) + 5))
    if IME_TIMER_ENABLE:
        buf += b'\x83\xBB'; d32(S(S_TIMER_ID)); buf += b'\x00'   # cmp dword [timer_id],0
        buf += b'\x75'; r8('tmrok')            # jne .tmrok
        buf += b'\x8D\x83'; d32(timerproc_va); buf += b'\x50'    # lea eax,[timerproc]; push
        buf += b'\x6A\x32'                    # push 50
        buf += b'\x6A\x00'                    # push 0
        buf += b'\x6A\x00'                    # push 0（hWnd=NULL）
        buf += b'\xFF\x93'; d32(IAT_SETTIMER) # call [SetTimer]
        buf += b'\x89\x83'; d32(S(S_TIMER_ID))# mov [timer_id],eax
        mark('tmrok')
    buf += b'\xFF\x93'; d32(IAT_GETFOCUS)   # call [GetFocus]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('ret')          # jz .ret
    buf += b'\x89\x83'; d32(S(S_HWND))      # mov [hwnd],eax
    buf += b'\xFF\x83'; d32(S(S_CNT_HWND))  # inc dword [cnt_hwnd]
    # ---- imm32 函数解析 ----
    buf += b'\x8B\x83'; d32(S(S_IMM))       # mov eax,[imm]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x85'; r32('immok')        # jnz .immok
    buf += b'\x8D\x83'; d32(s_imm); buf += b'\x50'
    buf += b'\xFF\x93'; d32(IAT_LOADLIB)    # call [LoadLibraryA]
    buf += b'\x89\x83'; d32(S(S_IMM))       # mov [imm],eax
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('ret')          # jz .ret
    for s_va, slot in ((s_get, S_GETCTX), (s_set, S_SETFONT), (s_rel, S_RELCTX), (s_setwin, S_SETWIN), (s_cand, S_SETCAND)):
        buf += b'\x8D\x83'; d32(s_va); buf += b'\x50'
        buf += b'\x8B\x83'; d32(S(S_IMM)); buf += b'\x50'
        buf += b'\xFF\x93'; d32(IAT_GPA)    # call [GetProcAddress]
        buf += b'\x89\x83'; d32(S(slot))    # mov [slot],eax
        buf += b'\x85\xC0'                  # test eax,eax
        buf += b'\x0F\x84'; r32('ret')      # jz .ret
    mark('immok')
    # ---- 异常过滤器：解析 SetUnhandledExceptionFilter 并安装（一次）----
    if not EXC_FILTER_ENABLE:
        buf += bytes([0xE9]); r32('insdone')       # 停用：跳过安装代码
    exc_va = cf_va - IME_FONT_CF_OFF + EXC_OFF
    flt_va = cf_va - IME_FONT_CF_OFF + EXC_FILTER_OFF
    buf += bytes([0x83, 0xBB]); d32(exc_va + 0x1C); buf += bytes([0x00])   # cmp [setuf],0
    buf += bytes([0x0F, 0x85]); r32('ufok')            # jnz .ufok
    buf += bytes([0xC7, 0x83]); d32(exc_va + 0x20); buf += bytes([0x40, 0x01, 0x00, 0x00])  # mov [cnt],0x140
    buf += bytes([0x8B, 0x83]); d32(IAT_UEF)           # mov eax,[UnhandledExceptionFilter 槽]
    buf += bytes([0x25, 0x00, 0x00, 0xFF, 0xFF])       # and eax,0xFFFF0000
    mark('scan')
    buf += bytes([0xFF, 0x8B]); d32(exc_va + 0x20)     # dec dword [cnt]
    buf += bytes([0x0F, 0x84]); r32('ufok')            # jz .ufok（防死循环）
    buf += bytes([0x66, 0x81, 0x38, 0x4D, 0x5A])       # cmp word [eax],0x5A4D
    buf += bytes([0x74]); r8('found')                  # je .found
    buf += bytes([0x2D, 0x00, 0x00, 0x01, 0x00])       # sub eax,0x10000
    buf += bytes([0xEB]); r8('scan')                   # jmp .scan
    mark('found')
    buf += bytes([0x8D, 0x93]); d32(s_setuf)           # lea edx,[str]
    buf += bytes([0x52])                               # push edx
    buf += bytes([0x50])                               # push eax
    buf += bytes([0xFF, 0x93]); d32(IAT_GPA)           # call [GetProcAddress]
    buf += bytes([0x89, 0x83]); d32(exc_va + 0x1C)     # mov [setuf],eax
    mark('ufok')
    buf += bytes([0x83, 0xBB]); d32(exc_va + 0x18); buf += bytes([0x00])   # cmp [inst],0
    buf += bytes([0x75]); r8('insdone')                # jne .insdone
    buf += bytes([0x83, 0xBB]); d32(exc_va + 0x1C); buf += bytes([0x00])   # cmp [setuf],0
    buf += bytes([0x74]); r8('insdone')                # je .insdone
    buf += bytes([0x8D, 0x83]); d32(flt_va); buf += bytes([0x50])          # lea eax,[filter]; push
    buf += bytes([0x8B, 0x83]); d32(exc_va + 0x1C)     # mov eax,[setuf]
    buf += bytes([0xFF, 0xD0])                         # call eax
    buf += bytes([0x89, 0x83]); d32(exc_va + 0x14)     # mov [old],eax
    buf += bytes([0xC7, 0x83]); d32(exc_va + 0x18); buf += bytes([0x01, 0x00, 0x00, 0x00])  # mov [inst],1
    mark('insdone')
    # ---- user32 GetCaretPos 解析 ----
    buf += b'\x8B\x83'; d32(S(S_GETCARET))  # mov eax,[caret]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x85'; r32('caretok')      # jnz .caretok
    buf += b'\x8D\x83'; d32(s_u32); buf += b'\x50'
    buf += b'\xFF\x93'; d32(IAT_GMH)        # call [GetModuleHandleA]（user32 必已加载）
    buf += b'\x8B\xF0'                      # mov esi,eax
    buf += b'\x85\xF6'                      # test esi,esi
    buf += b'\x0F\x84'; r32('rel')               # jz .rel
    buf += b'\x8D\x83'; d32(s_caret); buf += b'\x50'
    buf += b'\x56'                          # push esi
    buf += b'\xFF\x93'; d32(IAT_GPA)        # call [GetProcAddress]
    buf += b'\x89\x83'; d32(S(S_GETCARET))  # mov [caret],eax
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('rel')               # jz .rel
    mark('caretok')
    # ---- 组字字体 ----
    buf += b'\xFF\xB3'; d32(S(S_HWND))      # push dword [hwnd]
    buf += b'\xFF\x93'; d32(S(S_GETCTX))    # call [ImmGetContext]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('ret')          # jz .ret
    buf += b'\x89\x83'; d32(S(S_HIMC))      # mov [hIMC],eax
    buf += b'\x6A\x00'                      # push 0
    buf += b'\x6A\x00'                      # push 0
    buf += b'\x6A\x31'                      # push 0x31（WM_GETFONT）
    buf += b'\xFF\xB3'; d32(S(S_HWND))      # push [hwnd]
    buf += b'\xFF\x93'; d32(IAT_SM)         # call [SendMessageA]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('rel')          # jz .rel
    buf += b'\x8D\x93'; d32(S(S_LF))        # lea edx,[lf]
    buf += b'\x52'                          # push edx
    buf += b'\x6A\x3C'                      # push 60
    buf += b'\x50'                          # push eax
    buf += b'\xFF\x93'; d32(IAT_GETOBJ)     # call [GetObjectA]
    buf += b'\x89\x83'; d32(S(S_GETOBJRC))  # mov [getobjrc],eax
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('rel')          # jz .rel
    buf += b'\x6A\x00'                      # push 0
    buf += b'\xFF\x93'; d32(IAT_GETDC)      # call [GetDC]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('rel')          # jz .rel
    buf += b'\x89\x83'; d32(S(S_HDC))       # mov [hdc],eax
    buf += b'\x6A\x5A'                      # push 90（LOGPIXELSY）
    buf += b'\x50'                          # push eax
    buf += b'\xFF\x93'; d32(IAT_DEVCAPS)    # call [GetDeviceCaps]
    buf += b'\x89\x83'; d32(S(S_DPI))       # mov [dpi],eax
    buf += b'\x8B\x83'; d32(S(S_LF))        # mov eax,[lfHeight]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x74'; r8('nh')                # jz .nh
    buf += b'\xF7\xAB'; d32(S(S_DPI))       # imul dword [dpi]
    buf += b'\xB9\x60\x00\x00\x00'          # mov ecx,96
    buf += b'\xF7\xF9'                      # idiv ecx
    buf += b'\x89\x83'; d32(S(S_LF))        # mov [lfHeight],eax
    mark('nh')
    buf += b'\x8B\x83'; d32(S(S_LF + 4))    # mov eax,[lfWidth]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x74'; r8('nw')                # jz .nw
    buf += b'\xF7\xAB'; d32(S(S_DPI))       # imul dword [dpi]
    buf += b'\xB9\x60\x00\x00\x00'          # mov ecx,96
    buf += b'\xF7\xF9'                      # idiv ecx
    buf += b'\x89\x83'; d32(S(S_LF + 4))    # mov [lfWidth],eax
    mark('nw')
    buf += b'\x8D\x83'; d32(S(S_LF)); buf += b'\x50'
    buf += b'\x8B\x83'; d32(S(S_HIMC)); buf += b'\x50'
    buf += b'\xFF\x93'; d32(S(S_SETFONT))   # call [ImmSetCompositionFontA]
    buf += b'\x89\x83'; d32(S(S_SETRC))     # mov [setrc],eax
    # ---- 组字窗/候选框位置（GetCaretPos → 分别写两个表单）----
    buf += b'\x8D\x93'; d32(cf_va + 40)      # lea edx,[cand.ptCurrentPos]（cand@cf+32，pt@+8）
    buf += b'\x52'                          # push edx
    buf += b'\xFF\x93'; d32(S(S_GETCARET))  # call [GetCaretPos]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('nocf')         # jz .nocf
    # 组字表单：原始光标 × dpi/96
    buf += b'\x8B\x83'; d32(cf_va + 40)     # mov eax,[cand.x]（原始）
    buf += b'\xF7\xAB'; d32(S(S_DPI))       # imul dword [dpi]
    buf += b'\xB9\x60\x00\x00\x00'          # mov ecx,96
    buf += b'\xF7\xF9'                      # idiv ecx
    buf += b'\x89\x83'; d32(cf_va + 4)      # mov [cf.x],eax
    buf += b'\x8B\x83'; d32(cf_va + 44)     # mov eax,[cand.y]（原始）
    buf += b'\xF7\xAB'; d32(S(S_DPI))       # imul dword [dpi]
    buf += b'\xB9\x60\x00\x00\x00'          # mov ecx,96
    buf += b'\xF7\xF9'                      # idiv ecx
    buf += b'\x89\x83'; d32(cf_va + 8)      # mov [cf.y],eax
    buf += b'\xC7\x83'; d32(cf_va); buf += b'\x02\x00\x00\x00'   # mov dword [cf],CFS_POINT
    # 候选框缩放系数默认 1/1（运行中可热调）
    buf += b'\x83\xBB'; d32(S(S_CAND_D)); buf += b'\x00'   # cmp dword [cand_d],0
    buf += b'\x75'; r8('canddef')           # jne .canddef
    buf += b'\xC7\x83'; d32(S(S_CAND_N)); buf += b'\x01\x00\x00\x00'
    buf += b'\xC7\x83'; d32(S(S_CAND_D)); buf += b'\x01\x00\x00\x00'
    mark('canddef')
    # 候选框表单：原始光标 × n/d
    buf += b'\x8B\x83'; d32(cf_va + 40)     # mov eax,[cand.x]
    buf += b'\xF7\xAB'; d32(S(S_CAND_N))    # imul dword [cand_n]
    buf += b'\xF7\xBB'; d32(S(S_CAND_D))    # idiv dword [cand_d]
    buf += b'\x89\x83'; d32(cf_va + 40)     # mov [cand.x],eax
    buf += b'\x8B\x83'; d32(cf_va + 44)     # mov eax,[cand.y]
    buf += b'\xF7\xAB'; d32(S(S_CAND_N))    # imul
    buf += b'\xF7\xBB'; d32(S(S_CAND_D))    # idiv
    buf += b'\x89\x83'; d32(cf_va + 44)     # mov [cand.y],eax
    buf += b'\xC7\x83'; d32(cf_va + 36); buf += b'\x40\x00\x00\x00'  # mov dword [cand.dwStyle],CFS_CANDIDATEPOS
    buf += b'\x8D\x83'; d32(cf_va + 32); buf += b'\x50'      # push &cand
    buf += b'\x8B\x83'; d32(S(S_HIMC)); buf += b'\x50'
    buf += b'\xFF\x93'; d32(S(S_SETCAND))   # call [ImmSetCandidateWindow]
    buf += b'\x89\x83'; d32(S(S_CANDRC))    # mov [candrc],eax
    buf += b'\x8D\x83'; d32(cf_va); buf += b'\x50'          # push &cf
    buf += b'\x8B\x83'; d32(S(S_HIMC)); buf += b'\x50'
    buf += b'\xFF\x93'; d32(S(S_SETWIN))    # call [ImmSetCompositionWindow]
    buf += b'\x89\x83'; d32(S(S_SETWINRC))  # mov [setwinrc],eax
    buf += b'\x8B\x83'; d32(cf_va + 4)      # mov eax,[cf.x]
    buf += b'\x89\x83'; d32(S(S_CFX))       # mov [cfx],eax
    buf += b'\x8B\x83'; d32(cf_va + 8)      # mov eax,[cf.y]
    buf += b'\x89\x83'; d32(S(S_CFY))       # mov [cfy],eax
    buf += b'\x8B\x83'; d32(cf_va + 40)     # mov eax,[cand.x]
    buf += b'\x89\x83'; d32(S(S_CANDX))     # mov [candx],eax
    buf += b'\x8B\x83'; d32(cf_va + 44)     # mov eax,[cand.y]
    buf += b'\x89\x83'; d32(S(S_CANDY))     # mov [candy],eax
    mark('nocf')
    buf += b'\x8B\x83'; d32(S(S_HDC)); buf += b'\x50'
    buf += b'\x6A\x00'                      # push 0
    buf += b'\xFF\x93'; d32(IAT_RELDC)      # call [ReleaseDC]
    mark('rel')
    buf += b'\x8B\x83'; d32(S(S_HIMC)); buf += b'\x50'
    buf += b'\xFF\xB3'; d32(S(S_HWND))      # push [hwnd]
    buf += b'\xFF\x93'; d32(S(S_RELCTX))    # call [ImmReleaseContext]
    # ---- 焦点窗口挂子类过程（即时修复 + 上下文实验）----
    if not IME_SUB_ENABLE:
        buf += b'\xE9'; r32('instdone')     # 停用子类化：直接跳过
    buf += b'\x8B\x83'; d32(S(S_HWND))     # mov eax,[hwnd]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('instdone')     # jz .instdone
    buf += b'\x6A\x3C'                      # push 60
    buf += b'\x8D\x8B'; d32(cls_va); buf += b'\x51'   # lea ecx,[clsbuf]; push
    buf += b'\x50'                          # push eax
    buf += b'\xFF\x93'; d32(IAT_GETCLS)     # call [GetClassNameA]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('instdone')     # jz .instdone
    buf += b'\x8D\x8B'; d32(cls_va)         # lea ecx,[clsbuf]
    buf += b'\x81\x39\x54\x45\x64\x69'   # cmp dword [ecx],'TEdi'
    buf += b'\x0F\x85'; r32('instdone')     # jne .instdone
    buf += b'\x80\x79\x04\x74'             # cmp byte [ecx+4],'t'
    buf += b'\x0F\x85'; r32('instdone')     # jne .instdone
    buf += b'\x8B\x83'; d32(S(S_HWND))     # mov eax,[hwnd]
    buf += b'\x8D\xB3'; d32(tab_va)         # lea esi,[tab]
    buf += b'\xBA\x08\x00\x00\x00'           # mov edx,8
    mark('iloop')
    buf += b'\x8B\x0E'                      # mov ecx,[esi]
    buf += b'\x85\xC9'                      # test ecx,ecx
    buf += b'\x74'; r8('iempty')            # jz .iempty
    buf += b'\x3B\xC1'                      # cmp eax,ecx
    buf += b'\x74'; r8('ihave')             # je .ihave
    buf += b'\x83\xC6\x08'                  # add esi,8
    buf += b'\x4A'                          # dec edx
    buf += b'\x75'; r8('iloop')             # jnz .iloop
    buf += b'\xE9'; r32('instdone')         # jmp .instdone
    mark('iempty')
    buf += b'\x89\x06'                      # mov [esi],eax（记录 hwnd）
    buf += b'\x8B\xC8'                      # mov ecx,eax
    buf += b'\x6A\xFC'                      # push -4（GWLP_WNDPROC）
    buf += b'\x51'                          # push ecx
    buf += b'\xFF\x93'; d32(IAT_GETWINLONG) # call [GetWindowLongA]
    buf += b'\x89\x46\x04'                  # mov [esi+4],eax（原过程）
    buf += b'\x8D\x83'; d32(sub_va); buf += b'\x50'   # lea eax,[subproc]; push
    buf += b'\x6A\xFC'                      # push -4
    buf += b'\xFF\xB3'; d32(S(S_HWND))      # push [hwnd]
    buf += b'\xFF\x93'; d32(IAT_SETWINLONG) # call [SetWindowLongA]
    buf += b'\xE9'; r32('instdone')         # jmp .instdone
    mark('ihave')
    buf += b'\x8B\xC8'                      # mov ecx,eax
    buf += b'\x6A\xFC'                      # push -4
    buf += b'\x51'                          # push ecx
    buf += b'\xFF\x93'; d32(IAT_GETWINLONG) # call [GetWindowLongA]
    buf += b'\x8D\x93'; d32(sub_va)         # lea edx,[subproc]
    buf += b'\x3B\xC2'                      # cmp eax,edx
    buf += b'\x74'; r8('instdone')          # je .instdone
    buf += b'\x89\x46\x04'                  # mov [esi+4],eax（更新原过程）
    buf += b'\x52'                          # push edx
    buf += b'\x6A\xFC'                      # push -4
    buf += b'\xFF\xB3'; d32(S(S_HWND))      # push [hwnd]
    buf += b'\xFF\x93'; d32(IAT_SETWINLONG) # call [SetWindowLongA]
    mark('instdone')
    mark('ret')
    buf += b'\x61'                          # popad
    buf += b'\xC3'                          # ret

    for pos, va in disp:
        struct.pack_into('<i', buf, pos, va - base)
    for pos, mk in rel8:
        d = marks[mk] - (pos + 1)
        if not -128 <= d <= 127:
            raise RuntimeError(f'IME 例程短跳超出范围: {d}')
        buf[pos] = d & 0xFF
    for pos, mk in rel32:
        struct.pack_into('<i', buf, pos, marks[mk] - (pos + 4))
    return bytes(buf)


def _build_subclass_stub(stub_va, fix_va, ctx_va, tab_va, pset_va):
    """子类窗口过程：焦点/组字时做修复与上下文实验；其余消息透明转发给原过程。"""
    buf = bytearray()
    disp = []
    rel8 = []
    rel32 = []
    marks = {}

    def d32(va):
        disp.append((len(buf), va))
        buf.extend(b'\x00' * 4)

    def r8(mk):
        rel8.append((len(buf), mk))
        buf.append(0)

    def r32(mk):
        rel32.append((len(buf), mk))
        buf.extend(b'\x00' * 4)

    def mark(mk):
        marks[mk] = len(buf)

    buf += b'\x55'                          # push ebp
    buf += b'\x8B\xEC'                      # mov ebp,esp
    buf += b'\x53\x56\x57'                 # push ebx/esi/edi
    buf += b'\xE8\x00\x00\x00\x00'        # call $+5
    base = stub_va + len(buf)
    buf += b'\x5B'                          # pop ebx
    buf += b'\x8B\x45\x0C'                 # mov eax,[ebp+0xC]  msg
    buf += b'\x83\xF8\x07'                 # cmp eax,7   WM_SETFOCUS
    buf += b'\x75'; r8('nfoc')               # jne .nfoc
    buf += b'\xE8' + struct.pack('<i', fix_va - (stub_va + len(buf) + 5))  # call fix（焦点时预设）
    buf += b'\x83\xBB'; d32(ctx_va + 4); buf += b'\x00'   # cmp dword [ctx.arm],0
    buf += b'\x0F\x84'; r32('co')           # jz .co
    buf += b'\x83\xBB'; d32(ctx_va + 8); buf += b'\x00'   # cmp dword [ctx.saved],0
    buf += b'\x0F\x85'; r32('co')           # jnz .co（已置）
    buf += b'\x8B\x83'; d32(ctx_va); buf += b'\x85\xC0'  # mov eax,[ctx.focus]; test
    buf += b'\x0F\x84'; r32('co')           # jz .co
    buf += b'\x50'                          # push eax
    buf += b'\x8B\x83'; d32(pset_va)        # mov eax,[pset]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('co')           # jz .co
    buf += b'\xFF\xD0'                      # call eax
    buf += b'\x89\x83'; d32(ctx_va + 8)     # mov [ctx.saved],eax
    buf += b'\xE9'; r32('co')                # jmp .co
    mark('nfoc')
    buf += b'\x83\xF8\x08'                 # cmp eax,8   WM_KILLFOCUS
    buf += b'\x75'; r8('nrest')              # jne .nrest
    buf += b'\x8B\x83'; d32(ctx_va + 8)     # mov eax,[ctx.saved]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('co')           # jz .co
    buf += b'\x50'                          # push eax
    buf += b'\x8B\x83'; d32(pset_va)        # mov eax,[pset]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x0F\x84'; r32('co')           # jz .co
    buf += b'\xFF\xD0'                      # call eax
    buf += b'\xC7\x83'; d32(ctx_va + 8); buf += b'\x00\x00\x00\x00'  # mov [ctx.saved],0
    buf += b'\xE9'; r32('co')                # jmp .co
    mark('nrest')
    buf += b'\x3D\x0D\x01\x00\x00'        # cmp eax,0x10D  WM_IME_STARTCOMPOSITION
    buf += b'\x74'; r8('fixit')              # je .fixit
    buf += b'\x3D\x0F\x01\x00\x00'        # cmp eax,0x10F  WM_IME_COMPOSITION
    buf += b'\x75'; r8('co')                 # jne .co
    mark('fixit')
    buf += b'\x89\x83'; d32(ctx_va + 12)    # mov [ctx.lastmsg],eax（诊断）
    buf += b'\xFF\x83'; d32(ctx_va + 16)    # inc dword [ctx.fixcnt]（诊断）
    buf += b'\xE8' + struct.pack('<i', fix_va - (stub_va + len(buf) + 5))  # call fix（前置）
    mark('co')
    buf += b'\x89\x83'; d32(ctx_va + 12)    # mov [ctx.lastmsg],eax
    # 查表转发
    buf += b'\x8D\x8B'; d32(tab_va)         # lea ecx,[tab]
    buf += b'\xBA\x08\x00\x00\x00'        # mov edx,8
    mark('tloop')
    buf += b'\x8B\x31'                      # mov esi,[ecx]
    buf += b'\x85\xF6'                      # test esi,esi
    buf += b'\x74'; r8('tnone')              # jz .tnone
    buf += b'\x3B\x75\x08'                 # cmp esi,[ebp+8]
    buf += b'\x74'; r8('tfound')             # je .tfound
    buf += b'\x83\xC1\x08'                 # add ecx,8
    buf += b'\x4A'                          # dec edx
    buf += b'\x75'; r8('tloop')              # jnz .tloop
    mark('tnone')
    buf += b'\x33\xFF'                      # xor edi,edi
    buf += b'\xEB'; r8('tcall')              # jmp .tcall
    mark('tfound')
    buf += b'\x8B\x79\x04'                 # mov edi,[ecx+4]
    mark('tcall')
    buf += b'\x85\xFF'                      # test edi,edi
    buf += b'\x74'; r8('tret0')              # jz .tret0
    buf += b'\xFF\x75\x14'                 # push [ebp+0x14]  lParam
    buf += b'\xFF\x75\x10'                 # push [ebp+0x10]  wParam
    buf += b'\xFF\x75\x0C'                 # push [ebp+0x0C]  msg
    buf += b'\xFF\x75\x08'                 # push [ebp+8]     hwnd
    buf += b'\xFF\xD7'                      # call edi
    buf += b'\x5F\x5E\x5B\x5D'            # pop edi/esi/ebx/ebp
    buf += b'\xC2\x10\x00'                 # ret 0x10
    mark('tret0')
    buf += b'\x33\xC0'                      # xor eax,eax
    buf += b'\x5F\x5E\x5B\x5D'            # pop edi/esi/ebx/ebp
    buf += b'\xC2\x10\x00'                 # ret 0x10
    for pos, va in disp:
        struct.pack_into('<i', buf, pos, va - base)
    for pos, mk in rel8:
        d = marks[mk] - (pos + 1)
        if not -128 <= d <= 127:
            raise RuntimeError(f'子类过程短跳超出范围: {d}')
        buf[pos] = d & 0xFF
    for pos, mk in rel32:
        struct.pack_into('<i', buf, pos, marks[mk] - (pos + 4))
    return bytes(buf)


def _build_timerproc_stub(stub_va, fix_va):
    """50ms 线程定时器回调：调用修复例程后返回（TimerProc 是 stdcall）。"""
    buf = bytearray()
    buf += b'\x60'                                  # pushad
    buf += b'\xE8' + struct.pack('<i', fix_va - (stub_va + len(buf) + 5))  # call fix
    buf += b'\x61'                                  # popad
    buf += b'\x33\xC0'                             # xor eax,eax
    buf += b'\xC2\x10\x00'                        # ret 0x10
    return bytes(buf)


def _build_cleanup_stub(stub_va, scr_va):
    """幽灵卸载清理桩：KillTimer，然后补回原指令并跳回。"""
    buf = bytearray()
    disp = []
    rel8 = []

    def d32(va):
        disp.append((len(buf), va))
        buf.extend(b'\x00' * 4)

    def r8(mk):
        rel8.append((len(buf), mk))
        buf.append(0)

    buf += b'\x53'                                  # push ebx
    buf += b'\xE8\x00\x00\x00\x00'              # call $+5
    base = stub_va + len(buf)
    buf += b'\x5B'                                  # pop ebx
    buf += b'\x8B\x8B'; d32(scr_va + S_TIMER_ID)   # mov ecx,[timer_id]
    buf += b'\x85\xC9'                             # test ecx,ecx
    buf += b'\x74'; r8('skip')                      # jz .skip
    buf += b'\x51'                                  # push ecx
    buf += b'\x6A\x00'                             # push 0
    buf += b'\xFF\x93'; d32(IAT_KILLTIMER)         # call [KillTimer]
    buf += b'\xC7\x83'; d32(scr_va + S_TIMER_ID); buf += b'\x00\x00\x00\x00'  # mov dword [timer_id],0
    mark = len(buf)
    buf += b'\x8B\x83'; d32(0x4AE07C)              # mov eax,[ebx+(0x4AE07C-base)]（补回原指令）
    buf += b'\x5B'                                  # pop ebx
    buf += b'\xE9' + struct.pack('<i', IME_CLEANUP_NEXT - (stub_va + len(buf) + 5))  # jmp 0x43C689
    for pos, va in disp:
        struct.pack_into('<i', buf, pos, va - base)
    for pos, mk in rel8:
        d = mark - (pos + 1)
        if not -128 <= d <= 127:
            raise RuntimeError('清理桩短跳超出范围')
        buf[pos] = d & 0xFF
    return bytes(buf)


AUDIT_ENABLE = True
EXC_FILTER_ENABLE = False     # 异常过滤器实验停用（引入菜单/设置异常）


def _build_audit_stubs(stub_va, cb_va, log_va):
    """窗口过程审计：EnumThreadWindows 记录 (hwnd, GWLP_WNDPROC, class)。
    用 bytes([...]) 写机器码，避免转义问题。"""
    import struct as _st

    # --- 调用桩 ---
    stub = bytearray()
    disp1 = []

    def d1(va):
        disp1.append((len(stub), va))
        stub.extend(b'\x00' * 4)

    stub += bytes([0x60])                          # pushad
    stub += bytes([0xE8, 0, 0, 0, 0])              # call $+5
    base1 = stub_va + len(stub)
    stub += bytes([0x5B])                          # pop ebx
    stub += bytes([0xC7, 0x83]); d1(log_va); stub += bytes([0, 0, 0, 0])   # mov dword [ebx+log],0
    stub += bytes([0xFF, 0x93]); d1(IAT_GETTID)    # call [GetCurrentThreadId]
    stub += bytes([0x6A, 0x00])                    # push 0
    stub += bytes([0x8D, 0x93]); d1(cb_va)         # lea edx,[ebx+cb]
    stub += bytes([0x52])                          # push edx
    stub += bytes([0x50])                          # push eax
    stub += bytes([0xFF, 0x93]); d1(IAT_ENUMTW)    # call [EnumThreadWindows]
    stub += bytes([0x61])                          # popad
    stub += bytes([0xC3])                          # ret
    for pos, va in disp1:
        _st.pack_into('<i', stub, pos, va - base1)

    # --- 回调 ---
    cb = bytearray()
    disp2 = []
    rel8 = []
    marks = {}

    def d2(va):
        disp2.append((len(cb), va))
        cb.extend(b'\x00' * 4)

    def r8(mk):
        rel8.append((len(cb), mk))
        cb.append(0)

    def mark(mk):
        marks[mk] = len(cb)

    cb += bytes([0x60])                            # pushad
    cb += bytes([0xE8, 0, 0, 0, 0])                # call $+5
    base2 = cb_va + len(cb)
    cb += bytes([0x5B])                            # pop ebx
    cb += bytes([0x8B, 0x83]); d2(log_va)          # mov eax,[ebx+count]
    cb += bytes([0x83, 0xF8, 0x40])                # cmp eax,0x40
    cb += bytes([0x73]); r8('done')                # jae .done
    cb += bytes([0x6B, 0xC0, 0x18])                # imul eax,eax,24
    cb += bytes([0x8D, 0xBB]); d2(log_va + 4)      # lea edi,[ebx+entries]
    cb += bytes([0x03, 0xF8])                      # add edi,eax
    cb += bytes([0x8B, 0x74, 0x24, 0x24])          # mov esi,[esp+0x24]（pushad 后 hwnd）
    cb += bytes([0x89, 0x37])                      # mov [edi],esi
    cb += bytes([0x6A, 0xFC])                      # push -4
    cb += bytes([0x56])                            # push esi
    cb += bytes([0xFF, 0x93]); d2(IAT_GETWINLONG)  # call [GetWindowLongA]
    cb += bytes([0x89, 0x47, 0x04])                # mov [edi+4],eax
    cb += bytes([0x6A, 0x10])                      # push 16
    cb += bytes([0x8D, 0x57, 0x08])                # lea edx,[edi+8]
    cb += bytes([0x52])                            # push edx
    cb += bytes([0x56])                            # push esi
    cb += bytes([0xFF, 0x93]); d2(IAT_GETCLS)      # call [GetClassNameA]
    cb += bytes([0xFF, 0x83]); d2(log_va)          # inc dword [count]
    mark('done')
    cb += bytes([0x61])                            # popad
    cb += bytes([0xB8, 1, 0, 0, 0])                # mov eax,1
    cb += bytes([0xC2, 0x08, 0x00])                # ret 8
    for pos, va in disp2:
        _st.pack_into('<i', cb, pos, va - base2)
    for pos, mk in rel8:
        d = marks[mk] - (pos + 1)
        if not -128 <= d <= 127:
            raise RuntimeError(f'审计回调短跳超出范围: {d}')
        cb[pos] = d & 0xFF
    return bytes(stub), bytes(cb)


def _build_exc_filter_stub(stub_va, exc_va):
    """未处理异常过滤器：记录异常码/地址/EIP/ESP，然后转交原过滤器。"""
    import struct as _st
    buf = bytearray()
    disp = []
    rel8 = []
    marks = {}

    def d32(va):
        disp.append((len(buf), va))
        buf.extend(b'\x00' * 4)

    def r8(mk):
        rel8.append((len(buf), mk))
        buf.append(0)

    def mark(mk):
        marks[mk] = len(buf)

    buf += bytes([0x60])                     # pushad
    buf += bytes([0xE8, 0, 0, 0, 0])         # call $+5
    base = stub_va + len(buf)
    buf += bytes([0x5B])                     # pop ebx
    buf += bytes([0x8B, 0x44, 0x24, 0x24])   # mov eax,[esp+0x24]（EXCEPTION_POINTERS*）
    buf += bytes([0x85, 0xC0]); buf += bytes([0x74]); r8('out')
    buf += bytes([0x8B, 0x08])               # mov ecx,[eax]
    buf += bytes([0x85, 0xC9]); buf += bytes([0x74]); r8('out')
    buf += bytes([0x8B, 0x11]); buf += bytes([0x89, 0x93]); d32(exc_va)          # code
    buf += bytes([0x8B, 0x51, 0x0C]); buf += bytes([0x89, 0x93]); d32(exc_va + 4)  # ExceptionAddress
    buf += bytes([0x8B, 0x50, 0x04]); buf += bytes([0x89, 0x93]); d32(exc_va + 8)  # ContextRecord
    buf += bytes([0x85, 0xD2]); buf += bytes([0x74]); r8('out')
    buf += bytes([0x8B, 0x8A, 0xB8, 0x00, 0x00, 0x00])   # mov ecx,[edx+0xB8] Eip
    buf += bytes([0x89, 0x8B]); d32(exc_va + 0x0C)
    buf += bytes([0x8B, 0x8A, 0xC4, 0x00, 0x00, 0x00])   # mov ecx,[edx+0xC4] Esp
    buf += bytes([0x89, 0x8B]); d32(exc_va + 0x10)
    mark('out')
    buf += bytes([0x8B, 0x83]); d32(exc_va + 0x14)       # mov eax,[old filter]
    buf += bytes([0x85, 0xC0]); buf += bytes([0x74]); r8('ret0')
    buf += bytes([0xFF, 0x74, 0x24, 0x24])               # push dword [esp+0x24]
    buf += bytes([0xFF, 0xD0])                           # call eax
    buf += bytes([0x61])                                 # popad
    buf += bytes([0xC2, 0x04, 0x00])                     # ret 4
    mark('ret0')
    buf += bytes([0x33, 0xC0])                           # xor eax,eax
    buf += bytes([0x61])
    buf += bytes([0xC2, 0x04, 0x00])
    for pos, va in disp:
        _st.pack_into('<i', buf, pos, va - base)
    for pos, mk in rel8:
        d = marks[mk] - (pos + 1)
        if not -128 <= d <= 127:
            raise RuntimeError(f'异常过滤器短跳超出范围: {d}')
        buf[pos] = d & 0xFF
    return bytes(buf)


def patch_ime_focus_dpi(data: bytearray) -> bytearray:
    """写入输入法组字修复例程 + 50ms 定时器 + 卸载清理桩。"""
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
        raise RuntimeError('输入法字体：找不到 .cave 节')
    cave_va = DPI_WRAP_IB + cave_rva
    stub_va = cave_va + IME_FONT_STUB_OFF
    cf_va = cave_va + IME_FONT_CF_OFF
    timer_va = cave_va + IME_TIMERPROC_OFF
    clean_va = cave_va + IME_CLEANUP_STUB_OFF
    scr_va = cave_va + IME_FONT_SCRATCH_OFF
    str_va = cave_va + IME_FONT_STR_OFF
    stub = _build_ime_font_stub(stub_va, scr_va, str_va, cf_va, timer_va)
    sub_va = cave_va + IME_SUB_STUB_OFF
    ctx_va = cave_va + IME_CTX_OFF
    tab_va = cave_va + IME_TAB_OFF
    sub = _build_subclass_stub(sub_va, stub_va, ctx_va, tab_va, scr_va - IME_FONT_SCRATCH_OFF + 0xB0)
    strs = b''.join(IME_FONT_STRS)
    if IME_TIMER_ENABLE:
        timer = _build_timerproc_stub(timer_va, stub_va)
        clean = _build_cleanup_stub(clean_va, scr_va)
    else:
        timer = b''
        clean = b''
    if len(stub) > IME_FONT_CF_OFF - IME_FONT_STUB_OFF:
        raise RuntimeError('输入法字体：例程过长')
    reg_end = max(IME_FONT_STR_OFF + len(strs),
                  IME_SUB_STUB_OFF + len(sub),
                  IME_CTX_OFF + 12,
                  IME_TAB_OFF + 64,
                  IME_CLSBUF_OFF + 60)
    if IME_TIMER_ENABLE:
        reg_end = max(reg_end, IME_TIMERPROC_OFF + len(timer), IME_CLEANUP_STUB_OFF + len(clean))
    if any(data[cave_raw + IME_FONT_STUB_OFF: cave_raw + reg_end]):
        raise RuntimeError('输入法字体：例程/数据位置非空')
    data[cave_raw + IME_FONT_STUB_OFF: cave_raw + IME_FONT_STUB_OFF + len(stub)] = stub
    data[cave_raw + IME_FONT_STR_OFF: cave_raw + IME_FONT_STR_OFF + len(strs)] = strs
    data[cave_raw + IME_SUB_STUB_OFF: cave_raw + IME_SUB_STUB_OFF + len(sub)] = sub
    if AUDIT_ENABLE:
        astub, acb = _build_audit_stubs(cave_va + AUDIT_STUB_OFF, cave_va + AUDIT_CB_OFF, cave_va + AUDIT_LOG_OFF)
        if any(data[cave_raw + AUDIT_STUB_OFF: cave_raw + AUDIT_LOG_OFF + 0x604]):
            raise RuntimeError('审计：位置非空')
        data[cave_raw + AUDIT_STUB_OFF: cave_raw + AUDIT_STUB_OFF + len(astub)] = astub
        data[cave_raw + AUDIT_CB_OFF: cave_raw + AUDIT_CB_OFF + len(acb)] = acb
        if EXC_FILTER_ENABLE:
            ef = _build_exc_filter_stub(cave_va + EXC_FILTER_OFF, cave_va + EXC_OFF)
            tail = data[cave_raw + EXC_OFF: cave_raw + EXC_FILTER_OFF + len(ef)]
            if any(tail):
                raise RuntimeError('异常过滤器：位置非空')
            data[cave_raw + EXC_FILTER_OFF: cave_raw + EXC_FILTER_OFF + len(ef)] = ef
    if IME_TIMER_ENABLE:
        data[cave_raw + IME_TIMERPROC_OFF: cave_raw + IME_TIMERPROC_OFF + len(timer)] = timer
        data[cave_raw + IME_CLEANUP_STUB_OFF: cave_raw + IME_CLEANUP_STUB_OFF + len(clean)] = clean
        fo = IME_CLEANUP_SITE - 0x400C00
        if bytes(data[fo:fo + 5]) != IME_CLEANUP_ORIG:
            raise RuntimeError(f'输入法字体：0x{IME_CLEANUP_SITE:X} 原始字节不符')
        data[fo:fo + 5] = b'\xE9' + struct.pack('<i', clean_va - (IME_CLEANUP_SITE + 5))
    print('输入法修复已应用: 组字字体/位置（由 request 包装驱动）')
    return data


# 窗口尺寸存档修复：幽灵的"保存窗口配置"函数（0x462848）在 SSP 的消息上下文（aware）里
# 执行，其中的 ClientWidth/ClientHeight 取值走 WinAPI 客户区 → 拿到物理尺寸；而恢复（load，
# GDISCALED）把存档值当逻辑值应用 → 每次带 Todo 退出再启动就 ×1.5。这里把保存函数整体
# 包一层：保存期间线程置 GDISCALED，使 accessor 返回逻辑值，存档稳定不再累积。
PROF_SAVE_ENABLE = False      # 暂停：包装保存函数仍引发关闭窗口崩溃，待稳后重做
PROF_SAVE_SITE = 0x462848
PROF_SAVE_ORIG = bytes.fromhex('53 8B D8 80 BB 12 03 00 00 00')  # push ebx; mov ebx,eax; cmp byte [ebx+0x312],0
PROF_SAVE_BODY = 0x462852       # 补完 push ebx / mov ebx,eax / cmp 后的继续处
PROF_SAVE_STUB_OFF = 0x2C00
PROF_SAVE_SAVED_OFF = 0x48      # 数据区 +0x48：保存的旧上下文


def _build_prof_save_stub(stub_va, data_va):
    pset_va = data_va + DPI_WRAP_PSET
    saved_va = data_va + PROF_SAVE_SAVED_OFF
    buf = bytearray()
    disp = []
    rel8 = []
    marks = {}

    def d32(va):
        disp.append((len(buf), va))
        buf.extend(b'\x00' * 4)

    def r8(mk):
        rel8.append((len(buf), mk))
        buf.append(0)

    def mark(mk):
        marks[mk] = len(buf)

    buf += b'\x53'                          # push ebx
    buf += b'\x8B\xD8'                      # mov ebx,eax（补回原序言）
    buf += b'\x50'                          # push eax（保存 self）
    buf += b'\xE8\x00\x00\x00\x00'        # call $+5
    base = stub_va + len(buf)
    buf += b'\x5A'                          # pop edx（本地基址）
    buf += b'\x8B\x82'; d32(pset_va)        # mov eax,[edx+pset]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x74'; r8('callbody')           # jz .callbody
    buf += b'\x6A\xFB'                      # push -5（UNAWARE_GDISCALED）
    buf += b'\xFF\xD0'                      # call eax
    buf += b'\x89\x82'; d32(saved_va)       # mov [edx+saved],eax
    mark('callbody')
    buf += b'\x58'                          # pop eax（恢复 self）
    buf += b'\x80\xBB\x12\x03\x00\x00\x00'    # cmp byte [ebx+0x312],0（补回标志检查）
    buf += b'\xE8' + struct.pack('<i', PROF_SAVE_BODY - (stub_va + len(buf) + 5))  # call 原函数体
    buf += b'\x50'                          # push eax（保存返回值）
    buf += b'\xE8\x00\x00\x00\x00'        # call $+5
    buf += b'\x5A'                          # pop edx
    buf += b'\x8B\x82'; d32(pset_va)        # mov eax,[edx+pset]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x74'; r8('done')               # jz .done
    buf += b'\x8B\xCA'                      # mov ecx,eax（pset）
    buf += b'\x8B\x82'; d32(saved_va)       # mov eax,[edx+saved]
    buf += b'\x85\xC0'                      # test eax,eax
    buf += b'\x74'; r8('done')               # jz .done
    buf += b'\x50'                          # push eax
    buf += b'\xFF\xD1'                      # call ecx
    mark('done')
    buf += b'\x58'                          # pop eax
    buf += b'\xC3'                          # ret（ebx 由原函数体自身的 pop 恢复）
    for pos, va in disp:
        struct.pack_into('<i', buf, pos, va - base)
    for pos, mk in rel8:
        d = marks[mk] - (pos + 1)
        if not -128 <= d <= 127:
            raise RuntimeError(f'存档包裹短跳超出范围: {d}')
        buf[pos] = d & 0xFF
    return bytes(buf)


def patch_profile_size_fix(data: bytearray) -> bytearray:
    """包装窗口配置保存函数：保存期间 GDISCALED，存档写成逻辑尺寸。"""
    if not PROF_SAVE_ENABLE:
        return data
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
        raise RuntimeError('存档修复：找不到 .cave 节')
    fo = PROF_SAVE_SITE - 0x400C00
    if bytes(data[fo:fo + len(PROF_SAVE_ORIG)]) != PROF_SAVE_ORIG:
        raise RuntimeError(f'存档修复：0x{PROF_SAVE_SITE:X} 原始字节不符')
    cave_va = DPI_WRAP_IB + cave_rva
    stub_va = cave_va + PROF_SAVE_STUB_OFF
    data_va = cave_va + DPI_WRAP_DATA_OFF
    stub = _build_prof_save_stub(stub_va, data_va)
    if any(data[cave_raw + PROF_SAVE_STUB_OFF: cave_raw + PROF_SAVE_STUB_OFF + len(stub)]):
        raise RuntimeError('存档修复：桩位置非空')
    data[cave_raw + PROF_SAVE_STUB_OFF: cave_raw + PROF_SAVE_STUB_OFF + len(stub)] = stub
    data[fo:fo + 5] = b'\xE9' + struct.pack('<i', stub_va - (PROF_SAVE_SITE + 5))
    data[fo + 5:fo + 10] = b'\x90' * 5
    print('窗口尺寸存档修复已应用: 保存函数包裹 GDISCALED')
    return data


# Notify 退出崩溃规避：幽灵有 unload 导出（SSP 关闭时调用）。包装它：先 FindWindowA
# ('Tnotifyform') + DestroyWindow 把 Notify 窗口销毁，之后 SSP 关闭流程再向它派发消息
# 只会得到"无效句柄"的普通失败，不会踩到被写坏的过程指针。
UNLOAD_WRAP_ENABLE = True
UNLOAD_STUB_OFF = 0x3340
UNLOAD_STR_OFF = 0x33A0
UNLOAD_ORIG_RVA = 0xAA234
IAT_FINDWINDOWA = 0x4B3704
IAT_DESTROYWINDOW = 0x4B3750
UNLOAD_CLASS_STR = b'Tnotifyform\x00'


def _build_unload_stub(stub_va, str_va, orig_va):
    import struct as _st
    buf = bytearray()
    disp = []
    rel8 = []
    marks = {}

    def d32(va):
        disp.append((len(buf), va))
        buf.extend(b'\x00' * 4)

    def r8(mk):
        rel8.append((len(buf), mk))
        buf.append(0)

    def mark(mk):
        marks[mk] = len(buf)

    buf += bytes([0x60])                       # pushad
    buf += bytes([0xE8, 0, 0, 0, 0])           # call $+5
    base = stub_va + len(buf)
    buf += bytes([0x5B])                       # pop ebx
    buf += bytes([0x6A, 0x00])                 # push 0（lpWindowName=NULL）
    buf += bytes([0x8D, 0x83]); d32(str_va)    # lea eax,[ebx+str]
    buf += bytes([0x50])                       # push eax
    buf += bytes([0xFF, 0x93]); d32(IAT_FINDWINDOWA)     # call [FindWindowA]
    buf += bytes([0x85, 0xC0])                 # test eax,eax
    buf += bytes([0x74]); r8('skip')           # jz .skip
    buf += bytes([0x50])                       # push hwnd
    buf += bytes([0xFF, 0x93]); d32(IAT_DESTROYWINDOW)   # call [DestroyWindow]
    mark('skip')
    buf += bytes([0x61])                       # popad
    buf += bytes([0xE9])                       # jmp 原 unload
    rel8_pos = None
    orig_off = len(buf)
    buf.extend(b'\x00' * 4)
    for pos, va in disp:
        _st.pack_into('<i', buf, pos, va - base)
    for pos, mk in rel8:
        d = marks[mk] - (pos + 1)
        if not -128 <= d <= 127:
            raise RuntimeError(f'unload 桩短跳超出范围: {d}')
        buf[pos] = d & 0xFF
    _st.pack_into('<i', buf, orig_off, orig_va - (stub_va + orig_off + 4))
    return bytes(buf)


def patch_unload_notify(data: bytearray) -> bytearray:
    """包装 unload 导出：先销毁 Notify 窗口，再走原流程。"""
    if not UNLOAD_WRAP_ENABLE:
        return data
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
        raise RuntimeError('unload 包装：找不到 .cave 节')

    def rva_off(rva):
        for i in range(nsec):
            off = sec + 40 * i
            va = _u32(data, off + 12)
            vsz = _u32(data, off + 8)
            raw = _u32(data, off + 20)
            rsz = _u32(data, off + 16)
            if va <= rva < va + max(vsz, rsz):
                return raw + (rva - va)
        raise RuntimeError(f'unload 包装：RVA 0x{rva:X} 不在节内')

    ed_rva = _u32(data, opt + 96)
    if ed_rva == 0:
        raise RuntimeError('unload 包装：无导出表')
    eo = rva_off(ed_rva)
    nnam = _u32(data, eo + 24)
    afn = _u32(data, eo + 28)
    anm = _u32(data, eo + 32)
    aord = _u32(data, eo + 36)
    ordi = None
    for i in range(nnam):
        no = rva_off(_u32(data, rva_off(anm) + 4 * i))
        end = no
        while data[end] != 0:
            end += 1
        if bytes(data[no:end]) == b'unload':
            ordi = _u16(data, rva_off(aord) + 2 * i)
            break
    if ordi is None:
        raise RuntimeError('unload 包装：导出缺失')
    cur = _u32(data, rva_off(afn) + 4 * ordi)
    if cur != UNLOAD_ORIG_RVA:
        raise RuntimeError(f'unload 包装：导出地址不符 (0x{cur:X})')
    cave_va = DPI_WRAP_IB + cave_rva
    stub_va = cave_va + UNLOAD_STUB_OFF
    str_va = cave_va + UNLOAD_STR_OFF
    stub = _build_unload_stub(stub_va, str_va, DPI_WRAP_IB + UNLOAD_ORIG_RVA)
    if any(data[cave_raw + UNLOAD_STUB_OFF: cave_raw + UNLOAD_STR_OFF + len(UNLOAD_CLASS_STR)]):
        raise RuntimeError('unload 包装：位置非空')
    data[cave_raw + UNLOAD_STUB_OFF: cave_raw + UNLOAD_STUB_OFF + len(stub)] = stub
    data[cave_raw + UNLOAD_STR_OFF: cave_raw + UNLOAD_STR_OFF + len(UNLOAD_CLASS_STR)] = UNLOAD_CLASS_STR
    struct.pack_into('<I', data, rva_off(afn) + 4 * ordi, cave_rva + UNLOAD_STUB_OFF)
    print('Notify 退出规避已应用: unload 导出包装（先销毁 Notify 窗口）')
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

    # 数据区（PSET/GDWF 初始为 0）
    blob = b'\x00' * 4 + b'user32.dll\x00' + b'\x00' * (DPI_WRAP_SETNAME - DPI_WRAP_USER32 - 11) \
        + b'SetThreadDpiAwarenessContext\x00'
    blob += b'\x00' * (DPI_GETDPI_SLOT - len(blob))
    blob += b'\x00' * 4
    blob += b'GetDpiForWindow\x00'
    data[cave_raw + DPI_WRAP_DATA_OFF: cave_raw + DPI_WRAP_DATA_OFF + len(blob)] = blob

    targets = (('request', DPI_WRAP_REQ_OFF), ('load', DPI_WRAP_LOAD_OFF))
    for nm, off_ in targets:
        ordi, frva = found[nm]
        sv = DPI_WRAP_IB + cave_rva + off_
        dv = DPI_WRAP_IB + cave_rva + DPI_WRAP_DATA_OFF
        tv = DPI_WRAP_IB + frva
        stub = _build_dpi_wrap_stub(sv, dv, tv, insert_font_call=(nm == 'request'))
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


# ------------------------------------------------------------- misaki.dll（透明窗命中区）
# 幽灵的透明窗（文字信息窗 / 模拟时钟 / 倒计时）是 WS_EX_LAYERED +
# UpdateLayeredWindow 逐像素 alpha 窗口，图层位图由 misaki.dll 管理；系统按图层
# alpha 做逐像素命中判定（alpha==0 穿透），DPI 虚拟化下只有字形能命中、拖不动。
# 这里把 misaki 的 allclear 清零填充值改成 0x01（alpha=1/255，肉眼不可见但可命中）
# → 整个窗口进入命中区。配合 first.dll 侧把信息窗窗口高度改成“只剩文字区”
# （见 PATCHES 第 3 条），整窗命中即等于文字区命中；时钟本就整窗可拖。
# 只改 misaki 自己 allclear 的两处行清零调用，不影响其它零填充。
MISAKI_ORIG_CRC32 = 0x5B172E3D
MISAKI_ORIG_SIZE = 482816
MISAKI_CAVE_VA = 0x465584          # 代码段尾部零填充（1148 字节，无任何引用）
MISAKI_ZFILL_WRAP = 0x406C38       # xor ecx,ecx; call _FillChar; ret（清零助手）
MISAKI_FILLCHAR = 0x4029C4         # Delphi _FillChar（EAX=目标, EDX=字节数, CL=值）
MISAKI_SITE_A = 0x4622D4           # allclear 32bpp 行清零 call
MISAKI_SITE_B = 0x462333           # allclear 24bpp 行清零 call
MISAKI_INFO_W = 148                # 信息窗宽度（行字节 592/444）
MISAKI_CLOCK_W = 64                # 时钟宽度（行字节 256/192）


def _misaki_call(site_va, target_va):
    return b'\xE8' + struct.pack('<i', target_va - (site_va + 5))


def _build_misaki_stub(cave_va):
    """小桩：进入时 EAX=行指针, EDX=行字节数, EBX=图层id, EDI=行号, ESI=剩余行数。

    不调用任何方法、不用绝对地址，直接用循环里现成的寄存器判断图层：
      高度 = EDI + ESI；行字节数 = 宽度×4（32bpp 循环）或 宽度×3（24bpp 循环）。

    - 信息窗（148 宽：592/444 行字节，128 或 64 高都算）：整层填 0x01；
    - 时钟（64 宽：256/192 行字节）：整体填 0x01；
    - 其余图层（倒计时等）：原样清零。
    """
    code = bytearray()
    fix = []
    lab = {}

    def emit(*bs):
        code.extend(bs)

    def cmp_eax(v):
        emit(0x3D); code.extend(struct.pack('<I', v))

    def cmp_edx(v):
        emit(0x81, 0xFA); code.extend(struct.pack('<I', v))

    def cj(cc, label):
        fix.append((len(code), label)); emit(cc, 0)

    def uj(label):
        fix.append((len(code), label)); emit(0xEB, 0)

    emit(0x50)                  # push eax（保存行指针）
    emit(0x8D, 0x04, 0x3E)      # lea eax,[edi+esi] ; 高度
    cmp_eax(128)
    cj(0x74, 'size')            # je  -> 再看行字节
    cmp_eax(64)
    cj(0x75, 'zero')            # jne -> 其它图层原样清零

    lab['size'] = len(code)
    cmp_edx(MISAKI_INFO_W * 4)  # 信息窗
    cj(0x74, 'one')
    cmp_edx(MISAKI_INFO_W * 3)
    cj(0x74, 'one')
    cmp_edx(MISAKI_CLOCK_W * 4) # 时钟
    cj(0x74, 'one')
    cmp_edx(MISAKI_CLOCK_W * 3)
    cj(0x74, 'one')
    uj('zero')

    lab['one'] = len(code)              # 填 0x01（命中区）
    emit(0x58)                          # pop eax
    emit(0xB1, 0x01)                    # mov cl,1
    emit(0xE9); code.extend(struct.pack('<i', MISAKI_FILLCHAR - (cave_va + len(code) + 4)))

    lab['zero'] = len(code)             # 原样清零
    emit(0x58)                          # pop eax
    emit(0xE9); code.extend(struct.pack(
        '<i', MISAKI_ZFILL_WRAP - (cave_va + len(code) + 4)))

    for pos, label in fix:
        code[pos + 1] = (lab[label] - (pos + 2)) & 0xFF
    return bytes(code)


def build_misaki() -> bytes:
    """构建 output/misaki.dll 并返回补丁后的数据。"""
    base = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.join(base, 'input', 'misaki.dll')
    out_path = os.path.join(base, 'output', 'misaki.dll')
    with open(src_path, 'rb') as f:
        orig = f.read()
    crc = zlib.crc32(orig) & 0xFFFFFFFF
    if len(orig) != MISAKI_ORIG_SIZE or crc != MISAKI_ORIG_CRC32:
        raise RuntimeError(
            f'input/misaki.dll 与预期不符 (size={len(orig)} crc32={crc:08x}，'
            f'预期 size={MISAKI_ORIG_SIZE} crc32={MISAKI_ORIG_CRC32:08x})')

    data = bytearray(orig)
    fo = MISAKI_CAVE_VA - 0x400C00
    stub = _build_misaki_stub(MISAKI_CAVE_VA)
    if any(data[fo:fo + len(stub)]):
        raise RuntimeError('misaki：cave 位置非空')
    data[fo:fo + len(stub)] = stub
    for site in (MISAKI_SITE_A, MISAKI_SITE_B):
        off = site - 0x400C00
        want = _misaki_call(site, MISAKI_ZFILL_WRAP)
        if bytes(data[off:off + 5]) != want:
            raise RuntimeError(f'misaki：0x{site:X} 原始字节不符')
        data[off:off + 5] = _misaki_call(site, MISAKI_CAVE_VA)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as f:
        f.write(bytes(data))
    print(f'misaki.dll 写入完成 → {out_path}')
    print(f'  allclear 行清零填充值改为 0x01（信息窗整层 / 时钟整窗，1/255 不可见）')
    return bytes(data)


def deploy_misaki(data: bytes, dst: str):
    """部署 misaki.dll：校验目标当前内容（只接受原始/本脚本历史版本），首次备份原文件。"""
    with open(dst, 'rb') as f:
        cur = f.read()
    cur_crc = zlib.crc32(cur) & 0xFFFFFFFF
    known = (MISAKI_ORIG_CRC32, 0x0BF872B8, 0x3E1F48F3, 0xED6A0FDE, 0xE70CC889,
             0xAD29F5BA, 0x782E2359, 0xC53C5D89,
             zlib.crc32(data) & 0xFFFFFFFF)
    if cur_crc not in known:
        raise RuntimeError(f'misaki 部署目标当前内容未知 (crc32={cur_crc:08x})，拒绝覆盖: {dst}')
    bak = os.path.join(os.path.dirname(os.path.abspath(dst)), 'misaki.dll.bak')
    if not os.path.exists(bak):
        with open(bak, 'wb') as f:
            f.write(cur)
        print(f'已备份原始文件 → {bak}')
    with open(dst, 'wb') as f:
        f.write(data)
    print(f'已复制 → {dst}')


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
    # 拖动坐标换算（物理像素 → 虚拟坐标）
    if DPI_ANCHOR_ENABLE:
        data = patch_dpi_anchor(data)
    # 系统字体初始化包装（状态栏提示字等系统字体取 96dpi 规格）
    if DPI_SYSFONT_ENABLE:
        data = patch_dpi_sysfont(data)
    # 状态栏类补 CS_HREDRAW（仅 TStatusBar，CreateParams 调用点透明桩）
    if CRPARAMS_HREDRAW_ENABLE:
        data = patch_createparams_hredraw(data)
    # 输入法组字/候选窗：控件焦点期间线程置 GDISCALED（与虚拟化窗口一致）
    if IME_FOCUS_DPI_ENABLE:
        data = patch_ime_focus_dpi(data)
    # 窗口尺寸存档修复（保存时按逻辑尺寸写盘）
    data = patch_profile_size_fix(data)
    # Notify 退出崩溃规避：unload 时先销毁 Notify 窗口
    data = patch_unload_notify(data)

data = patch_aitxt(data)

os.makedirs(os.path.dirname(DLL_OUT), exist_ok=True)
with open(DLL_OUT, 'wb') as f:
    f.write(data)

print(f'first.dll 写入完成 → {DLL_OUT}')

# ---- misaki.dll（透明窗命中区：整层 alpha=1，1/255 不可见但可命中）----
misaki_data = build_misaki()

# ---- 部署：命令行第一个参数 = ghost master 目录（或其下任一 dll 文件路径）----
if len(sys.argv) >= 2:
    dst = sys.argv[1]
    target_dir = dst if os.path.isdir(dst) else os.path.dirname(os.path.abspath(dst))
    first_dst = os.path.join(target_dir, 'first.dll')
    shutil.copy2(DLL_OUT, first_dst)
    print(f'已复制 → {first_dst}')
    deploy_misaki(misaki_data, os.path.join(target_dir, 'misaki.dll'))

print(f'  写入: {ok}  截断: {trunc}  跳过: {skip}')
