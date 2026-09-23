#!/usr/bin/env python3
"""
一次完成 first.dll 的三类补丁：文本翻译（CSV）+ 兼容补丁 + AITXT 词库。
输出到 output/first.dll（可选：命令行第一个参数 = 额外复制到的部署路径）。

文本翻译（CSV）：
  Offset = 写入位置，Length = 最大字节数，Type = code|rsrc|font。
  - code：off-4 处为 4 字节小端长度，写入后更新长度并清零剩余
  - rsrc：off-1 处为 1 字节长度，写入后更新长度并清零剩余
  - font：无长度前缀，用 \\x00 补齐

兼容补丁（写入前逐字节校验原值）：
  1. NOTIFY -> 按 GET 分发（把 0x719E9 处的 jne 填成 NOP）
     first.dll 只实现了 GET；SSP 2.5.33+ 在 cantalk=0 时会把后台事件
     以 NOTIFY 发来，之前会收到 400 并卡死状态机。
  2. r"\\![enter,inductionmode]" 字符串长度 23 -> 0（0x79E08）
     诱导模式会让 cantalk 永远保持 false，导致后台事件走 NOTIFY
     （响应被忽略）、泡澡结束的对话不可见。

AITXT（词库）：
  aitxt_translated.txt（UTF-8）-> GBK -> 加密数据块，覆盖 PE 资源目录
  中定位到的 AITXT 资源，并更新资源数据项的 Size 字段。
  写入前做 round-trip 校验。

链接化补丁（海原雄山）：
  「自动加链接」名单由 7 段固定序列注册（push ebp / mov eax,<常量> / call
  0x4AA838 / pop ecx），名单区后紧接代码，没有空位。这里把最后一段（木野さん）
  的 call 重定向到新增的可执行节 .cave 中的小桩：桩内先补完原调用，再对
  「海原雄山」常量（命中处理表里已有，翻译表会把它改写成 GBK）调用一次注册。
  桩为位置无关代码（不依赖镜像基址）。

RSS 链接补丁（OnAnchorSelect 打开浏览器）：
  SSP 把头条展开成 \\_a[URL]title\\_a 锚点，点击后发 OnAnchorSelect，Ref0=URL；
  first.dll 对该 ID 匹配名字失败后落到兜底「……ん？」，SSP 便不再打开链接。
  这里把兜底分支入口重定向到 .cave 中的第二个桩：若 Ref0 以 "http" 开头，
  在节内缓冲里拼出 "\\![open,browser,<URL>]" 作为响应脚本返回；否则走原兜底。
"""
import csv, hashlib, os, sys, struct, shutil, unicodedata

SHIFTJIS_OFFSETS = {
    0xD6BB0, 0xD6BF0, 0xD6C4B, 0xD7E4E, 0xD7E8C, 0xD7ED1,
    0xD7F12, 0xD7F53, 0xD7F99, 0xD7FE3, 0xD802B, 0xD806F,
}

# （偏移, 原始字节, 替换字节）
PATCHES = [
    # 1. NOTIFY -> 按 GET 分发
    (0x719E9, bytes.fromhex('0F 85 AF 7E 00 00'), b'\x90' * 6),
    # 2. r"\![enter,inductionmode]" 字符串长度 23 -> 0
    (0x79E08, bytes.fromhex('17 00 00 00'), bytes.fromhex('00 00 00 00')),
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

# ---- 双击判定补丁（只屏蔽 choosing + 搜索对话框；纯读请求/响应，不碰栈残留）----
CAVE_B_OFF = 0x500                 # 双击判定桩（读请求 Status + 搜索框标志）
CAVE_A_OFF = 0x600                 # 响应监控桩（维护搜索框标志）
FLAG_OFF = 0x8C0                   # 搜索框标志（dword：0=关 1=开）
MARK_OFF = 0x8C4                   # 当前游戏标记（1=视力游戏；OnQu*/OnTy* 清 0）
                                   # 视力游戏进行中双击直接吞掉（等它自己结束）
PENDING_OFF = 0x8C8                # 退出待处理字节（1=本次退出响应需前置关闭输入框命令）
GAMELEFT_OFF = 0x8CC               # 游戏已退出字节（1=重置前不再让游戏进度事件继续）
SWALLOW_OFF = 0x8CD                # 本次响应待吞字节（GAMELEFT 期间的游戏进度事件）
TYPING_CLOSEQ_OFF = 0x6E200        # formCloseQuery：CanClose := 窗体.已提交标志[Self+0x321]
TYPING_CLOSEQ_ORIG = bytes.fromhex('8A 80 21 03 00 00 88 01 C3')
TYPING_CLOSEQ_VA = 0x46EE00
CAVE_Q_OFF = 0x10C0                # 跳板桩：GAMELEFT 时放行关闭（配合 WM_CLOSE 静音关框）
CLOSE_CMD_OFF = 0x1010             # 常量：退出时前置到响应的收尾命令
# 说明：问答游戏的答题框是 SSP 的 inputbox；打字游戏的框是 DLL 自己的窗体
#      （由辅助桩 WM_CLOSE 关闭，不靠这些命令）。多关几个箱型无副作用。
CLOSE_CMD = (b'\\![quicksection,false]'
             b'\\![close,communicatebox]'
             b'\\![close,teachbox]'
             b'\\![close,inputbox,__SYSTEM_ALL_INPUT__]')
PBUF_RC_OFF = 0x10F8               # 前置拼接缓冲：refcount（须在 CLOSE_CMD 之后）
PBUF_LEN_OFF = 0x10FC              # 长度
PBUF_DATA_OFF = 0x1100             # 数据（cap 到 0x2000）

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

       1) 事件名（[ebp-0x4c]）以 OnUs / OnGo / OnQu / OnTy / OnEy 开头
          → 输入框标志清零（FLAG_OFF = 0）。覆盖：
            OnUserInput*（取消/超时）、OnGoogle（搜索提交）、
            OnQuiz*、OnTypinggame*、OnEyesightgame*（游戏输入框提交与退出等）。
          （按前缀清可保证游戏中途退出/提交后标志不会卡住——曾出现
            "问答/视力退出后双击打不开主菜单"的卡标志问题。）
          OnQuizLeave / OnTypinggameLeave（游戏「退出」）时另置 PENDING 和
          GAMELEFT：
            - PENDING → 该次退出响应最前面插入 CLOSE_CMD（关普通输入框）；
            - GAMELEFT → 游戏退出后，一旦再有游戏自己的事件（OnQuiz*/
              OnTypinggame* 里非 Leave/Enter 的进度事件，如提交/下一题/
              超时）进来，就把「本次响应」整条吞掉（清响应）。原因：原版
              退出处理器不会真正终止游戏流程，残留输入框上回车/超时会继续
              raise 下一题、不断出新题（打字框还不是 inputbox，close 命令
              关不掉）。吞掉进度响应后游戏链就断了，游戏窗体由 .setpend 的辅助桩 WM_CLOSE 关闭（见 _build_closebox_stub）；重新进入游戏时（OnQuizEnter /
              OnTypinggameEnter）清 GAMELEFT。
        2) 否则响应（[ebp-0x1c]）里含 "\\![open,inputbox"（前缀）→ 标志置 1。
           注：实测挂载点处响应里只出现该前缀（",OnGoogle,-1]" 等后缀是之后
           才拼上的），所以此判定对所有输入框（搜索/问答/游戏）一视同仁；
           这一点与原版 MATERIA 略有差异（其游戏输入框可双击）。
       3) 最后复刻被覆盖的 cmp/jne 并跳回原路径。

       【防崩】[ebp-0x4c] 对没有 ID 的请求（SSP 协议探测 GET Version
       SHIORI/2.6 等）是未初始化的栈残留，直接解引用会崩（曾导致 SSP
       降级 2.2、ghost 打不开）。这里给这段读取装了 SEH 保护：
       一旦异常，处理器把 Eip 改到收尾处，安全跳过事件名判定。

       【实现】所有分支用标签名 + 回填（fixups），指令长度由 len(b) 决定，
       避免手算偏移滚雪球；分支距离超界会在回填时 assert 报错。
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
    jcc8(0x74, 'clrf')                                  # je .clrf
    b += b'\x81\x3E\x4F\x6E\x47\x6F'                    # cmp [esi],'OnGo'
    jcc8(0x74, 'clrf')                                  # je .clrf
    b += b'\x81\x3E\x4F\x6E\x51\x75'                    # cmp [esi],'OnQu'
    jcc8(0x74, 'chkq')                                  # je .chkq
    b += b'\x81\x3E\x4F\x6E\x54\x79'                    # cmp [esi],'OnTy'
    jcc8(0x74, 'chkt')                                  # je .chkt
    b += b'\x81\x3E\x4F\x6E\x45\x79'                    # cmp [esi],'OnEy'
    jcc8(0x74, 'setm')                                  # je .setm
    jmp32('after')                                      # 都不是→跳过（距离远，须 rel32）
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
    # .setm：标志清零 + 置视力标记（mark=1）
    label('setm')
    b += b'\x8D\x93' + struct.pack('<i', disp)          # lea edx,[ebx+flag]
    b += b'\xC7\x02\x00\x00\x00\x00'                    # mov dword [edx],0
    b += b'\x8D\x93' + struct.pack('<i', disp + 4)      # lea edx,[ebx+flag+4]（mark）
    b += b'\xC7\x02\x01\x00\x00\x00'                    # mov dword [edx],1
    jmp32('after')                                      # 到此为止（别落进 Qu/Ty 分流把标记清掉）
    # .chkq：OnQuiz* 分流（Leave→置位；Enter→清 GAMELEFT；其余→.gamem）
    label('chkq')
    b += b'\x81\x7E\x04\x69\x7A\x4C\x65'                # cmp [esi+4],'izLe'（OnQuizLeave）
    jcc8(0x74, 'setpend')                               # je .setpend
    b += b'\x81\x7E\x04\x69\x7A\x45\x6E'                # cmp [esi+4],'izEn'（OnQuizEnter）
    jcc8(0x74, 'clrgl')                                 # je .clrgl（清 GAMELEFT）
    jmp8('gamem')                                       # 其余 OnQuiz* → .gamem
    # .chkt：OnTypinggame* 分流
    label('chkt')
    b += b'\x81\x7E\x0C\x4C\x65\x61\x76'                # cmp [esi+12],'Leav'（OnTypinggameLeave）
    jcc8(0x74, 'setpend')                               # je .setpend
    b += b'\x81\x7E\x0C\x45\x6E\x74\x65'                # cmp [esi+12],'Ente'（OnTypinggameEnter）
    jcc8(0x74, 'clrgl')                                 # je .clrgl
    jmp8('gamem')                                       # 其余 OnTypinggame* → .gamem
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
    # 立即关游戏窗体：辅助桩按类名找 Ttypinggameform/Teyesightform 并投递
    # WM_CLOSE（异步）。打字框的 CloseQuery 由 .cave+0x10C0 的桩在 GAMELEFT
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
    # --- 缓存扫描：\![*]\q[ → 复制整段响应到游戏菜单缓存（cave+0x904）---
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
    b += b'\x81\xF9\xFC\x06\x00\x00'                    # cmp ecx,0x6FC
    jcc8(0x76, 'cl')                                    # jbe .cl
    b += b'\xB9\xFC\x06\x00\x00'                        # mov ecx,0x6FC
    label('cl')
    b += b'\x8D\x93' + struct.pack('<i', 0x900 - (CAVE_A_OFF + 0x0B))  # lea edx,[ebx+cache]
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
    assert len(b) == 0x259, hex(len(b))
    return bytes(b)


def _build_typing_closeq_stub(cave_va):
    """formCloseQuery 跳板（.cave+0x10C0，挂在 0x46EE00）。

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
      （打字框的 CloseQuery 需先放行，由 .cave+0x10C0 的桩在 GAMELEFT 时处理；
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
    strs = bytearray(b'\x00' * (0x140 - 0x110))
    strs[0x000:0x010] = b'Ttypinggameform\x00'
    strs[0x010:0x01F] = b'Teyesightform\x00'
    strs[0x020:0x02E] = b'Tcountdownform\x00'
    return bytes(b), bytes(strs)


def _build_dc_status_stub(rva):
    """双击入口桩（挂在 0x4782BD，request() 帧内，EBP 有效）：

       request() 的参数约定（经 0x45F220 + System.Move 0x40283C 反汇编确认，
       且已被旧版补丁在 SSP 实测证实）：
         [ebp+8]  = 请求字符串指针（PChar）
         [ebp+0xC] = 指向请求长度的指针（PDWORD）
       判定优先级：
       1) 请求含 "Status: choosing"（选择肢/菜单等待中）→ 吞掉（204）；
       2) 请求含 "passive"（游戏进行中）：
          - 游戏标记（MARK_OFF）=1（视力游戏）→ 吞掉（视力是单题小游戏，
            双击不做任何事，等它自己结束）；
          - 标记=0（打字/问答）→ 菜单缓存（.cave+0x904，由桩A 维护）非空
            则用 LStrAsg 把缓存的游戏菜单写回响应（SSP 显示该游戏那层
            菜单，便于点「退出」）；缓存为空 → 吞掉。
       3) 输入框标志（FLAG_OFF）为 1 → 吞掉（204）；
       4) 其余复刻原指令后继续（跳 0x4782C5），走 ghost 原逻辑（弹主菜单）。
    """
    b = bytearray()
    va = lambda i: rva + i

    def rel32(target, at):
        return struct.pack('<i', target - va(at + 4))

    disp = FLAG_OFF - (CAVE_B_OFF + 0x40)
    b += b'\x57'                                        # 0x00: push edi
    b += b'\x56'                                        # 0x01: push esi
    b += b'\x8B\x7D\x08'                                # 0x02: mov edi,[ebp+8]（请求指针）
    b += b'\x8B\x4D\x0C'                                # 0x05: mov ecx,[ebp+0xC]（长度指针）
    b += b'\x8B\x09'                                    # 0x08: mov ecx,[ecx]（长度）
    b += b'\x83\xF9\x10'                                # 0x0A: cmp ecx,16
    b += b'\x72\x2C'                                    # 0x0D: jb .reqdone（0x3B）
    b += b'\x8D\x74\x0F\xF0'                            # 0x0F: lea esi,[ecx+edi-16]
    b += b'\x81\x3F\x53\x74\x61\x74'                    # 0x13: cmp [edi],'Stat'
    b += b'\x75\x1B'                                    # 0x19: jne .next（0x36）
    b += b'\x81\x7F\x04\x75\x73\x3A\x20'                # 0x1B: cmp [edi+4],'us: '
    b += b'\x75\x12'                                    # 0x22: jne .next
    b += b'\x81\x7F\x08\x63\x68\x6F\x6F'                # 0x24: cmp [edi+8],'choo'
    b += b'\x75\x09'                                    # 0x2B: jne .next
    b += b'\x81\x7F\x0C\x73\x69\x6E\x67'                # 0x2D: cmp [edi+0xC],'sing'
    b += b'\x74\x69'                                    # 0x34: je .suppress（0x9F）
    b += b'\x47'                                        # 0x36: .next: inc edi
    b += b'\x39\xF7'                                    # 0x37: cmp edi,esi
    b += b'\x76\xD8'                                    # 0x39: jbe .scan（0x13）
    b += b'\xE8\x00\x00\x00\x00'                        # 0x3B: .reqdone: call $+5
    b += b'\x58'                                        # 0x40: pop eax（= va(0x40)）
    b += b'\x05' + struct.pack('<i', disp)              # 0x41: add eax,flag（=cave+0x880）
    # --- passive 扫描（游戏进行中：请求含 "passive"；优先于输入框判定）---
    # 注意：Status 可能直接是 "passive,balloon(...)"（前面无 talking/choosing），
    # 所以不能匹配 ",passive"（带逗号），要匹配裸的 "passive"。
    b += b'\x8B\x7D\x08'                                # 0x46: mov edi,[ebp+8]
    b += b'\x8B\x4D\x0C'                                # 0x49: mov ecx,[ebp+0xC]
    b += b'\x8B\x09'                                    # 0x4C: mov ecx,[ecx]
    b += b'\x83\xF9\x07'                                # 0x4E: cmp ecx,7
    b += b'\x72\x5B'                                    # 0x51: jb .flagchk（0xAE）
    b += b'\x8D\x74\x0F\xF9'                            # 0x53: lea esi,[ecx+edi-7]
    b += b'\x8B\xD1'                                    # 0x57: mov edx,ecx
    b += b'\x83\xEA\x06'                                # 0x59: sub edx,6
    b += b'\x81\x3F\x70\x61\x73\x73'                    # 0x5C: .ps: cmp [edi],'pass'
    b += b'\x75\x0E'                                    # 0x62: jne .pn（0x72）
    b += b'\x66\x81\x7F\x04\x69\x76'                    # 0x64: cmp word [edi+4],'iv'
    b += b'\x75\x06'                                    # 0x6A: jne .pn（0x72）
    b += b'\x80\x7F\x06\x65'                            # 0x6C: cmp byte [edi+6],'e'
    b += b'\x74\x06'                                    # 0x70: je .passive（0x78）
    b += b'\x47'                                        # 0x72: .pn: inc edi
    b += b'\x4A'                                        # 0x73: dec edx
    b += b'\x75\xE6'                                    # 0x74: jnz .ps（0x5C）
    b += b'\xEB\x36'                                    # 0x76: jmp .flagchk（0xAE）
    # --- passive：按游戏标记分流（1=视力→吞掉；0=重放缓存菜单）---
    b += b'\x83\xB8\x04\x00\x00\x00\x00'                # 0x78: .passive: cmp dword [eax+4],0（mark）
    b += b'\x75\x1E'                                    # 0x7F: jne .suppress（0x9F）（视力：双击无效）
    # --- 重放缓存的游戏菜单（长度=0 时退化为吞掉）---
    b += b'\x83\xB8\x40\x00\x00\x00\x00'                # 0x81: .replay: cmp dword [eax+0x40],0（缓存长度@0x900）
    b += b'\x74\x15'                                    # 0x88: je .suppress（0x9F）
    b += b'\x8D\x90\x44\x00\x00\x00'                    # 0x8A: lea edx,[eax+0x44]（缓存串=cave+0x904）
    b += b'\x8D\x45\xE4'                                # 0x90: lea eax,[ebp-0x1c]
    b += b'\xE8' + rel32(0x403C58, 0x94)                # 0x93: call LStrAsg（@0x94）
    b += b'\x5E\x5F'                                    # 0x98: pop esi/edi
    b += b'\xE9' + rel32(DC_DONE_VA, 0x9B)              # 0x9A: jmp 0x478848
    # --- 吞掉双击（204）---
    b += b'\x5E\x5F'                                    # 0x9F: .suppress: pop esi/edi
    b += b'\x8D\x45\xE4'                                # 0xA1: lea eax,[ebp-0x1c]
    b += b'\xE8' + rel32(0x403BC0, 0xA5)                # 0xA4: call 0x403BC0（清响应）
    b += b'\xE9' + rel32(DC_DONE_VA, 0xAA)              # 0xA9: jmp 0x478848
    # --- 输入框标志检查（清响应 → 204）---
    b += b'\x83\x38\x00'                                # 0xAE: .flagchk: cmp dword [eax],0
    b += b'\x75\xEC'                                    # 0xB1: jne .suppress（0x9F）
    # --- 非 passive 非输入框：清响应走原逻辑（主菜单）---
    b += b'\x5E\x5F'                                    # 0xB3: .normal: pop esi/edi
    b += b'\x8D\x45\xE4'                                # 0xB5: lea eax,[ebp-0x1c]
    b += b'\xE8' + rel32(0x403BC0, 0xB9)                # 0xB8: call 0x403BC0（清响应）
    b += b'\xE9' + rel32(DC_CONT_VA, 0xBE)              # 0xBD: jmp 0x4782C5
    assert len(b) == 0xC2, hex(len(b))
    return bytes(b)


def patch_extra_link(data: bytearray) -> bytearray:
    """海原雄山 链接化 + OnAnchorSelect 打开 http 链接 + 双击判定。"""
    blob = bytearray(0x2000)
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

    # 桩 A：搜索框标志维护（挂在事件响应汇总点）
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

    print(f'补丁已应用: 海原雄山 + OnAnchorSelect(http) + 双击判定(choosing/输入框/游戏菜单) @ RVA 0x{rva:X}')
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

data = patch_extra_link(data)

data = patch_aitxt(data)

os.makedirs(os.path.dirname(DLL_OUT), exist_ok=True)
with open(DLL_OUT, 'wb') as f:
    f.write(data)

print(f'写入完成 → {DLL_OUT}')
if len(sys.argv) >= 2:
    dst = sys.argv[1]
    shutil.copy2(DLL_OUT, dst)
    print(f'已复制 → {dst}')

print(f'  写入: {ok}  截断: {trunc}  跳过: {skip}')
