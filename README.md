# HackiOS · OnePlus 9

基于 [oosexgang/HackiOS](https://github.com/oosexgang/hackios-xnu-kernel) 的一加 9（LE2110 / `lemonade`）适配，运行 XNU 与 iOS 16.5 用户空间。

## 当前进度

当前 base：**`2026.10.24-base.1`**，源码标签：`base-2026.10.24-1`。

| 模块 | 已验证范围 |
| --- | --- |
| GPU | Adreno 660 GLES / Metal；Metal 101 项、MPS 2 项、Core Animation 4 项通过 |
| 显示与触控 | SpringBoard 桌面、60 Hz DPU 直接显示、实体触控；显示检查 28 项通过 |
| Wi-Fi | QCA6490 关联、DHCP、DNS、HTTP 和断线重连；联网检查 61 项通过 |
| 干净运行包 | 已移除自检、测试程序、自动手势、调试录制和定时退出；默认运行与正常退出检查通过 |

以上包含同机联合验收，尚未完成长期稳定性验证。系统只在 RAM 中运行，不刷写 Android 分区，重启后设置与会话不保留。

实验分支 `codex/lemonade-120hz` 已接通 iOS 系统的 120 Hz 显示路径。两轮真机验收中，UIKit `CADisplayLink` 达到约 **120 fps**，对应变化画面的 DPU 输出为 **119.57–119.96 fps**；Core Animation 动画输出为 **121.15–121.19 fps**。默认 60 Hz 对照与退出恢复均通过，原 base 保持不变。启动方法、结果与适用范围见 [iOS 120 Hz 验收](docs/LEMONADE-IOS-120HZ.md)。

## 使用

本机发布包：`out/releases/lemonade-2026.10.24-base.1.zip`。

Mac 需安装 Python 3.11+、adb 和 fastboot。USB 连接这台一加 9，解压后双击 `Start.command`；返回 Android 使用 `Return-to-Android.command`。

## 待完善

- 完整 Metal 兼容、120 Hz 长时间稳定性与动态刷新率切换、睡眠唤醒、温控与电源管理，以及其余外设。
- 原生设置首次输入 Wi-Fi 密码的完整流程尚未单独验收；天气小组件仍显示占位内容。
- 压力测试结束时存在显示服务清理异常；干净 base 的正常退出已通过。

## 代码布局

| 路径 | 内容 |
| --- | --- |
| `xnu/files/` | 内核、GPU、显示、触控和 Wi-Fi 驱动 |
| `xnuloader/`、`uefi/` | RAM 加载器与设备启动支持 |
| `userland/` | init、图形运行库和系统服务，属于独立用户空间仓库 |
| `common/devices/lemonade*` | 机型配置与配套源码版本 |
| `tools/lemonade-*` | 构建、打包、启动与开发验收工具 |
| `docs/` | 移植记录、构建步骤和验收细节 |

Base 代码已合入两个仓库的 `main` 分支；120 Hz 实验保留在独立分支。普通 `git` 管理本仓库，`common/ugit` 管理配套的 [用户空间仓库](https://github.com/b1ackm4x/hackios-xnu-userland)；版本对应关系见 [lemonade.sources.json](common/devices/lemonade.sources.json)。

详细说明：[Base 与验收](docs/LEMONADE-BASE.md) · [构建与移植](docs/LEMONADE.md) · [显示](docs/LEMONADE-DISPLAY.md) · [Wi-Fi](docs/WIFI-LEMONADE.md) · [仓库维护](docs/REPOSITORY.md)。

保留原作者 OOSEX GANG 的归属、历史与许可；XNU 许可见 [LICENSE](LICENSE)，第三方声明见 [NOTICES](prebuilt/NOTICES.md)。Apple 系统文件、原机固件、凭据、构建产物和私有日志不提交。Redwood/QEMU 的使用方式见[上游文档](https://github.com/oosexgang/hackios-xnu-kernel#readme)。
