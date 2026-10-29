# OnePlus 9 日常运行 base

本文面向维护者，记录本机 base 的组包与发布前验收。普通用户请看 [复现指南](LEMONADE-REPRODUCE.md)，无需编译或运行本文的 DMA、图形、联网验收程序。现有包绑定维护者的测试手机；新用户准备工具和公开已验收运行版本尚未交付。

本文保留 `2026.10.24-base.1` 的 60 Hz 基线记录。当前日常版本为
[`2026.10.30-base.1`，默认 120 Hz](LEMONADE-BASE-20261030.md)，旧包与标签保留用于回退。

`2026.10.24-base.1` 将现阶段的 GPU、Metal、桌面显示、实体触控与 Wi-Fi
放入同一个干净的运行镜像。它是此适配项目的功能基线，后续版本继续在此基础上完善。

## 当前交付状态

运行包位于本机 `out/releases/lemonade-2026.10.24-base.1/`。
包内 APFS 已逐文件回读核对内容、权限、属主和符号链接；主机验证通过。
启动器同时要求当前内核的 DMA guard，以及绑定当前内核和 root 镜像哈希的联合验收通过。

2026-10-24 的联合日志 `1792809857` 已通过 28 项显示与 61 项联网检查。
Metal 功能 101/101、MPS 2/2、Core Animation 4/4；DPU 直接完成 915 帧，
另有 3 帧 CPU 回退，918 个提交和释放记录完整，没有顺序错误。
同次运行完成 Wi-Fi 关联、DHCP、DNS、三次 HTTP、断开重连及另三次 HTTP，
并确认 DMA 停止和显示寄存器恢复。三个桌面服务注册后，该轮未再出现对
geod、navigationService、sleepserver 的查找失败。

未经扩展的正式镜像随后从 Android 经包内 `launch.py start` 启动，默认运行 90 秒，
完成 689 帧直接显示；11 次状态观察均在运行，直到主机明确请求停止。
16 项干净运行检查全部通过：没有开机自检、测试任务或定时退出，运行镜像哈希未变，
GPU/DPU 与 Wi-Fi 正常恢复并返回加载器。本轮未见 backboardd 段错误。
这属于有界功能验收，尚不能代替长期日常稳定性测试。

保留的 73 帧 IOSurface 源图显示桌面、应用图标及日历小组件；天气卡片仍为占位状态。
这是实际提交给显示的源图证据，不是光学屏幕拍摄，也不是 120 Hz 验收。

2026-10-24 首轮集成内核 `92ac8ce4…` 通过 DMA guard，但与 GPU、触控同时启用时，
Wi-Fi 的 2 MiB BAR 映射耗尽 XNU 仅 8 MiB 的永久 `io_submap`，在启动桌面前 panic。
实际调用栈与反汇编定位到 `lemonade_wifi_init → ml_io_map → io_map → kmem_alloc`。
修正版 `74e65700…` 将晚期 Wi-Fi 寄存器映射移入 `kernel_map`，保留原先设备内存属性，
映射持续到 RAM 内核结束，使退出时的硬件恢复仍可访问寄存器；DMA 数据别名的显式回收不变。
该修正版已通过对应内核的真实 DMA guard 和上述联合验收。

此外，macOS 上从 Codex 验收需使用具备 USB 访问权限的执行环境；普通沙箱看不到
fastboot 接口不能据此断言手机掉线。启动镜像被接收后还须等待加载器的 USB 接口
重新枚举，验收工具已补上这段身份检查与等待。

此前各组件的独立验收记录仍有效，但不替代此包的集成验收：
[图形与显示](LEMONADE-DISPLAY.md)、[Wi-Fi](WIFI-LEMONADE.md)。

## 包含的运行功能

- Adreno 660 GLES 与当前 RedwoodMetal / AIR 运行库。
- SpringBoard 桌面、天气和日历小组件所需服务。
- 按需注册原生 geod、navd、sleepd，供桌面与小组件请求其服务；这不代表定位、导航或睡眠唤醒已移植完成。
- DPU 直接呈现已完成 GPU 渲染的单层 IOSurface；不适用的图层布局回退到 CPU 复制。
- S6SY792 实体触控。
- QCA6490 原机运行固件、Wi-Fi 驱动、wifid、configd、DNS 与 Safari 所需系统服务。

本次整合只为显式 `lemonade_runtime=1` 的完整 Wi-Fi station 模式开放与 GPU、
触控和 CPU 策略共存。其余逐级硬件探测仍保持隔离要求；真实 DMA guard
仍须绑定当前内核哈希，不能沿用另一内核的记录。

## 日常启动与开发验证分开

运行镜像的 `/redwood/bin` 只有必要的 `metal-enable`，`/redwood/sbin/init`
以 `REDWOOD_RELEASE` 构建，跳过内存、文件、进程、设备与计时自检。
完成实际挂载和运行环境准备后，启动 Metal 并直接交给系统 launchd。

包中不含图形/网络测试程序、测试 shader、测试图片、`rwtouch`、自动手势、
`rc.jobs`、调试录制、详细性能采样或定时退出参数。网络密码、账号与既有网络
输入不会打入包内。原生系统服务来自本机已有且校验过的 System 镜像。

`devices/oneplus-9/tools/lemonade-validate-base.py` 可从干净镜像生成一次性的验证副本，临时加入
网络和图形验收程序；它不改发布镜像。网络输入只放入权限受限的临时目录，
结束或异常退出均清理；留存日志先脱敏。真机验证必须持有这台手机的 Device Hub 租约。

## 维护者构建与发布前验收

在既有、已配置 `THEOS` 的构建容器中：

```sh
devices/oneplus-9/tools/lemonade-build-kernel.sh
bash devices/oneplus-9/tools/lemonade-build-base-runtime.sh
```

主机上使用已验证的 Metal 构建、原机固件及本地 System 镜像：

```sh
python3 devices/oneplus-9/tools/test-lemonade-base.py
python3 devices/oneplus-9/tools/lemonade-build-base.py --version 2026.10.24-base.1
# Android 在线、目标身份和租约已核实后：
python3 devices/oneplus-9/tools/lemonade-validate-base.py
# 联合验收通过后，使用包内启动器观察未经扩展的运行镜像：
python3 devices/oneplus-9/tools/lemonade-validate-base-runtime.py
```

同一个版本目录不能被构建器覆盖。发布前须核对：当前内核的 DMA guard、
图形/触控/Wi-Fi 同时运行的实际证据、未经测试程序扩展的干净 base 正常启动和退出，
以及返回 Android 后五个活动启动分区哈希。发布包还包含逐文件 SHA-256 清单。
只将源码与构建工具提交 Git；本机固件、Apple 镜像、设备身份和原始日志不上传。

## 使用与范围

将这台 LE2110 通过 USB 接入已安装 Python 3.11+、adb、fastboot 的 Mac，
双击 `Start.command`。加载器只从 RAM 运行；系统上传约两分钟。
`Return-to-Android.command` 请求正常退出并返回原 Android，再检查启动完成。
启动器只接受此包绑定的设备身份，且不提供刷分区、擦除或切换槽位操作。

当前显示维持 60 Hz，完整 Metal 兼容、120 Hz、睡眠唤醒、完整温控和电源管理仍未完成。
用户数据在 RAM 中，重启后设置与会话不保留。它不是可替代 Android 的完整手机 ROM，
也未完成长期运行验收；蜂窝、蓝牙、充电等未列入此版本的功能基线。
联网验收使用临时验证副本；正式包不包含验证程序和已知网络。
原生设置首次输入 Wi-Fi 密码的完整流程仍需单独验收。
联合压力运行结束、GPU 开始归还硬件之后，观察到 backboardd 在 AIR 运行库中的一次
退出清理异常；内核仍完成硬件恢复并返回加载器，随后 Android 校验正常。
这属于尚待完善的进程与 GPU 停止顺序，不能把这轮记录表述为完整生命周期无异常。

项目保留 [oosexgang/hackios-xnu-kernel](https://github.com/oosexgang/hackios-xnu-kernel)
和对应 userland 的原作者归属及许可，设备适配现维护在 b1ackm4x 的单一 HackiOS 仓库中，两个原仓库的历史均保留。
