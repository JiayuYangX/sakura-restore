# さくら（御影樱）人格汉化

本仓库为伪春菜原基础软件 MATERIA 的内置人格 さくら（樱）的汉化工程。文本内容翻译由大模型辅助完成。

![示意图](pic.png)

人格文件请见 [ghost](https://github.com/JiayuYangX/sakura-restore/tree/ghost) 分支；
外壳图片处理见 [shell](https://github.com/JiayuYangX/sakura-restore/tree/shell) 分支。

## 安装方法

从 [Release](https://github.com/JiayuYangX/sakura-restore/releases) 中下载最新版本提供的 NAR 文件，拖入 SSP 进行安装。支持在线更新。

## 必要环境

- [SSP](https://ssp.shillest.net/)
- Windows（未开启「Beta 版: 使用 Unicode UTF-8 提供全球语言支持」选项）

## 汉化项目描述

文件提取自 [伺か period 583](http://usada.sakura.vg/contents/files/materia583.exe)。
原配布链接：<http://usada.sakura.vg/>

### 汉化

该人格的所有文本都被硬编码进 SHIORI（`first.dll`）中，其中绝大部分以明文形式存储。对话和菜单、提示文本位于 CODE 段，内置图形界面文本位于 rsrc 段。原来使用的 SJIS 编码文本在中文系统环境中运行会显示乱码，故直接将其替换为 GBK 译文（字节长度不超过原文）。

然而仍有如环境变量等少数文本以自定义压缩格式存储在 rsrc 段的 AITXT 中。由于无法获取其压缩/解压算法，本项目采用翻译器接口（`makoto.dll`，遵循 MAKOTO/2.0 协议）在 SHIORI 返回该部分文本后对其 SJIS 字节进行匹配替换。

### 图片修复

外壳和对话框现均改为使用图片自身透明通道。外壳图片使用 [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) 的 realesrgan-x4plus-anime 模型进行四倍放大；再使用 [ImageMagick](https://imagemagick.org) 透明填充红色背景、边缘 2 像素勾线后再缩放回原大小，可形成平滑自然的半透明边缘。

### 兼容性修复

人格需依赖原来的 `materia.exe` 才能正常运行，故将其置于 `ghost/master` 目录之下。硬编码字符串由原本的 Shift-JIS（932 代码页）字符集变为 GBK（936 代码页），可能仅在中文系统环境下（未启用 UTF-8 Beta 设置）正常显示。

外壳和对话框在现代高 DPI 显示器上建议使用与系统相同的缩放比例；时钟和提示框未经过 SSP 缩放，可能显得过小。

人格编写的时代旧 SHIORI 规范未完全实现 NOTIFY 方法，除部分特殊事件外仍基本使用 GET，`first.dll` 接到 NOTIFY 请求后会直接响应 `400 Bad Request`；现代 SHIORI 规范及 SSP 基础软件在 `caltalk=0` 时 `OnSecondChange` 事件严格使用 NOTIFY 进行通信，`first.dll` 拒绝响应导致状态机卡住，无法自动退出小睡（`mode2`）和入浴（`mode3`）状态，能量值累加也会停摆。

故通过两步 DLL 修补解决相关问题：（1）禁用 `0x719E` 处 NOTIFY 的跳转使请求正常处理；（2）`0x79E08` 处 `\![enter,inductionmode]` 字段长度设为 0 取消进入诱导模式。


## 翻译流程

1. 运行 `extract2csv.py` 和 `extract_aitxt.py` 提取日文原文
2. 运行 `replace_chars.py` 预处理不可编码字符 
3. 人工翻译 CSV 中的文本
4. 运行 `patch_dll.py` 打静态补丁
5. 运行 `build.bat` 生成带替换表的 `makoto.dll`
6. 部署后测试

## 环境及工具

- MSYS2 + `mingw-w64-i686-gcc`（32 位 MinGW）
- Python 3.x
- Real-ESRGAN-nccn-vulkan
- ImageMagick