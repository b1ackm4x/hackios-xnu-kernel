# AMB655X 实验性 120 Hz

本文说明物理面板切换的实现，并保留第一阶段驱动验收记录。后续已完成系统模式发布、
UIKit 和 Core Animation 真机验证，当前使用方法见 [默认 120 Hz base](LEMONADE-BASE-120HZ.md)，
系统接口见 [iOS 120 Hz](LEMONADE-IOS-120HZ.md)，合成与同步见
[图形与显示驱动](LEMONADE-DISPLAY.md)。下文第一阶段的入口限制和待办不代表当前主线状态。

## 设计与修复要点（2026-10-25 汇总）

物理高刷、系统公布的模式、应用请求及实际显示完成分别验证。驱动只在已核对的 AMB655X
命令模式链路上使用原厂切换命令，沿用固件的 PLL、PHY 和 DSC；没有把 `fb_hz=120`
或应用请求值当作面板切换成功。

| 环节 | 实现与修复 |
| --- | --- |
| 面板切换 | 在传输空闲后发送原厂解锁、120 Hz 和重新锁定序列；调整 TE 行计数分频，先记录恢复状态，失败及退出均尝试恢复 60 Hz |
| 物理确认 | 观察至少 64 个 TE 周期且频率在验收范围内，才把活动模式发布为 120 Hz；传输仍独立检查 PP0、行状态和帧计数 |
| 系统帧时钟 | IOMobileFramebuffer 发布已确认模式；当前 base 使用原生 CADisplayTimer，保留硬件 TE 与 GPU/DPU fence；具体接口和历史对照见 iOS 120 Hz 文档 |
| 多层与回退 | 兼容 BGRA 图层合成后沿用 direct 路径；从 GOP 兼容路径重新进入时重新切换并测量，修复回退后继续按 120 Hz 参数运行而物理模式已变为 60 Hz 的问题 |
| 实际吞吐 | 双缓冲使 CPU 合成与 DPU 传输重叠，解决原生多层约 60.5 fps 的瓶颈；Safari 的优先 60 fps 原生偏好另行处理，不能用面板模式代替应用帧率 |

物理切换和恢复位于 [lemonade_display.c](../xnu/files/bsd/dev/lemonade_display.c)，
模式发布、队列和合成位于 [MiniIOMobileFramebuffer.cpp](../xnu/files/iokit/Kernel/MiniIOMobileFramebuffer.cpp)。
这是固定 120 Hz；自适应刷新、完整休眠唤醒和长期功耗尚未完成。

## 第一阶段概况（历史，2026-10-24）

2026-10-24：`codex/lemonade-120hz` 在 LE2110 真机完成 XNU 显示后端的 120 Hz 移动图形
验证。原 `2026.10.24-base.1` 发布目录、压缩包与源码标签保持不变；普通启动仍使用 60 Hz。
本实验没有刷写 Android 分区。

动画由内核持有的两个原生 1080×2400 缓冲区绘制，包含移动矩形和逐帧编号。结果证明
面板 TE 与 DPU 完成不同画面的速率，不代表 SpringBoard、UIKit、CADisplayLink、Metal
应用或整机已支持 120 FPS，也不是高速相机的光学无撕裂测量。

## 真机结果

最终内核 SHA-256：`f10dd60bb187c20b28fcd786fa940ed6d1fa9468ccdc45d278b5c7d8ff51ca67`。

| 项目 | 结果 |
| --- | --- |
| 64 帧先行短测 | 121.205 FPS；64 次完成、63 次相邻图像变化 |
| 第一轮 1000 帧 | 121.187 FPS；最长间隔 8.302 ms |
| 最终内核 1000 帧复跑 | **121.162 FPS**；最长间隔 **8.321 ms** |
| 最终内核实测 TE | **121.187 Hz**；64 个周期耗时 528.108 ms |
| 输出完整性 | 1000 次完成对应 1000 个硬件帧；999 次相邻 MISR 签名变化 |
| 错误 | 欠载、SMMU 错误、异常显示中断均为零 |
| 动画中断退出 | 6 秒退出时源仍在使用；面板、原 GOP、自动传输和映射恢复全部通过 |
| 随后独立 60 Hz 复测 | **60.593 Hz**，间隔 16.499–16.509 ms；加载器未重启 |
| 返回 Android | USB 身份、启动完成、原槽位与锁状态一致；五个关键分区哈希与备份一致 |

120 Hz 是原厂模式名称；本机实测节拍约为 121.2 Hz，恢复后的原厂 60 Hz 节拍约为
60.6 Hz。保留实测数值，不把设定值当成测量结果。

私有证据在 `local/lemonade/120hz-source/`：`final-1000-result.json`、
`return-test-result.json`、`recovery-60hz.json`。对应运行日志编号为 `1792852542`、
`1792852588`、`1792852656`，含校验匹配的清单；Android 回归为
`android-roundtrip-20261024T223845.json`。原始日志、身份记录和镜像不提交。

## 实现范围

- 只接受 Android 实际选择的 `qcom,mdss_dsi_samsung_amb655x_dsc_cmd` 面板，绑定本机身份
  与准备后 DTB 的哈希。切换前核对 DSI 版本、命令模式、DSC、尺寸、空闲状态、中断所有权
  和错误标志，其余面板拒绝。
- 原厂 HS 长写序列为 `F0 5A 5A`、`60 10`、`F0 A5 A5`；恢复使用 `60 00` 和原厂
  12 ms 等待。两种模式的链路时钟、PHY、DSC 相同，本实现不调整 PLL 或 DSC。
- 停止自动传输后切换，保留外部 TE 和 65535 行超时高度，调整 TE 行计数分频。实际观察
  到 64 个约 120 Hz 的 TE 周期后才开始动画。
- 只修改空闲缓冲区，清理修改区域缓存，等待前一次命令完成后提交下一帧。逐帧编号、
  硬件帧计数与 MISR 相邻变化共同判定有效输出。
- 60 Hz 的 CPU 拷贝窗口不能用于 120 Hz。实验入口拒绝 desktop/direct/GPU/触控/联网混合
  运行，不把 `fb_hz=120` 当作物理切换，也不提前向用户空间宣告未验证的模式。
- 第一条面板命令前建立恢复记录。正常完成与定时中断均先恢复面板和 TE，再恢复 GOP、
  自动刷新、SMMU、IRQ 与时钟。命令超时有界，失败仍尝试重新锁定面板。

寄存器和 FIFO 格式依据 OnePlus 的
[SM8350 DSI 控制器](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/techpack/display/msm/dsi/dsi_ctrl_hw_cmn.c)、
[包头转换](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/techpack/display/msm/dsi/dsi_ctrl.c)
与 [TE 配置](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/techpack/display/msm/sde/sde_hw_intf.c)。
面板序列与本机原始设备树及 UEFI 的 AMB655X 初始化表交叉核对。

## 开发入口

需要已有设备基线、可用 root 的原 Android、匹配的构建环境和系统镜像；先取得设备租约，
确保宿主能直接访问 USB fastboot。`panel` 仅保留面板名和哈希，不保存原始命令行。

```sh
python3 tools/lemonade-usb.py panel
docker exec hackios-lemonade-env bash -lc 'cd /work && JOBS=8 tools/lemonade-build-kernel.sh'
python3 tools/lemonade-usb.py boot
# 等加载器 USB 枚举完成；status 应显示 xnuloader
python3 tools/lemonade-usb.py status
python3 tools/lemonade-usb.py ramdisk
python3 tools/lemonade-usb.py run --kernel built --userland --display manual \
  --display-hz 120 --display-flips 1000 --seconds 20
python3 tools/lemonade-check-refresh.py local/lemonade/<运行编号>-run-cp0.log
```

首次调试可省略 `--display-flips 1000`，运行 64 帧短测；验收器要求完整的 1000 帧。
回滚测试另加 `--display-return-test --seconds 6`，检查器另加 `--interrupted`。
随后在同一加载器运行 `--display irq --seconds 12` 复测 60 Hz，再用 `reboot` 返回 Android，
运行 `tools/lemonade-verify-android.py` 核对关键分区。

主机验证包括 51 项 USB 范围与身份检查、17 组原显示恢复测试、5 组真实 C 的 DSI FIFO／
超时／中断恢复模拟，以及 5 项帧率证据反例检查。C 测试使用 ASan/UBSan；内核已实际编译。

下一阶段需接入 IOMobileFramebuffer 模式发布、用户空间帧时钟及 CADisplayLink 应用测试，
并验证 IOSurface 生命周期、60/120 切换和失败回退。当前实验不改变 60 Hz base 桌面路径。
