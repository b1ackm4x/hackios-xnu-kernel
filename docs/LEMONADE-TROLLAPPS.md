# TrollApps 安装记录（2026-10-28）

在本机 OnePlus 9（LE2110 / lemonade / SM8350）的 iOS 16.5 RAM 环境中，
已安装 TrollStore 2.1.1、其 ldid 签名工具及 TrollApps 2.3。
本轮基于已封存的 `lemonade-2026.10.27-base.2`，沿用该包的 System、kernel 和 loader，
在独立 root 镜像中加入应用安装服务与已通过 K380 输入测试的键盘适配代码。
没有改写原封存包，也没有重新进行蓝牙验收。

## 软件来源与安装

- [TrollApps 2.3 官方发布](https://github.com/TheResonanceTeam/TrollApps/releases/tag/2.3)
- [TrollStore 2.1.1 官方发布](https://github.com/opa334/TrollStore/releases/tag/2.1.1)
- [ldid 官方发布](https://github.com/opa334/ldid/releases/tag/v2.1.5-procursus7-new)

下载文件的 SHA-256 及来源记录在 `out/lemonade-trollapps-1/manifest.json`。
设备内使用官方 `trollstorehelper` 依次安装 TrollStore、ldid，启用 URL scheme，然后安装 IPA。
TrollStore 的系统 placeholder 安装因 `com.apple.mobile.installd` 不可用而失败，
官方 helper 随后通过自己的 MCMAppContainer 安装路径完成安装，最终返回 0。
没有替换系统应用或安装 persistence helper。

## 实机证据

本轮记录在 `local/lemonade/trollapps-20261028/223909/`：

- 启动前确认目标 USB 身份、Android 已启动、原槽位 `_b`，五个基线分区哈希均匹配。
- 四个安装步骤均返回 0；通过 LaunchServices 读回 TrollStore 2.1.1 和 TrollApps 2.3。
- 安装后的 ldid 与下载输入逐字节一致；TrollStore 的 `apple-magnifier` URL scheme 已启用。
- 22:42:10 系统接受 TrollApps 打开请求，launchd 实际启动 `TrollApps[106]`。
- 观察结束时为 `result: running 0 0`，未观察到该应用进程退出或内核 panic。
- Wi-Fi 关联及 DHCP 成功。这不等于已经验证仓库加载、IPA 下载或从 TrollApps 转交安装其他应用。

`evidence.json` 区分安装、进程启动、实际界面确认。用户随后在手机实际屏幕上确认“已显示 TrollApps”，
因此应用界面打开已通过实机确认。此次 USB framebuffer 截图读到的是旧启动画面，不能用作界面证据；
源 IOSurface 记录需停止当前运行才能读取，故保留当前手机会话供使用。用户的界面确认不扩展为仓库加载或下载验收。

## 再次启动与边界

本轮为 RAM 运行，`ufs_write=0`、`var_store=0`，没有刷写 Android 分区。
重启后本轮安装及应用数据消失。为保持手机可用，本轮未主动重启回 Android；
上述分区哈希是启动前的回程基线检查，不是本轮结束后的再次回程验收。

本机安装流程保存在 `local/lemonade/trollapps-20261028/run.py`。
需要重新安装时，从目标手机正常进入 Android 且 USB 调试在线的状态运行它；
它会核验已封存文件与目标身份，再冷启动独立应用镜像。Wi-Fi 输入只加入临时 RAM 启动文件，
上传后删除；独立 root 镜像不包含 Wi-Fi 凭据。手机当前会话保持运行，没有设置自动退出计时器。

本次结论只覆盖这两个应用的安装和已记录的启动步骤，不代表任意 IPA 或 TrollApps 内所有软件可用。

## 应用源配置（2026-10-28）

用户反馈列表为空后，独立会话 `out/lemonade-trollapps-sources-1` 加入 7 个源：
Resonance Team、UTM、Ignited、Flyinghead、Provenance、AltStore、OatmealDome。
地址来自 TrollApps 官方可信源页面及开发团队自己的仓库，记录在本机
`local/lemonade/trollapps-20261028/sources/SOURCES.md`。

主机请求全部返回 HTTP 200，随后使用 TrollApps 2.3 发布版的原始 Swift 模型逐份解码通过。
配置助手只在 TrollApps 进程内运行，在其初始化 RepositoryManager 之前，按 AppStorage 所需的
JSON 字符串格式写入 `repos`；已有保存的列表会合并，旧值会另存，重复启动按版本标记避免重复添加。
没有改变应用的证书校验或下载实现，也没有自动安装源内其他 IPA。

手机实机读回确认 `selected=7 total=7`。手机内原生 NSURLSession 对这 7 个 HTTPS 地址全部成功，
每个均返回 HTTP 200 和非空 `apps` 列表，总计 18 个条目；其中包含不同版本或重复条目，
不表示 18 个不同应用。首轮源配置证据在 `local/lemonade/trollapps-20261028/231716/`。
2026-10-29，用户针对搜索 UTM 或 Delta 的检查确认“能搜到了”，应用搜索界面已通过实机确认；
尚未验证下载或运行这些应用。

重新建立同一带源会话时，从已启动并连接 USB 的目标 Android 系统执行：

```sh
python3 local/lemonade/trollapps-20261028/run.py \
  --candidate out/lemonade-trollapps-sources-1 \
  --owner codex-hackios-trollapps-sources
```

它会重新安装 TrollStore 和 TrollApps 并带入保存的源配置；之前 RAM 会话里的其他数据仍不会保留。

## Delta 安装提示与启动闪退（2026-10-29）

用户在 TrollApps 下载 Delta 后看见 `FAILED_TO_INSTALL`，随后确认桌面已出现图标，但打开闪退。
因此当前证据支持应用已完成注册、启动失败；尚未取得实际安装返回码或启动崩溃栈。
TrollApps 2.3 将 helper 的任何非零返回值都标成失败，且队列仅显示错误标题，不显示包含数字的正文。
TrollStore 在应用注册后仍可能返回开发者模式或附属二进制警告，不能仅凭队列标题判定安装未完成。

当前 RAM 会话的精确目标 USB 可枚举，但多次有界诊断请求超时，尚不能读取本次闪退日志。
已准备独立诊断镜像，保留现有源与键盘组件，额外加入一次性安装/启动诊断程序，
以及只对 Delta 生效的崩溃日志库。两个 arm64 程序已交叉编译、签名并完成镜像写入与读回校验；
首个镜像的桌面启动问题已定位并修正，当前使用 `out/lemonade-delta-diagnostic-3`；
原始 Delta 启动崩溃已通过后述实机对比排除，并从保存的 IOSurface 录制确认进入应用界面。

诊断程序首先请求源内 Delta 1.7.1；若无法取得符合 HTTP 状态、ZIP 头与长度要求的 IPA，
才改用作者 GitHub 的 Delta 1.6。该备用包已在主机完成 ZIP 完整性、包标识和 SHA-256 校验，
最低版本为 iOS 14，架构为 arm64。主机请求 AltStore CDN 返回 403，但用户手机已出现图标，
所以主机的 403 不能认定为手机这次失败的原因。再次诊断时，从 Android 启动后执行：

```sh
python3 local/lemonade/trollapps-20261028/run.py \
  --candidate out/lemonade-delta-diagnostic-3 \
  --owner codex-hackios-delta
```

诊断会保留 helper 原始返回码，并分别记录应用注册、启动请求和实际崩溃。
启动请求被接受不等于界面成功打开；当前没有 Delta 可运行的验收结论。

### 首轮实机结果

用户重启回 Android 后，精确 LE2110 / SM8350 及 boot-complete 再次核对通过，
原 Android 分区哈希全部匹配。诊断会话证据位于
`local/lemonade/trollapps-20261028/090338/`。

手机原生 NSURLSession 从 AltStore CDN 成功取得 Delta 1.7.1：HTTP 200、
196,023,694 字节、ZIP 头正确，SHA-256 为
`8d1b060509d99d9294910f18683dd51f14eb4fd30b7d928b2b6cd57c1971650d`。
没有启用 1.6 备用包。与 TrollApps 相同的 mobile → persona 99 → root helper 调用成功，
安装器退出码为 **0**，LaunchServices 读回确认包标识与版本均正确。

该轮自动打开 TrollApps 和 Delta 的请求均未被系统接受；在随后 90 秒观察中，
没有 Delta 进程启动记录、诊断库初始化或崩溃栈。因此这一轮证明下载与安装成功，
尚未复现用户之前的启动闪退。

用户随后确认没有进入正常桌面。检查发现本轮 `kern.exec_inject` 返回 EINVAL（22）：
诊断库加入后，配置变为 515 字节文本加一个 NUL，超过内核 512 字节缓冲区的限制，
整组兼容组件均未加载。因此不能把本轮无法打开应用归因于 Delta。
该候选镜像已标记为拒绝使用，原封存基线没有改动。

第二个诊断镜像缩短库路径，传入长度为 507 字节（含 NUL）。打包器现在模拟 init 的
换行转换与末尾裁剪，并校验内核要求的 `newlen < 512`；越界配置会在生成镜像前被拒绝。
加入边界长度与打包拒绝的回归测试，全部 27 项主机测试通过。
第二轮 `092429` 的实机日志确认 `init: library insertion` 成功，TrollApps 正常启动，
7 个源再次全部返回 HTTP 200。Delta 1.7.1 下载哈希与前轮一致，安装仍返回 0。

### 实际启动崩溃

第二轮 09:28:34，launchd 启动 Delta[113]，诊断库成功加载，随后捕获 SIGTRAP：
`DeltaCore.GameView.init(frame:)` 位于栈顶，调用来自 `GameViewController.viewDidLoad`。
完整脱敏栈保存在 `092429/delta-crash-snapshot.log`。
这次明确复现了应用自身的启动退出，与首轮整组兼容组件未加载的问题分开记录。

[DeltaCore 的 GameView 源码](https://github.com/rileytestut/DeltaCore/blob/869042a5699b19df3cf9f3369a950737ff818149/DeltaCore/UI/Game/GameView.swift)
在构造时强制解包 `EAGLContext(api: .openGLES2)`。这个公开源码位置与实机崩溃函数一致，
但未断言该源码提交就是 CDN 二进制的构建提交。现有基线包含 Redwood OpenGL ES 实现，
正常仅由 backboardd 加载，Delta 的崩溃日志中没有该库加载记录。

据此准备第三个独立候选 `out/lemonade-delta-diagnostic-3`：只在 Delta 内探测原生 ES2
上下文；若为空，则加载现有 `libredwood-gl.dylib` 并再次探测。保持崩溃捕获，
不改写 Delta IPA、系统缓存或封存内核。

### OpenGL 加载修正实机结果

第三轮 `093102` 再次核验原 Android 分区、冷启动并安装同一 Delta 1.7.1。
安装返回 0，09:35:13 launchd 启动 Delta[113]。同一进程内探测结果如下：

- `native_es2_context=0`：原生 OpenGL ES 2 上下文确实为空。
- `redwood_library_loaded=1`：加载已包含在基线中的 Redwood OpenGL ES 库成功。
- `redwood_es2_context=1 api=2`：同一 API 的上下文随后创建成功，Mesa 报告 FD660。
- Delta 自己随后再次创建了 ES2 上下文，越过第二轮崩溃位置；持续观察 **95.9 秒**，
  没有 Delta 信号/异常捕获、进程退出或内核 panic。

这确认了导致原始启动崩溃的 OpenGL 上下文问题及修正效果。
当时仅凭运行日志，`delta_ui_verified` 和 `delta_game_execution_verified` 均保持为 false。

相同源码已保存到 `userland/src/lemonade_delta_gl.m`，
`devices/oneplus-9/tools/lemonade-build-delta-gl.sh` 可在既有 Linux 构建环境中生成签名 arm64 库。
重建启用 `-Wall -Wextra -Werror` 并通过；源码哈希与实机候选完全一致。
重建二进制哈希与本轮候选不同，因此 `out/lemonade-delta-gl/build.json` 的实机标志仍为 false；
实机结论绑定 `out/lemonade-delta-diagnostic-3/manifest.json` 中的实际镜像和诊断库哈希。

### 截图与返回 Android（2026-10-29 15:25）

用户请求截图后，USB `oem screenshot` 仅取得旧的 loader framebuffer，不能作为 Delta 画面证据。
用户随后要求重启设备；先正常退出 RAM 会话，取得 `result: returned 1441823 0`，
再成功导出保存的 IOSurface 录制，共 147 帧、187×406 像素。
最新帧对应内核运行时间 402.762 秒，保存在
`local/lemonade/trollapps-20261028/screenshots/20261029-152508-surface.png`。

检查图像明确看到 Delta 的 **What's New / Delta 1.7** 页面及应用导航，
因此 `delta_ui_verified=true`，验证方式为查看本轮保存的 IOSurface 录制。
该图是此前录下的应用画面，不是 15:25 的实时截图，也不是对物理面板的光学拍摄；
`delta_game_execution_verified=false`，游戏导入、运行、音频和存档仍未验证。

随后已重启精确 LE2110 / SM8350 目标，ADB 确认 Android `sys.boot_completed=1`，
并恢复进入 RAM 会话前的 Android 蓝牙设置。本轮 HackiOS RAM 会话已结束。


## B站安装（2026-10-30）

已在同一台 LE2110 的 RAM 会话中安装哔哩哔哩 8.89.0（build 88900100，
`tv.danmaku.bilianime`，arm64，最低 iOS 13）。包来自
[TouchFriend/BiliBiliMApp 3.1.0 发布](https://github.com/TouchFriend/BiliBiliMApp/releases/tag/3.1.0)
的 `bili-universal_8.89.0.app.zip`，不属于 B站官方直接分发的 IPA。
该资产的 SHA-256 与 GitHub 发布记录一致。

单独提供的应用包仍包含 `BilibiliVideoTools.dylib` 加载项，且其依赖未完整包含。
本轮仅移除此附加插件及对应加载命令，再按 IPA 的 Payload 结构打包；
没有使用该项目的广告修改版 IPA。主程序与剩余两个库均已脱壳。
符号裁剪实验未采用，安装包不包含实验输出。

手机本地签名对这个约 819 MiB 的应用产生很高的临时空间和内存需求。
最终方案在 Mac 上构建官方 TrollStore `fastPathSign`，预先完成三个 Mach-O 的签名，
并独立校验真实 SHA-256 CodeDirectory 的每一个代码页。
应用标记 `TSPreAppliedExploitType=2` 后，实机官方安装器确实进入已预签名的安装分支，
没有在手机上再次执行大包签名。主程序使用应用容器权限，`get-task-allow=false`。

最终 IPA：`local/lemonade/bilibili-20261030/downloads/Bilibili-8.89.0-presigned.ipa`，
276385963 字节，SHA-256 `2f3fb3d23882baf0ac53e905608ce2078b6e92a719d5f5ebba651697ab1e9773`。
完整来源、处理步骤、工具提交和代码页校验结果在同目录上层的 `package-presigned.json`。

安装会话为独立的 `out/lemonade-bilibili-4`。原封存包未更改，System 镜像沿用已验证版本。
候选 kernel 增加了显式 RAMFS 挂载参数：普通挂载维持原有四分之一内存上限，
本轮单独的安装临时区允许申请最多二分之一内存。实机验证临时区为 3072 MiB，
普通数据盘仍为 1536 MiB；超限和错误版本参数均返回 EINVAL。
该 kernel 的 SHA-256 是 `77aa12d453ffcacd8feee126bdb8d9da080df1ea37b42ec2d5abb8112be516f4`。
编译完成且无编译错误，已有的链接后导出列表检查告警按原构建流程单独识别。

实机记录显示：下载 HTTP 200、完整长度及 SHA-256 匹配；安装器返回 0；
LaunchServices 读回 8.89.0；系统接受打开请求并实际启动 `bili-universal`。
临时安装文件随后清理，临时区恢复约 3071 MiB 可用，数据盘剩余约 220 MiB。
界面显示、登录、视频播放及声音分别验收，不由安装成功推导。

本轮恢复 TrollApps、7 个软件源和 Delta 1.7.1，Delta 保留图标但不自动打开。
启动前核对目标 USB、Android 完成开机及五个基线分区哈希。全程 RAM 启动，
`ufs_write=0`、`var_store=0`；未执行 Android 分区刷写。重启仍会清除 RAM 会话的应用与数据。
重新建立安装会话需要原本机 LAN 下载服务和对应候选镜像；当前地址属于本次环境，
以后重用前须重新检查本机地址和确切目标手机，不能直接视为跨网络通用启动包。


## Delta 游戏导入与启动测试（2026-10-30）

按用户要求，从作者 [Vulkanon 官网](https://vulkanon.pqrs.org/) 下载其免费开源 GBA 游戏 2.1。
ROM 为 2,097,152 字节，SHA-256 为
`28ee58518de9bb0a1c6a3b8195e15c8626d317cc75548377cfa581403145b6c5`。
来源、下载原件与独立测试程序保存在 `local/lemonade/delta-game-20261030/`。

本轮已确认 **游戏导入成功，尚未确认游戏可运行**：

- 使用 Delta 1.7.1 原有文件导入入口；手机内游戏库文件的 SHA-256 与下载 ROM 一致。
- 第一轮启动游戏在 `GameView.eaglContext.didSet` 的共享 EAGL 上下文创建处 SIGTRAP。
  现有 GLES 适配不支持该共享初始化路径。随后使用 Delta 自带的 `metal` 偏好设置，越过此崩溃位置；没有实现通用 OpenGL 上下文共享。
- 第二轮的真实 IOSurface 录制显示 GBA 游戏库及 Vulkanon 条目，未进入游戏。
  记录为 `030540/source-latest.png`；该轮 UIKit `drawViewHierarchy` 返回黑图，不能据此判定物理屏幕黑屏。
- 后续一次恢复 B站时安装回报进程遭到内存回收，真正的 TrollStore helper 返回 0，
  但等待回报文件的游戏助手无法继续。最终改为检查 LaunchServices 中的实际注册状态，
  并提高两个短期助手的进程类型优先级。
- 最终 `out/lemonade-delta-game-5` / `032928` 会话恢复了 B站 8.89.0 和 Delta 1.7.1，
  ROM 导入校验通过，非动画关闭游戏库完成，系统接受了游戏启动 URL。
  之后等待数分钟仍无首次模拟帧通知，主队列中安排的截图回调也未执行，**不能认为游戏可玩**。
  日志出现缺失 AudioSession/AURemoteIO 服务及 `ci_filters_stitchable` Metal 库加载失败；
  尚未用线程栈确定具体卡住位置，不能直接把其中任一日志认定为唯一原因。

实机观察覆盖 4 次 RAM 启动；中间 `lemonade-delta-game-3` 因主机编译失败而被标为无效，从未启动。
最终证据为 `local/lemonade/delta-game-20261030/032928/evidence.json`。
诊断字段 `executed_frames` 实际只数状态切换后的帧通知，不能作为总帧数或 FPS 使用。
原始模拟器截图接口尚未执行到，未产出可宣称成功的游戏画面。

每轮启动前核对精确 LE2110 / SM8350 和 Android 分区基线；没有刷写 Android 分区。
结束时 loader 状态为 `running 0 0`，手机 RAM 系统仍运行，游戏及 B站安装保留，
Delta 在启动游戏时卡住。临时传输服务已停止，设备租约已释放。RAM 应用和游戏数据会随重启消失。


### 游戏启动进一步定位（2026-10-30）

`delta-game-7` 改为选择原生游戏列表里的 Vulkanon 单元格，进入 GBA 控制器界面，
主线程保持响应；但模拟器状态为 stopped，没有首帧通知或游戏输出。此前直接关闭
游戏库再调用 URL 的测试曾引发主线程与模拟队列互等，不能把该测试卡顿当作日常入口的必然行为。

`delta-game-8` 在页面可见且无模态窗口时调用原有 `GameViewController.startEmulation()` 重试，
记录到 `AudioManager.start → resetAudioEngine` 的 SIGTRAP；同段日志出现 AudioSession 服务
不可用、读取输出采样率失败。此轮证据在本机 `local/lemonade/delta-game-20261030/041050/`。
这定位了当前重试的音频初始化崩溃，尚不能认定补齐音频后其余渲染路径必定正常。
没有用伪造采样率或静音替身声称硬件音频可用，也没有改写 Delta 的模拟器主循环。

日用包只保留应用、Metal 首次偏好和作者免费 ROM 的原生导入；不带线程采样、
自动选游戏、启动重试、崩溃注入、截图回传或自动打开应用。当前结论是安装与导入已验证，
游戏首帧、可玩性和声音未通过。
