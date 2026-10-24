# OnePlus 9 日常 base · 默认 120 Hz

`2026.10.25-base.1` 将已验证的 iOS 120 Hz 显示路径设为日常 base 的默认配置。
双击包内 `Start.command` 即使用 120 Hz，不需要补充启动参数。旧的
`2026.10.24-base.1` 目录、ZIP 和源码标签保持不变，可用于 60 Hz 回退。

## 使用

本机运行包：`out/releases/lemonade-2026.10.25-base.1/`，压缩包为同名 `.zip`。
将此包绑定的 LE2110 通过 USB 接入安装了 Python 3.11+、adb、fastboot 的 Mac，运行
`Start.command`；返回 Android 使用 `Return-to-Android.command`。

默认参数包含 `lemonade_display_hz=120` 和 `lemonade_panel=655`，并启用 DPU 直接显示、
生产者释放同步与系统帧时钟。启动前校验设备身份、文件哈希、面板绑定、对应内核的 DMA
保护测试，以及 120 Hz 与 GPU／触控／Wi-Fi 联合验收记录。面板切换后先测量 64 个 TE
周期，成功后才向 iOS 发布 120 Hz 模式。

运行包包含 Adreno 660 GLES/Metal、SpringBoard、小组件所需服务、实体触控和 QCA6490
Wi-Fi。`/redwood/bin` 仍只有必要的 `metal-enable`；没有测试应用、网络测试程序、自动手势、
开机自检、详细性能采样或定时退出。网络密码和既有账号没有打入包内。

系统从 RAM 运行，重启后设置与会话不保留。它不是完整 Android 替代 ROM。120 Hz 是
固定启动模式，应用实际帧率受自身配置和负载影响；自适应刷新率、长期温控与续航、睡眠唤醒
和其余外设仍待后续验证。原生设置中首次输入 Wi-Fi 密码的完整流程也尚未单独验收。

## 构建与验收

两个源码仓库使用 `main`；具体配对提交和 base 标签见
[`lemonade.sources.json`](../common/devices/lemonade.sources.json)。既有环境中的构建步骤：

```sh
# 在配置好 THEOS 的构建容器中
tools/lemonade-build-kernel.sh
bash tools/lemonade-build-metal.sh
bash tools/lemonade-build-base-runtime.sh

# 宿主：先持有该手机的 Device Hub 租约，核对 Android USB 目标与面板
python3 tools/lemonade-usb.py panel
python3 tools/test-lemonade-base.py
python3 tools/test-lemonade-check-direct.py
python3 tools/lemonade-build-base.py
python3 tools/lemonade-validate-base.py
python3 tools/lemonade-validate-base-runtime.py
```

以上默认选择 `2026.10.25-base.1`；构建器拒绝覆盖已有版本目录。联合验收使用一次性副本
加入图形和网络负载，正式 `root.apfs` 不变。验证结束删除临时网络输入；留存日志先脱敏。
随后使用包内启动器原样运行干净镜像，确认默认模式、持续运行及主动退出恢复。
最后返回 Android，运行 `tools/lemonade-verify-android.py` 核对五个关键分区哈希。

## 证据范围

2026-10-25 联合运行日志 `1792859309`：对应内核的真实 DMA 保护检查通过；面板 64 个 TE
周期实测 **121.248 Hz**，整个运行中的显示窗口均保持 120 Hz 模式。34 项显示检查、61 项
联网检查通过；Metal 功能 101/101、MPS 2/2、Core Animation 4/4。1292 次显示提交全部完成，
提交与释放记录完整，没有回退、失败或退出残留映射。Wi-Fi 完成关联、DHCP、DNS、三次 HTTP、
断线重连及另三次 HTTP。退出时面板恢复 60 Hz，GOP、SMMU、IRQ 与 Wi-Fi DMA 均恢复。

该轮联合负载定时退出期间仍出现此前已有的 backboardd 进程清理异常，内核完成恢复并返回
加载器；不能将这轮压力测试描述成所有进程生命周期无异常。长期稳定性和停止顺序仍待完善。

正式包随后从 Android 经包内 `launch.py start` 原样启动。面板实测 **121.201 Hz**，系统的
120 Hz 模式解析生效；90 秒内的 11 次观察均保持运行，直到宿主明确请求停止。21 项干净
运行检查全部通过，769 次显示提交完成，无失败、回退或残留映射，未见 backboardd 段错误。
默认启动参数、正式镜像哈希、没有测试任务与定时退出，以及面板和 DMA 的正常退出恢复均已
核对。此处验证模式与默认行为，不将静止桌面的提交频率当成 120 fps 动画测量。

主机验证包括 10 项 base 打包／身份／默认参数检查和 8 项显示证据反例检查；base 测试包含
真实 Wi-Fi 运行入口的 ASan/UBSan 检查。正式运行包按逐文件清单校验并压缩，原 60 Hz 包
继续保留。

UIKit 120 fps 和实际变化画面的持续输出证据见 [iOS 120 Hz 验收](LEMONADE-IOS-120HZ.md)。
新 base 的联合运行、未经扩展的默认启动和 Android 回归证据保存在本机
`local/lemonade/release-2026.10.25-base.1/`，不将原始日志、Apple 镜像、原机固件或设备身份
上传到源码仓库。旧 60 Hz base 的验收记录见 [2026.10.24 基线](LEMONADE-BASE.md)。
