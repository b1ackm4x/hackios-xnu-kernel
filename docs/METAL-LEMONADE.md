# OnePlus 9 Metal 移植方案

研究日期：2026-10-23。目标为现有 `lemonade` 的 XNU 8796 / iOS 16.5 / Adreno 660，保留已验证的 GLES 路径。本文区分源码能力、主机交叉编译和真机验收；本轮没有操作手机。

## 决策

**先验证 HackiOS 已有的 RedwoodMetal → AIR → Mesa freedreno → RedwoodGPU 路线。** 第一阶段以计算结果和离屏像素为验收，不需要先移植 Turnip，也不需要等待原生 DPU 显示驱动。通过后，再接一个 CAMetalLayer 应用；全局 Metal 发布最后处理。

UniXNU 的 NativeMetal/metal2vulkan/Turnip 作为第二条路线，适合现有实现遇到大量应用接口缺口时评估。复用其用户空间前端或同步设计有价值，不能直接用 A740 内核驱动替换我们的 A660 驱动。

| 路线 | 可以复用 | 新工作 | 当前选择 |
| --- | --- | --- | --- |
| A：RedwoodMetal + AIR/NIR + Gallium freedreno | 当前 iOS 16.5 对象层、RedwoodGPU ABI、A660 启动/SMMU/提交、Mesa | 打包与最小测试；功能缺口、资源生命周期、应用兼容 | 最短真机验证路径 |
| B：NativeMetal + metal2vulkan + Turnip | 较完整的 Metal 对象、资源、blit、事件、管线缓存实现；Mesa A6xx 支持 | iOS 16.5 适配、Turnip 的 Redwood 内核后端、内存导入/同步/显示接口 | 保留独立原型，先验证 Vulkan 提交 |
| C：整个迁移到 UniXNU/sheng | 一套组合好的上游架构 | 重做 SM8350 平台与全部已验证外设适配，换系统版本 | 本轮不采用 |

## 对先前研究的修正

`docs/air.md` 开头说 Metal 对象层和提交 ABI “尚未开始”，已经落后于同一提交中的源码。当前 `userland/src/redwoodmetal.m` 实现了 `_MTLDevice` 子类、GEM 缓冲区、计算/渲染管线、命令记录、深度模板、索引和顶点数据、参数缓冲区等；`redwooddrawable.m` 处理 IOSurface 纹理接入。不能再把 HackiOS 描述成“只有着色器编译器”。

这也不等于完整 Metal：同文件仍有真实未实现或空操作，后面的功能表列出了可定位的例子。`docs/metal.md` 被多处引用，但当前发布树没有这个文件；判断以现有源码和可复现测试为准。

AIR 报告的数字也有不同范围：系统语料 456 个模块中 321 个通过 NIR/ir3；后段游戏语料为 7,879 个模块中 6,057 个翻译成功、6,032 个通过 ir3。两者都不是应用运行比例，也不是本机 A660 的测试结果。早期系统语料的 30 个真机完成项属于上游报告，不能当作我们的验收。

来源：[对象层](https://github.com/oosexgang/hackios-xnu-userland/blob/906425b75e02b4f99eb5d7bb1ef7c9583472d772/userland/src/redwoodmetal.m)、[IOSurface 接入](https://github.com/oosexgang/hackios-xnu-userland/blob/906425b75e02b4f99eb5d7bb1ef7c9583472d772/userland/src/redwooddrawable.m)、[AIR 报告](https://github.com/oosexgang/hackios-xnu-userland/blob/906425b75e02b4f99eb5d7bb1ef7c9583472d772/docs/air.md)。

## 路线 A 为什么能接上当前 A660

```text
iOS Metal.framework
  → RedwoodMetalDevice / 队列 / 编码器 / 资源
  → libredwoodair.dylib：AIR → NIR → ir3，Gallium freedreno
  → redwood-drm：IOConnectCallStructMethod
  → RedwoodGPU：GEM、IOSurface、提交、fence
  → 已适配的 redwood_adreno.c：A660、SMMU、ring、GMU
```

Metal 前端直接打开 `RedwoodGPU`，与当前 GLES 使用同一种 msm 风格调用：`0x42` 分配、`0x43` 查询 IOVA、`0x46` 提交、`0x47` 等待、`0x09` 释放。Mesa 根据内核报告的 chip ID 选择 GPU；当前 Mesa 25.0.7 源码已有 `GPUId(660)`，不需要把 A660 冒充 A635/A740。

`rwa_device_open()` 默认要求真实 GPU；`RWA_SOFTWARE=1` 才进入 softpipe。真机验收必须记录实际设备名、提交计数与 fence 完成，并确认没有启用软件模式。仅有 `MTLCreateSystemDefaultDevice()` 非空不够：前端在 GPU 未启动时仍可能创建可查询的设备对象。

资源与 GPU 仍由同一个内核驱动管理。后续不能让两套驱动同时初始化 GMU、SMMU 或 ring。GLES 已通过的显示、裁剪和退出回收需要作为 Metal 改动的回归项。

## 路线 A 的具体缺口

下表是 2026-10-23 的源码检查，不是整套 API 的完整覆盖统计。

| 范围 | 当前源码 | 影响与下一步 |
| --- | --- | --- |
| 设备发现 | `_MTLDevice`、运行时协议声明、插件 bundle 已有；测试程序使用特定 iOS 16.5 的私有函数地址 | 固定系统/cache 版本；先仅在测试进程注册，区分手动注册与系统发现 |
| 基础计算/绘制 | 已有管线、编码器、提交、读回及大量上游测试 | 先验证确定会终止的计算核和固定 4×4 图案 |
| 视口与光栅状态 | `setViewport:` 只落实深度范围；`setScissorRect:`、cull/front-face/fill setter 为空操作 | UI/游戏会出现真实像素错误；优先把这些状态传入 AIR/Gallium，并做差异像素测试 |
| blit / heaps / resolve | blit encoder、heap 返回 nil；多采样 resolve 没有实现 | 纹理复制、上传、资源管理与抗锯齿路径需逐项补齐 |
| 同步 | command buffer 记录后提交；事件 signal/wait 是空操作 | 基础顺序测试之后，补显式依赖、错误传播、完成回调与资源存活期；不能把方法存在视为语义正确 |
| IOSurface / 无复制内存 | 有 surface→纹理导入；普通 `newBufferWithBytesNoCopy:` 返回 nil | 先证明 drawable 与共享内存是同一份像素，再处理任意用户页导入 |
| 能力声明 | 默认 feature profile 0，报告 Apple1/Common1；源码记录 MPS 预热至少询问 Apple2 | 不能只改 `metal_profile=1` 让应用继续启动；需要证明新增承诺的行为，避免系统进程走到未实现路径 |
| 着色器 | AIR/NIR 已有实际实现；参数缓冲区通过编码记录绑定 | 动态生成的原始参数缓冲区不等于可读；运行时 MSL 文本编译仍不在这条 AIR 路线的已验证范围 |

具体位置：`userland/src/redwoodmetal.m` 的 `featureProfile`、`newHeapWithDescriptor:`、`newBufferWithBytesNoCopy:`、`blitCommandEncoder`、`encodeSignalEvent:`、`setViewport:`、`setScissorRect:`；编译/提交契约在 `tools/mesa/air/air_metal.h` 与 `air_metal.c`。

## 路线 B 的接入边界

UniXNU 当前 NativeMetal 的 ObjC++ 前端接 Rust `metal2vulkan`，翻译到 SPIR-V，再走 Mesa Turnip。其前端已包含 blit、事件、heap、视口、裁剪、函数常量和 IOSurface 导入等实现，但也仍有明确拒绝项，例如部分原始纹理布局和跨进程 shared-event handle。上游成功范围仍需按机型与应用分别看待。[前端源码](https://github.com/UniXNU/NativeMetal/blob/c6d29caad6d880cc8204ebe3bc34d77479a3255d/NativeBridgeMetalFrontend.mm)

| 接口 | 当前 Redwood | NativeAdreno/Turnip 要求 | 适配工作 |
| --- | --- | --- | --- |
| 调用格式 | msm 风格 selector，结构输入/输出 | 独立版本化 ABI，方法 1–16 | 写 Turnip 的 Redwood 后端或严格的适配层，不能只替换 service 名称 |
| GPU 地址 | 内核分配 IOVA；`gpu_va` 只是描述/保护现有地址范围 | 用户空间选择 IOVA、显式 BoMap | 先支持内核分配的地址模式或增加经验证的映射协议；开 `gpu_va=1` 不会自动得到 SET_IOVA |
| BO/导入 | GEM 内存、专用 IOSurface 导入 | 用户页 pin、CPU-map token、导入页生命周期、缓存语义 | 首先做线性离屏 BO；随后接 surface，保留完成前不可释放的约束 |
| 队列/同步 | 全局硬件提交路径、每客户端句柄、msm fence | queue、等待、状态/故障、异步 retire | 定义 reset 后旧 fence/BO 的失效规则；检查多进程并发和释放时序 |
| GPU 参数 | 当前 A660 chip ID、GMEM、布局参数 | `NativeAdrenoAbiDeviceInfo` 的 chip/GMEM/VA/UBWC 等 | 从 A660 实际配置填写；禁止使用 A740 的参数与启动命令 |
| 显示 | MiniIOSurface / MiniIOMobileFramebuffer | NativeMobileDisplay 的 scanout/retire 接口 | 离屏阶段不需要；窗口显示时另写桥接和 fence 交接 |
| 系统 ABI | iOS 16.5 / Metal 306 系列 | 当前构建面向 iOS 17，参考设备为 sheng/A740 | 重新验证 runtime selector、属性、框架符号和设备发布流程 |

特别注意：NativeMetal 最近为 A740 暂设 `TU_DEBUG=sysmem,nocb` 来绕开某些挂起，这不是可直接应用到 A660 的通用调优结论。两条路线最终都需要正确的缓存一致性、资源存活期和显示同步。

依赖必须整组固定：当前构建脚本实际读取的 Mesa pin 是 `adreno-compiler-inputs.json` 中的 `85fda3b…`；较早的 `gpu-metal-vulkan-inputs.json` 另有 `c823a83…` 的研究输入，不能混用。Rust bridge 使用 `metal2vulkan` 的 `720ef1c…` 加 NativeMetal 目录下 7 个补丁，要求 Rust 1.98。只拉某个仓库的最新分支不足以复现。

NativeMetal/NativeAdreno 标注 MIT；`metal2vulkan` 标注 `GPL-2.0-or-later OR LGPL-3.0-or-later`。若采用，保留各组件来源、许可证和补丁记录；本轮仅保存公开参考源码到忽略目录，没有合入这些组件。

来源：[内核 ABI](https://github.com/UniXNU/NativeAdreno/blob/b686b9054f57d393c80195b82568a09f8ca106a2/NativeAdrenoABI.h)、[Turnip 后端](https://github.com/UniXNU/UniXNU/blob/245aca1a9ec5b730b1cad80fade295800d2981f1/patches/mesa/turnip-darwin-kmd.patch)、[实际构建脚本](https://github.com/UniXNU/UniXNU/blob/245aca1a9ec5b730b1cad80fade295800d2981f1/scripts/build-turnip-darwin.py)、[Rust bridge 构建](https://github.com/UniXNU/UniXNU/blob/245aca1a9ec5b730b1cad80fade295800d2981f1/scripts/build-native-metal-bridge.py)、[metal2vulkan](https://github.com/steelbrain/metal2vulkan/blob/720ef1ce9ae2a2068999a8675717418b9416283e/README.md)。

## 本轮实际完成的主机验证

新增 `tools/lemonade-build-metal.sh`，在已有 Linux 容器和 iOS 16.5 SDK 中，只构建候选程序到 `out/lemonade-metal/`，不调用系统镜像打包或设备工具。

```sh
docker start hackios-lemonade-env
docker exec -e THEOS=/work/local/lemonade/toolchain/theos \
  hackios-lemonade-env bash tools/lemonade-build-metal.sh
```

已完成：

- 6 个 ARM64 Mach-O 候选：`libredwoodair.dylib`、`libredwoodmetal.dylib`、`RedwoodMetal` 插件、`air_run`、`metal_drv_test`、`RedwoodTriangle`。
- 前端、drawable 和测试程序请求的 31 个 AIR 动态符号均由新库导出。
- Clang 在 ARM64/iOS 16.5 目标下输出的 16 个共享 `rwa_*` 结构体布局完全一致。检查器还通过了故意改坏一个结构尺寸必须失败的负例。
- 编译前后检查 `root.apfs`、`system.apfs`、`images.json`、Mesa 输出和用户空间 obj 中的 GLES 库，5 个文件 SHA-256 不变。
- 候选产物和源码 hash 记录在 `out/lemonade-metal/manifest.json`；参考来源与 SHA 在 `local/lemonade/metal-research-20261023/provenance.json`，均不提交二进制。

这些结果证明当前源码可以组合交叉编译，且共享结构的布局匹配。没有证明 dyld 在目标系统加载成功、Metal 设备发现、A660 执行、窗口显示或应用兼容。现有镜像仍未加入这些 Metal 候选产物。

## 回到设备后的最小验证顺序

首先从 Wi-Fi 根控制器试验后的 Qualcomm 故障模式恢复 Android，再核对确切目标、原分区 hash、电量和温度。恢复尚未完成；本轮没有再次访问设备。Wi-Fi 的阶段化移植保存在独立分支 `codex/lemonade-wifi`。

Metal 试验必须从已验证的稳定内核源码重新构建并核对 manifest，或使用对应的已验证备份。`out/lemonade-kernel` 目前仍可能是 Wi-Fi 试验留下的产物，不能仅凭文件名把它当作稳定基线。

下列为待实施/执行的验收，不是已完成结果：

| 阶段 | 工作 | 通过标准 |
| --- | --- | --- |
| 0 主机打包 | 从稳定基线制作独立测试 root；加入同一次构建的库、程序和单步 rc.jobs；记录 image/source hash | 镜像一致性检查通过；可回退原 root；全局插件发布保持关闭 |
| 1 设备/资源 | 增加独立 `--device-only` 测试入口及明确失败退出码；测试进程注册一次设备，申请/映射/释放 BO | 真实 A660 名称与参数正确，资源可重复创建回收；明确区分手工注册和自动发现 |
| 2 计算 | 只跑确定会终止的 `MetalFaceMask_InitMinMax`；之后改用自有确定性 fixture | command buffer 完成、内核 fence 前进、结果逐字正确，软件路径未启用；正常返回 loader |
| 3 离屏绘制 | 固定 4×4 渲染、读回全部像素；再测索引、顶点、深度、裁剪差异 | 与参考逐像素一致，故意不画/改变输入必须失败；无 GPU/SMMU fault |
| 4 窗口/回收 | 单个 RedwoodTriangle/CAMetalLayer 应用、IOSurface 共享、重复进入退出 | 应用与合成器看到同一图像，fence 顺序正确；重复后 BO/内存回落；GLES 回归通过 |
| 5 兼容/发布 | 按固定应用清单补状态、blit、同步与能力声明；最后才试系统插件发布 | 每个应用独立记录正确性、崩溃、首帧时间和热态帧耗时；MPS/系统进程启动稳定 |

上游测试程序某些失败路径仍返回 0，不能用“程序退出成功”自动验收。默认无参会继续进入 AV1 测试，也不适合作为纯设备发现探针。新测试包装器必须同时检查预期结果、完成标记、GPU 日志和错误标记。

可复用的首批 shader 调用形式如下，仅用于独立测试镜像中，不在日常 SpringBoard 镜像中批量启动：

```sh
/redwood/bin/metal_drv_test \
  /System/Library/CoreImage/PortraitFilters.cifilter/default.metallib \
  MetalFaceMask_InitMinMax
/redwood/bin/metal_drv_test --draw \
  /System/Library/Extensions/AppleMetalGLRenderer.bundle/default.metallib
```

每阶段从短时 RAM 启动开始，单独收集结果并回收；先不要跑完整 shader sweep。初期不设置全局 `metal=1` 或运行 `metal_props on`，因为能力声明会改变系统进程选择的渲染路径。Wi-Fi、蓝牙和原生 DPU 是独立工作，基础离屏 Metal 测试不依赖它们。

## 固定的研究版本

| 组件 | 版本/提交 |
| --- | --- |
| 当前内核稳定基线 | `155de8c`（`device/lemonade`，本轮研究从此分支建立） |
| 当前用户空间 | `def5fa6aafcc29a6de9edf0b0ac5053b2715c428` |
| HackiOS 用户空间上游 | `906425b75e02b4f99eb5d7bb1ef7c9583472d772` |
| 路线 A Mesa | `25.0.7`，归档 SHA 由 `tools/mk-mesa.sh` 固定 |
| NativeMetal | `c6d29caad6d880cc8204ebe3bc34d77479a3255d` |
| NativeAdreno | `b686b9054f57d393c80195b82568a09f8ca106a2` |
| UniXNU 构建编排 | `245aca1a9ec5b730b1cad80fade295800d2981f1` |
| 路线 B 实际 Mesa pin | `85fda3b36405bcaea8bffc64aa4f2651dc90770d` |
| metal2vulkan pin | `720ef1ce9ae2a2068999a8675717418b9416283e`，另加上述 7 个补丁 |
