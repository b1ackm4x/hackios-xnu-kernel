# OnePlus 9 日常使用路线图

截至 2026 年 10 月 23 日，社区已经从“启动 XNU、显示 SpringBoard”推进到 GPU 合成、原生 Metal、更多机型和外设移植，但这些成果分布在不同设备和系统版本上。对我们的 `lemonade` 分支，近期目标应是能保存数据、稳定联网、正常充电和休眠的 Wi-Fi 随身设备。蜂窝通话、受保护的个人数据和 Apple 账号服务需要另外评估，不能把它们视为驱动补齐后的自然结果。

本文的“已实现”区分上游维护者报告与一加 9 真机验收。上游没有承诺完成时间；“可复用”表示已有代码或明确开发方向，仍需验证接口和机型差异。当前机型证据见 [LEMONADE.md](LEMONADE.md)，仓库操作见 [REPOSITORY.md](REPOSITORY.md)。

## 社区正在推进什么

### HackiOS 扩充通用兼容层并修复构建流程

直接上游 HackiOS 的 redwood 支持表报告了 GLES 桌面、Wi-Fi、音频、相机和数据持久化；这些是我们可研究的驱动框架和用户空间接口，不能直接写进一加 9 的支持表。上游仍将项目定位为定时运行的研究环境，充电、蓝牙、蜂窝和 Apple ID 等不在已支持范围内。[HackiOS 支持表](https://github.com/oosexgang/hackios-xnu-kernel/blob/8e61187098f2c26c5daee8f9f72b6416aa64b9ab/README.md)

项目在 10 月 22 日发布首个预发布版本。截至本次核查，公开 PR 主要处理镜像构建失败传播、旧用户空间预构建兼容和 QEMU 串口造成的进程暂停。这表明当前公共协作的一部分重心是让别人能可靠复现，不能据此推断已有完整的日常使用开发计划。[预发布版本](https://github.com/oosexgang/hackios-xnu-kernel/releases/tag/v0.1.0)、[构建失败处理](https://github.com/oosexgang/hackios-xnu-kernel/pull/4)、[用户空间构建兼容](https://github.com/oosexgang/hackios-xnu-userland/pull/3)、[QEMU 修复](https://github.com/oosexgang/hackios-xnu-kernel/pull/5)

### Metal 有两条值得区分的路线

HackiOS 的 AIR→NIR 路线偏重着色器编译。其详细报告记录：456 个模块中，321 个入口模块能够翻译和生成 Adreno 机器码；对应入口测试中，30 个在真机 GPU 上完成，另有 24 个已提交但未完成。其余 135 个库函数单独统计。文档明确把完整 Metal 对象层和内核提交 ABI 列为另外的工作。因此 README 中的较旧数字，以及“可以编译”的比例，都不代表应用 Metal 兼容度。[AIR 详细报告](https://github.com/oosexgang/hackios-xnu-userland/blob/906425b75e02b4f99eb5d7bb1ef7c9583472d772/docs/air.md)

UniXNU 的 NativeMetal 与 NativeAdreno 是独立实现路线。维护者报告在小米 Pad 6S Pro（SM8550、Adreno 740、iPadOS 17.7.11）上实现了 Metal 设备、桌面 GPU 合成和指定进程的 Metal 设备。核心路径是 Metal 前端、AIR→SPIR-V、Mesa Turnip、原生 GPU 提交；首次着色器编译仍慢。该成果更接近完整运行链路，但不能外推为所有 Metal 应用均兼容。[NativeMetal](https://github.com/UniXNU/NativeMetal/blob/c6d29caad6d880cc8204ebe3bc34d77479a3255d/README.md)、[驱动架构](https://unixnu.com/drivers/native-adreno)

这套方案值得跟踪和做独立原型，不能直接合并替换我们的 GPU 驱动：A740 与 A660 的启动、命令和电源管理不同，iPadOS 17 与当前 iOS 16.5 的驱动接口也不同；UniXNU 使用的原生 IOSurface/IOGPU 集成方式与我们的 Mini 驱动存在结构差异。[sheng 配置与系统版本](https://unixnu.com/devices/sheng)

### 新机型移植正在复用已有成果

`ios-sweet` 在 Redmi Note 10 Pro 上研究 GENI I²C 触控、SPMI 按键和可写 UFS；`ios-topaz` 明确复用了 HackiOS 的 GPU、显示和用户空间工作，并提供直接扫描连续帧缓冲及 Wi-Fi 的实现线索。这些项目有助于减少重复摸索，但都不能替代一加 9 的设备参数与实测。[ios-sweet](https://github.com/dipa-studio/ios-sweet/blob/067c9b3d463ecfbe37520683637ea2e6af31f3a4/README.md)、[ios-topaz](https://github.com/MakrSas/ios-topaz/blob/e4772911e116ab4ab6c4ac1f38e6106fefe8b483/README.md)

`ios-topaz` 的 10 月 22 日更新段与下方旧功能表对 CPU、音频等状态存在冲突，因此其充电、音频等宣传不能作为验收结论。本轮将它作为补丁参考。社区的机型目录也只能证明存在移植入口，不能证明列出的每台设备可日用。

### 性能工作已深入内存和帧提交

近期 UniXNU 提交集中在 IOSurface 导入、内存生命周期、缓存一致性、着色器缓存和显示提交线程；NativeAdreno 还暂时关闭了尚未完成恢复流程的 GPU 低功耗机制。这说明桌面跑起来之后，稳定性、功耗和帧延迟仍需共同解决。[显示线程优先级](https://github.com/UniXNU/NativeMetal/commit/c6d29caad6d880cc8204ebe3bc34d77479a3255d)、[导入缓冲区处理](https://github.com/UniXNU/NativeAdreno/commit/b686b9054f57d393c80195b82568a09f8ca106a2)、[暂时关闭 IFPC](https://github.com/UniXNU/NativeAdreno/commit/26220f1ab79fec643517336fbd81574bd1e0d05a)

即使是当前报告原生 Metal 成功的 sheng，支持表仍缺 Wi-Fi、蓝牙、音频、电池驱动和休眠唤醒。图形进展不能代表整机成熟度。[sheng 支持表](https://github.com/UniXNU/UniXNU-sheng/blob/251b291adf941b9953fe3ae66a808b9b626e164d/README.md#device-support)

## 我们已经达到的位置

一加 9 已在不刷写分区的 RAM 启动方式下运行 iOS 16.5，完成 A660 GLES 与 CoreAnimation、受控 CPU 频点、实体 S6SY792 触控的基础联调。受控测试中 Settings 连续滚动 96 秒达到 59.99 FPS；包含打开应用等操作的相同 24 次手势为 51.95 FPS。前者不能代表所有场景稳定 60 FPS。

后续 15 分钟体验记录了 52 次实体按下和对应抬起，没有 I²C 错误。09:07 检查到的停屏由 900 秒定时退出解释，退出后显示最后一帧；更早是否出现应用卡顿仍没有用户时间信息。这也暴露出体验模式缺少明确的结束状态。

当前显示仍依靠 CPU 缩放、拷贝到 UEFI GOP；触控通过 GPIO 模拟 I²C 并轮询中断线；CPU 使用固定受控频点。Wi-Fi、音频、实体按键、休眠、充电管理和持久化尚未在本机完成验收。上述数字和边界均来自 [一加 9 验证记录](LEMONADE.md)。

## 上游与机型分支的责任边界

这里的“本机负责”是指最终必须由持有一加 9 的适配者完成，不意味着所有代码都要重写。Linux、OnePlus 原厂内核和其他机型提供硬件协议、驱动或实现线索；XNU 接口与整机验证仍由本分支承担。

| 能力 | 可依靠的共享工作 | 一加 9 必须补齐的部分 | 当前判断 |
| --- | --- | --- | --- |
| XNU 和 iOS 用户空间 | HackiOS 的启动、IOKit 替代服务、应用兼容、构建修复 | 保持本机回归测试，修复板级资源与通用层的交互 | 优先跟随直接上游，选择性回合补丁 |
| Metal 和应用图形 | HackiOS AIR 编译器；UniXNU NativeMetal；Mesa freedreno/Turnip | A660 提交后端、iOS 16.5 ABI、IOSurface、同步与应用验证 | 共享层有明确进展，本机接入仍是项目 |
| 显示与帧同步 | Linux SM8350 DPU/DSI/DSC 驱动；其他机型的扫描输出设计 | AMB655X 面板、DSC 命令模式、TE 同步、DPU SMMU、缓冲区释放时机 | 本机负责，不能等待 redwood 驱动自然覆盖 |
| 触摸与实体按键 | 通用 HID 桥；其他机型 GENI/SPMI 实现 | S6SY792 的 GENI I²C 与 IRQ、复位和电源流程、多指、侧键、锁屏唤醒 | 基础触摸已通，长期使用路径本机负责 |
| 功耗与充电 | Qualcomm/Linux 电源、温度与充电协议；共享 IOPower 接口 | 电池真实读数、电量计配置、充电状态机、限流和温控、CPU/GPU DVFS、休眠恢复 | 日常使用的前置条件，本机负责 |
| Wi-Fi 与蓝牙 | HackiOS 的 Linux 驱动兼容层、网络服务桥；Linux 无线驱动 | 核对实际芯片和总线、固件加载、供电、校准、重连与省电；蓝牙另行适配 | 可复用框架，不能直接开 redwood 的开关 |
| 音频与传感器 | HackiOS 音频接口和 DSP 路径；原厂内核 | 本机 DSP 拓扑、codec、功放、麦克风路由、传感器与定位链路 | 主要是板级集成，录放音先于相机 |
| 相机与视频 | 上游媒体服务和相机框架经验；Mesa 只解决部分图形问题 | 本机传感器、ISP、校准、控制、编解码接入 | 工作量大，放在基础日用之后 |
| 存储与独立启动 | HackiOS UFS/持久化；UniXNU 与 sweet 的存储经验；Mu-Silicium | SM8350 UFS、可恢复的磁盘布局、写入/断电一致性、不接电脑启动 | 先只读验证，再设计持久化；不照搬清空 userdata 的方案 |
| 蜂窝网络与电话 | Qualcomm QMI/QRTR 资料；通用 CoreTelephony 兼容工作可共享 | SM8350 modem 与 iOS 的桥接，数据、SIM、短信、语音及 IMS 分阶段集成 | 本次查到的项目中，没有可直接采用的完整路线 |
| 数据安全与 Apple 服务 | 可共享签名校验、沙箱、密钥存储改进 | 本机密钥保护与数据加密集成；Apple 服务还依赖外部身份和授权 | 没有可承诺的完成时间，不能作为近期交付条件 |

主要硬件依据：[HackiOS 移植说明](https://github.com/oosexgang/hackios-xnu-kernel/blob/8e61187098f2c26c5daee8f9f72b6416aa64b9ab/docs/PORTING.md)、[OnePlus SM8350 原厂内核](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/tree/13ca08fdf0979fdd61d5e8991661874bb2d19150)、[Linux SM8350 DPU 配置](https://github.com/torvalds/linux/blob/v6.12/drivers/gpu/drm/msm/disp/dpu1/catalog/dpu_7_0_sm8350.h)、[Mesa 驱动说明](https://docs.mesa3d.org/drivers/freedreno.html)、[Mu-Silicium](https://github.com/Project-Silicium/Mu-Silicium/tree/42fe563791e51bf5500c2e470ed294ca0c94b97c)。Mu-Silicium 改善 UEFI 启动基础，不会自动提供进入 XNU 后所需的整机驱动。

## 日常使用还有两类结构性问题

### 手机通信需要完整协议桥

原厂 modem 固件能启动，并不等于 iOS 可以打电话。直接上游记录了 CommCenter 对 AppleIPAppender 和 AppleBasebandPCI 的依赖，以及其与 Qualcomm QMI 路径的差异。蜂窝数据可作为独立实验，电话、短信、IMS/VoLTE 和运营商行为还需要后续集成；Wi-Fi 成功不代表这条链路也已解决。[HackiOS 的 CommCenter 调查](https://github.com/oosexgang/hackios-xnu-userland/blob/906425b75e02b4f99eb5d7bb1ef7c9583472d772/userland/mkjobs.py#L67-L86)

### 当前锁屏和钥匙串不提供预期的数据保护

当前 MiniAppleKeyStore 明确模拟无密码、始终解锁状态，包装密钥的转换不提供保密性；MiniAMFI 没有真实的签名信任策略。因此“能显示锁屏”不代表数据受保护。持久化前必须把这项差距列入设计，不能把真实个人账号、支付凭据和重要文件放入当前系统。[MiniAppleKeyStore](../xnu/files/iokit/Kernel/MiniAppleKeyStore.cpp)、[MiniAMFI](../xnu/files/libkern/amfi/miniamfi.c)

本地加密与密钥保护是可以单独研究的工程问题；Apple ID、App Store/FairPlay、APNs 和 Apple Pay 还涉及 Apple 的身份与服务信任。本次查到的项目没有提供可用性保证。不能把替代 keybag 或跳过激活界面视为这些服务已被解决。[上游功能边界](https://github.com/oosexgang/hackios-xnu-kernel/blob/8e61187098f2c26c5daee8f9f72b6416aa64b9ab/README.md#what-works)

## 建议实施顺序与验收条件

以下是建议的验收门槛，不是已经完成的测试，也不是进度承诺。

| 阶段 | 实施内容 | 进入下一阶段的证据 |
| --- | --- | --- |
| 1 稳定体验与可观测性 | 显式显示会话结束状态；测真实电池、电流、温度；定位服务重试与空闲 CPU；建立可取回的故障日志 | 能区分定时退出、应用卡顿、输入失效和内核挂死；相同负载的功耗、温度与重试计数可复现 |
| 2 可控电源与基础操作 | 侧键、触控 GENI/IRQ、背光；充电与过温/低电处理；分阶段加入 DVFS | 插拔电源、低电和温度变化状态正确；持续使用不会依赖未知充电状态；基础点击、滚动、返回和锁屏完整通过 |
| 3 Wi-Fi 与数据保存 | 本机无线固件/总线适配；先验证 UFS 只读，再准备独立、可回滚的可写数据区与启动入口 | Settings 连接后真实访问网页；断线重连；20 次重启后测试文件校验一致；断电恢复在专用测试数据上通过 |
| 4 低功耗与显示链路 | DPU 扫描输出、TE 同步、缓冲区同步；系统休眠/唤醒与网络恢复；音频录放 | 50 次锁屏/唤醒无丢触摸、黑屏或网络失联；持续交互记录帧延迟、功耗、温度；基础条件通过后做 24 小时运行测试 |
| 5 应用兼容与长期使用 | 在独立分支试接 Metal，固定应用测试清单；补音频、蓝牙、传感器；逐步加强数据保护 | 对目标应用分别记录启动、输入、联网、媒体、后台与重启恢复；连续一周使用记录未解决问题 |
| 后续专项 | 相机、定位、蜂窝和 Apple 服务 | 每条链路独立立项，先完成最小验证，再决定是否纳入日用目标 |

DPU 显示可以在电源和联网工作期间穿插推进，具体寄存器与验证顺序见 [长期显示方案](LEMONADE.md#长期显示方案调研2026-10-23尚未作为驱动实现)。它能减少 CPU 搬图和帧同步问题，但单独完成也不能解决待机耗电。

空闲开销应先测再改：最近一次体验日志同时出现 CommCenter/sleepd 查询失败、lsd CPU monitor 消息和持续 CPU 活动。目前没有对照实验能把 CPU 开销归因给其中某一个服务；应逐项比较，不能直接批量删除系统服务。上游已有服务相互依赖的案例。[用户空间服务调查](https://github.com/oosexgang/hackios-xnu-userland/blob/906425b75e02b4f99eb5d7bb1ef7c9583472d772/userland/mkjobs.py)

## 分支维护与跟踪策略

保留 HackiOS `device/lemonade` 为当前稳定实验基线，继续吸收通用修复。本轮不迁移到 UniXNU；未来先在独立实验分支验证 A660 的 Metal 设备发现、最小渲染、资源释放和显示同步，再判断接入成本。这样不会为了新图形路线同时更换内核版本、用户空间和全部已验证驱动。

优先跟踪三个来源：HackiOS 的通用接口与应用修复，UniXNU 的 Metal/缓冲区/缓存实现，Linux 与 OnePlus 的 SM8350 硬件驱动。sweet/topaz 作为具体问题的参考。按子系统摘取有实测意义的改动，记录来源和许可证，避免直接覆盖整个机型分支。

本次结论来自项目文档、源码与公开提交，不把群聊热度或仓库关注量当作成熟度。下列版本固定了本次比较范围；网页与后续提交可能继续变化。

| 项目 | 2026 年 10 月 23 日核查的提交 |
| --- | --- |
| HackiOS kernel | `8e61187098f2c26c5daee8f9f72b6416aa64b9ab` |
| HackiOS userland | `906425b75e02b4f99eb5d7bb1ef7c9583472d772` |
| UniXNU | `245aca1a9ec5b730b1cad80fade295800d2981f1` |
| UniXNU sheng | `251b291adf941b9953fe3ae66a808b9b626e164d` |
| NativeMetal | `c6d29caad6d880cc8204ebe3bc34d77479a3255d` |
| NativeAdreno | `b686b9054f57d393c80195b82568a09f8ca106a2` |
| ios-sweet | `067c9b3d463ecfbe37520683637ea2e6af31f3a4` |
| ios-topaz | `e4772911e116ab4ab6c4ac1f38e6106fefe8b483` |
| Mu-Silicium | `42fe563791e51bf5500c2e470ed294ca0c94b97c` |
