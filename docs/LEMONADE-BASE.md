# OnePlus 9 日常运行 base

`2026.10.24-base.1` 将现阶段的 GPU、Metal、桌面显示、实体触控与 Wi-Fi
放入同一个干净的运行镜像。它是此适配项目的功能基线，后续版本继续在此基础上完善。

## 当前交付状态

候选包已构建，位于本机 `out/releases/lemonade-2026.10.24-base.1/`。
包内 APFS 已逐文件回读核对内容、权限、属主和符号链接；主机验证通过。
目前尚未通过这个集成内核的真机联合验收，`manifest.json` 中
`dma_guard_verified` 和 `integration_verified` 均为 `false`，启动器会拒绝启动。
2026-10-24 10:00 的尝试在 Android 切换引导模式后失去 USB 接口，未发送启动镜像。
完成联合验收、干净运行镜像验收和返回 Android 验证后，才能标记为可用 release。

此前各组件的独立验收记录仍有效，但不替代此包的集成验收：
[图形与显示](LEMONADE-DISPLAY.md)、[Wi-Fi](WIFI-LEMONADE.md)。

## 包含的运行功能

- Adreno 660 GLES 与当前 RedwoodMetal / AIR 运行库。
- SpringBoard 桌面、天气和日历小组件所需服务。
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

`tools/lemonade-validate-base.py` 可从干净镜像生成一次性的验证副本，临时加入
网络和图形验收程序；它不改发布镜像。网络输入只放入权限受限的临时目录，
结束或异常退出均清理；留存日志先脱敏。真机验证必须持有这台手机的 Device Hub 租约。

## 构建与验收

在既有、已配置 `THEOS` 的构建容器中：

```sh
tools/lemonade-build-kernel.sh
bash tools/lemonade-build-base-runtime.sh
```

主机上使用已验证的 Metal 构建、原机固件及本地 System 镜像：

```sh
python3 tools/test-lemonade-base.py
python3 tools/lemonade-build-base.py --version 2026.10.24-base.1
# Android 在线、目标身份和租约已核实后：
python3 tools/lemonade-validate-base.py
```

同一个版本目录不能被构建器覆盖。发布前须核对：当前内核的 DMA guard、
图形/触控/Wi-Fi 同时运行的实际证据、未经测试程序扩展的干净 base 正常启动和退出，
以及返回 Android 后五个活动启动分区哈希。发布包还包含逐文件 SHA-256 清单。
只将源码与构建工具提交私有 Git；本机固件、Apple 镜像、设备身份和原始日志不上传。

## 使用与范围

验收完成后，将这台 LE2110 通过 USB 接入已安装 Python 3.11+、adb、fastboot 的 Mac，
双击 `Start.command`。加载器只从 RAM 运行；系统上传约两分钟。
`Return-to-Android.command` 请求正常退出并返回原 Android，再检查启动完成。
启动器只接受此包绑定的设备身份，且不提供刷分区、擦除或切换槽位操作。

当前显示维持 60 Hz，完整 Metal 兼容、120 Hz、睡眠唤醒、完整温控和电源管理仍未完成。
用户数据在 RAM 中，重启后设置与会话不保留。它不是可替代 Android 的完整手机 ROM，
也未完成长期运行验收；蜂窝、蓝牙、充电等未列入此版本的功能基线。

项目保留 [oosexgang/hackios-xnu-kernel](https://github.com/oosexgang/hackios-xnu-kernel)
和对应 userland 的原作者归属及许可，设备适配维护在 b1ackm4x 私有仓库中。
