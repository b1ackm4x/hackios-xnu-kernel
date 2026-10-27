# HackiOS · OnePlus 9

[普通用户复现指南](docs/LEMONADE-REPRODUCE.md) · [CI 组件包与维护者说明](docs/LEMONADE-CI.md) · [GitHub Actions](https://github.com/b1ackm4x/hackios-xnu-kernel/actions/workflows/lemonade-ci.yml)

基于 [oosexgang/HackiOS](https://github.com/oosexgang/hackios-xnu-kernel) 的一加 9（LE2110 / `lemonade`）适配，运行 XNU 与 iOS 16.5 用户空间。

## 当前进度

当前 `main` 已集成默认 **120 Hz**、异步 Metal、双缓冲多层 BGRA 合成、安全退出、Safari 网页刷新策略、真实电量与充电状态、Sileo 安装修复，以及原生蓝牙发现。
最近完成整套运行包验收的本机版本是 **`2026.10.27-base.2`**，对应用户态和内核源码一并维护在两个仓库的 `main`。

| 模块 | 已验证范围 |
| --- | --- |
| GPU | Adreno 660 GLES / Metal；Metal 103 项、MPS 2 项、Core Animation 4 项通过 |
| 显示与触控 | SpringBoard 桌面、默认 120 Hz DPU 直接显示、实体触控；UIKit 最高 120 fps |
| Wi-Fi | QCA6490 关联、DHCP、DNS、HTTP 和断线重连；联网检查 61 项通过 |
| 电池 | 与 Wi-Fi / 图形联合验收通过，10 次内核、IORegistry、IOPS 对照一致；接电满电状态栏显示绿色 100% |
| 蓝牙 | 原生“设置 → 蓝牙”发现真实附近设备；与 Wi-Fi / 图形 / 电池共存验收通过，14 次原生状态采样一致 |
| Sileo | 开机自动注册；已修复安装时退出和 NewTerm 登录权限，图形化安装与终端命令另有实机验证 |
| 干净运行包 | 已移除自检、测试程序、自动手势、调试录制和定时退出；默认运行与正常退出检查通过 |

图形、触控、Wi-Fi、电池和蓝牙已完成同机联合验收，正式包无测试负载的 90 秒运行与主动退出也已通过；长期稳定性仍需验证。系统只在 RAM 中运行，不刷写 Android 分区，重启后设置与会话不保留。

原生蓝牙设置已实测显示附近 Mac；配对、音频及外设数据传输仍未验收。再次启动会先返回 Android 后冷启动，原 Android 蓝牙设置会恢复。详见[蓝牙实现与实测](docs/LEMONADE-BLUETOOTH.md)。

120 Hz 是默认启动模式。原生设置曾受 Metal 同步等待限制，同条件实测约 84–86 fps；
base.3 的同一内核实测慢滑 119.19 fps、快滑 119.86 fps，快速滚动的 P95 帧间隔由 16.6 ms 降至 8.3 ms。这里区分实际应用
表现与面板模式，不将此前 Refresh 测试应用的 120 fps 结果当作所有应用的保证。
base.3 的两层、四层与单层切换保持 120 Hz，3 次定时退出和 3 次主动退出均未观察到 backboardd SIGSEGV。
后续双缓冲修复将同一原生 Metal 场景从约 60.5 fps 提升到 **116–117 fps**；应用和 GPU 频率未改，详见 [原生多层显示实测](docs/LEMONADE-NATIVE-BENCH.md)。
旧运行包均保留。详见 [默认 120 Hz base 与修复验收](docs/LEMONADE-BASE-120HZ.md)。

Safari 原来将网页动画限制在 60 fps。base.4 关闭该默认策略并保留用户修改，UFO Test 无观测探针时实测显示完成 **105.27 fps**；**该网页尚未稳定达到 120 fps**。

2026-10-27 已[选择性同步上游 HakiOS](docs/UPSTREAM-SYNC-20261027.md) 的 modem/IPA、QMI/NAS、SoC 参数工具和构建改进。
一加 9 的新蜂窝路径保持禁用；本次同步后的二进制仅完成主机验证，最近实机验收包仍是上述 base.2。

## 使用

普通用户请从 [复现指南](docs/LEMONADE-REPRODUCE.md) 开始。我们负责预编译组件与发布前实机验收；用户只需准备受支持的手机、允许采集本机必要文件、下载指定 Apple 系统，再由本地工具组包和启动。用户不需要编译或运行 GPU、DMA、联网验收程序。

**目前尚未交付这套完整的普通用户准备工具和已验收公开运行版本。** [公开下载](https://github.com/b1ackm4x/hackios-xnu-kernel/releases) 现为 CI 通用组件实验版：`main` 更新后自动测试、编译和发布，包含内核、运行库、通用加载器和配套源码。它供维护者集成，不能直接启动手机；缺失步骤与当前工具限制在复现指南中逐项列明。[CI 说明](docs/LEMONADE-CI.md) 保留组件下载、校验与开发集成方法。

本机已验收的完整运行包：`out/releases/lemonade-2026.10.27-base.2.zip`，与上述 CI 组件包分别维护。

这个本机运行包需要 Mac 安装 Python 3.11+、adb 和 fastboot。USB 连接其绑定的一加 9，解压后双击 `Start.command`；返回 Android 使用 `Return-to-Android.command`。

## 待完善

- 完整 Metal 兼容、120 Hz 长时间稳定性与动态刷新率切换、睡眠唤醒、温控与电源管理，以及其余外设。
- USB 拔插后的电池状态更新仍受调试通道重连问题阻碍，采样中断时 powerd 保留最后值；未满电充电图标、低电量和充电控制仍待后续验证或实现，见 [电池驱动进度与实机图像](docs/LEMONADE-BATTERY.md)。
- 原生设置首次输入 Wi-Fi 密码的完整流程尚未单独验收；天气小组件仍显示占位内容。
- 普通用户准备工具：自动采集本机必要文件、下载指定 Apple 系统、用预编译组件组包，并认可维护者已验收的发布版本。
- 双缓冲多层合成覆盖非平面 BGRA32；其他格式仍可能使用兼容显示路径。已有原生场景实测，当前组合的完整运行包验收已通过。

## 代码布局

| 路径 | 内容 |
| --- | --- |
| `xnu/files/` | 内核、GPU、显示、触控、Wi-Fi、蓝牙和电池驱动 |
| `xnuloader/`、`uefi/` | RAM 加载器与设备启动支持 |
| `userland/` | init、图形运行库和系统服务，属于独立用户空间仓库 |
| `common/devices/lemonade*` | 机型配置与配套源码版本 |
| `tools/lemonade-*` | 构建、打包、启动与开发验收工具 |
| `docs/` | 移植记录、构建步骤和验收细节 |

完整 base 的蓝牙、Sileo 与既有 120 Hz / Wi-Fi / 电池代码已合入两个仓库的 `main` 分支。普通 `git` 管理本仓库，`common/ugit` 管理配套的 [用户空间仓库](https://github.com/b1ackm4x/hackios-xnu-userland)；版本对应关系见 [lemonade.sources.json](common/devices/lemonade.sources.json)。

详细说明：[Base 与验收](docs/LEMONADE-BASE-120HZ.md) · [构建与移植](docs/LEMONADE.md) · [显示](docs/LEMONADE-DISPLAY.md) · [Wi-Fi](docs/WIFI-LEMONADE.md) · [仓库维护](docs/REPOSITORY.md)。

保留原作者 OOSEX GANG 的归属、历史与许可；XNU 许可见 [LICENSE](LICENSE)，第三方声明见 [NOTICES](prebuilt/NOTICES.md)。Apple 系统文件、原机固件、凭据、构建产物和私有日志不提交。Redwood/QEMU 的使用方式见[上游文档](https://github.com/oosexgang/hackios-xnu-kernel#readme)。
