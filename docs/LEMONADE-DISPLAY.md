# OnePlus 9 图形与显示验收

2026-10-24 的增量实现和实机结果。本页记录实际已通过的范围；不把 Metal 设备枚举、
平均帧率或帧缓冲截图当作完整兼容、无撕裂或 120 Hz 的证明。

## Metal 绘制与复制

在 LE2110 / SM8350 / Adreno 660 上，以 Apple 编译器生成的 iOS AIR 执行真实 GPU 工作，
使用像素与缓冲区回读检查结果。原有的 GLES `FD660` 和共享 IOSurface 检查继续通过。

本轮修复：

- viewport、scissor 及单元素数组形式，并逐次绘制保存状态；空裁剪、负原点和小数视口生效。
- 正反面剔除、顶点绕序、线框、深度裁剪/钳制和深度偏移。
- load-action clear 作为独立的延迟 GPU 操作执行，覆盖没有 draw 的 pass，多个颜色附件保留各自颜色。
- 已编码的工作保留 pipeline、buffer、texture 和 sampler 到提交完成，防止提前释放。
- GPU buffer fill/copy、texture region copy、逐级 mipmap，以及带行跨度的 buffer/texture 互拷。
- 修正 Freedreno buffer 分配标志的条件运算符优先级，使共享 buffer 真正可导出供 GPU 纹理别名使用。
- Metal 设备名称取自实际 Mesa screen，移除硬编码的另一款 Adreno 名称。
- 2× / 4× 颜色 MSAA resolve；修正 `nr_storage_samples`，验证半像素边缘的真实平均值。
- 序列化 AIR/Gallium 公共入口，避免创建资源、编译、提交和回读并发访问一个 context。
- 正常启动时的 Metal bundle 发现，以及 MPS 所需的 `MTLCommandBuffer` 协议一致性。
- AIR 图像写入按 value operand 保留浮点/半精度类型；修复 MPS 输出为零。
- 半精度 varying 在阶段边界用 32 位传输，按语义名称链接顶点和片元输入；修复纹理合成色块。
- Stencil8 使用 Adreno 支持的深度/模板存储，并以模板视图供计算着色器读取。
- 半精度平方根和点积提高中间运算精度，圆角覆盖回读误差收敛到原生 Metal 对照范围内。
- flat varying 使用 Metal 规定的首顶点；索引/非索引的三角形列表和条带均检查实际颜色。
- 同一命令缓冲内合并 GPU 工作，在提交结束等待完成；Gallium surface 和 sampler view 按引用计数释放。
- 片元导数、隐式纹理 LOD 与 quad 操作保留相邻像素计算；half 导数用 32 位指令后收窄。
- 混合常量按每次 draw 保存，支持 4 种常量因子；无深度附件时仍使用 Metal 的 `[0,1]` 近远裁剪规则。
- 从专门化后的 NIR 计算常量缓冲实际读取范围，跳过已消除的读取；动态索引继续保留完整范围。
  避免每个 draw 因未对齐偏移而重复上传整个大 ring buffer。

早期 15 项光栅、13 项特性与 26 项 IOSurface 检查通过；特性套件随后逐步扩充，
每轮数量和结果见下表。测试包括边界外深度、正负深度偏移、计算结果和越界哨兵、延迟编码顺序、
释放源纹理后提交、四级 mipmap、保持行填充内容的双向复制。
同一特性测试在 Apple M4 的原生 Metal 上通过。剔除方向先以原生 Metal 结果校正，
再在 Adreno 上复测；没有通过调整预期结果掩盖硬件差异。

关键回归证据：

| 场景 | 私有日志 | 结果 |
| --- | --- | --- |
| 旧实现的 viewport/scissor | `1792779578-run-cp0.log` | 9 项中 8 项失败 |
| 修复 viewport/scissor | `1792779706-run-cp0.log` | 9 项通过 |
| 绕序修复与线框 | `1792780477-run-cp0.log` | 15 项通过 |
| 复制与共享 buffer 修复 | `1792781797-run-cp0.log` | 15 + 13 项通过 |
| 硬件刷新回归 | `1792782105-run-cp0.log` | 15 + 13 + 26 项通过 |
| MSAA storage sample 修复 | `1792782890-run-cp0.log` | 17 项特性检查通过 |
| clear-only 旧实现 | `1792783507-run-cp0.log` | 两项清屏失败；四线程 128 次渲染回读通过 |
| clear-only 修复 | `1792783682-run-cp0.log` | 20 项特性检查通过 |
| 二维图像计算旧实现 | `1792783883-run-cp0.log` | float / half 写入失败；MPS 输出为零 |
| AIR image-store 类型修复 | `1792784108-run-cp0.log` | 26 项特性检查 + 2 项 MPS 检查通过 |
| RGBA/BGRA 混合和 framebuffer fetch | `1792784320-run-cp0.log` | 38 项特性检查 + 2 项 MPS 检查通过 |
| 实际纹理采样与半精度插值旧实现 | `1792784531-run-cp0.log` | 8 项采样失败；Stencil8 4 项因格式被拒绝失败 |
| 全尺寸多次 framebuffer fetch | `1792784622-run-cp0.log` | RGBA/BGRA 1125×2436 两项通过；前述 12 项仍失败 |
| VPC 半精度变量传输修复 | `1792784733-run-cp0.log` | 8 项采样全部通过；52 项中仅 Stencil8 4 项失败；MPS 2 项通过 |
| Stencil8 存储与采样修复 | `1792784845-run-cp0.log` | 56 项特性 + 2 项 MPS 全部通过 |
| 圆角覆盖精度 | `1792784937-run-cp0.log` | float 覆盖与 CPU 一致；half 最大误差 7，原生 Apple 最大 4，128 个通道超出阈值 |
| 语义链接与半精度运算修复 | `1792785198-run-cp0.log` | 59 项特性 + 2 项 MPS 全部通过；half 覆盖最大误差 2 |
| CPU/GPU 共享图像直接读写 | `1792785669-run-cp0.log` | 61 项特性 + 2 项 MPS 全部通过；32 次带行填充的小图和 4 次全屏读写均为零差异 |
| flat varying 旧实现 / 首顶点修复 | `1792785824` / `1792785974` | 原有 4 项失败，修复后 65 项特性 + 2 项 MPS 全部通过 |
| 公共 CoreAnimation 圆角集成 | `1792786290-run-cp0.log` | 圆形角、连续角、子层裁剪 3 项失败；CGPath 圆角通过，与桌面裂缝相符 |
| 首轮批量提交 | `1792786507-run-cp0.log` | 延迟 flush 访问已销毁 surface 导致崩溃；此版本不合格 |
| 批量引用生命周期修复 | `1792786822-run-cp0.log` | 73 项特性 + 2 项 MPS 全部通过，无 SIG11；CA 仍有 3 项失败 |
| 导数专项旧实现 | `1792787034-run-cp0.log` | 77 项中 3 项失败：float 导数边界、half 导数渐变、half 圆角导数；Apple 同一库 77 项通过 |
| 相邻像素计算修复 | `1792787144-run-cp0.log` | float 导数边界归零，CA 四项全部零差异；77 项中仅 half 导数渐变仍失败 |
| half 导数转换修复 | `1792787251-run-cp0.log` | 77 项特性 + 2 项 MPS + 4 项 CA 全部通过；实际桌面圆角裂缝消失 |
| 减少普通绘制屏障 | `1792787374-run-cp0.log` | 77 + 2 + 4 项通过，无崩溃；手指按下期间 13.46 FPS，未解决剩余性能瓶颈 |
| 混合常量 / 无深度附件裁剪旧实现 | `1792787494-run-cp0.log` | 86 项中 5 项失败：4 种混合常量被拒绝，近裁剪错误；Apple 86 项通过 |
| 混合常量 / 裁剪修复 | `1792787610-run-cp0.log` | 86 项特性 + 2 项 MPS + 4 项 CA 全部通过；90 秒无崩溃、越窗、丢弃或异常中断 |
| 常量缓冲读取范围优化 | `1792788259-run-cp0.log` | 88 项特性 + 2 项 MPS + 4 项 CA 全部通过；新增静态索引 3 和动态索引 127 的未对齐缓冲测试，Apple 同一库亦全部通过 |
| 默认频点连续滚动 | `1792788769-run-cp0.log` | 五分钟运行，192 秒连续滚动后 88 + 2 + 4 项全部通过；12,780 次呈现无越窗、丢弃或异常中断 |
| 最终进程内回归 | `1792788880-run-cp0.log` | FD660 GLES 回读、26 项 IOSurface、15 项光栅和 88 项特性全部通过；两项非法索引按预期拒绝，90 秒正常返回 |

测试入口是 `--metal-test`。它只在测试进程内注册 Metal，桌面仍使用 GLES。
`--metal-system apps` 和 `--metal-system desktop` 是另行验收的系统发现/合成路径，
不能用进程内测试替代它们的验收。

## 硬件刷新同步

`lemonade_display.c` 仅在 SM8350 匹配且显式选择时启用。实机读回的是 DPU 7.0，
两个 540×2400 mixer、两路 VIG 读取同一个 1080×2400 / stride 4320 的固件 GOP，
INTF1 使用命令模式和自动刷新。未改动 DSI、PLL、面板模式、像素地址或缩放参数。

从设备树和 Linux 驱动核对显示中断为 SPI 83（GIC intid 115），INTF1 TEAR read-pointer
为 bit 2。先做 32 次有界中断测试，确认事件对应扫描开始；没有仅凭一个递增计数器就
把它当作 vsync。当前内核时钟测得中断间隔约 16.50 ms；此数字没有独立光学校时。

`--display sync` 启用持续 TE 中断、IOMobileFramebuffer 通知和 `ca_vbl=1`，
使 CoreAnimation 实际接收显示驱动的刷新通知。ISR 只确认所拥有的中断位并安排回调。
注册/注销与回调生命周期有锁保护；中断过快、来源异常或长时间停止时停止硬件路径。
普通退出和 panic 返回都恢复自己修改的中断配置及两项总线时钟请求。

当前呈现仍由 CPU 将 IOSurface 缩放复制到固件 GOP。复制前等待 TE 后的安全空闲窗口，
并读取实际行计数确认屏幕未传输；每个复制 worker 排空写入后再记录完成。
这改善同步，但尚不是 DPU 直接扫描 IOSurface，也没有消除 CPU 全屏复制。

`1792782105-run-cp0.log` 的 100 秒测试：

- 5,957 次 TE 事件，异常中断 0。
- 1,349 次呈现，检测到越窗 0，主动丢弃 0。
- 11 次固定输入动作，包含 8 次往返滚动；所有长帧均保留。
- 手指按下期间平均 56.63 FPS，全部活动窗口平均 54.50 FPS。
- 活动窗口复制耗时均值 2.826 ms，P95 3.354 ms，最大 4.993 ms。
- GPU 传感器 50.9 / 50.6°C；不是电池或机身温度。
- 返回时 TEAR enable、GIC enable/route 与总线时钟请求读回原值。

帧率表示 GOP 完成间隔，不代表实际面板帧数。截图无法证明扫描过程中没有撕裂。
旧的 24 手势、96 秒 Settings 等测试工作负载不同，不能直接用它们计算本轮提升百分比。

上述约 56 FPS 属于 GLES 桌面。系统 Metal 桌面必须单独报告：同一 11 手势工作负载下，
315 MHz 的逐 draw 等待版本 `1792785974` 手指按下期间为 9.19 FPS，
批量提交及引用生命周期修复后 `1792786822` 为 13.30 FPS，全部活动窗口为 13.21 FPS。
后者活动帧复制均值 3.088 ms；90 秒 5,352 次 TE、727 次呈现，越窗/丢弃/异常中断为零。
此时仍有明显瓶颈，不能用早先 GLES 的结果代替 Metal 性能。
`1792787610` 的分阶段汇总显示滚动时每份命令缓冲执行约 65–69 ms，最后 GPU fence 约 3 ms；
剩余优化重点为 CPU 侧绘制路径。该轮手指按下期间 13.29 FPS。

`1792787902` 的逐阶段计时进一步发现，512 次 draw 重复上传约 120–200 MB 常量数据，
绑定阶段占约 240–370 ms。`1792788259` 从专门化后的 NIR 推导保守读取范围后，
同样 512 次 draw 仅上传约 40–43 KB，绑定阶段约 0.6–1.0 ms。
静态范围以外的动态索引不截断，新增测试访问第 127 个 float4 验证此边界。
在默认 315 MHz、相同 11 手势下，手指按下期间从 13.78 提高到 54.68 FPS，
全部活动窗口从 13.66 提高到 53.49 FPS。活动间隔 P50 16.494 ms、P95 32.455 ms、
最大 265.339 ms，长帧全部保留。活动帧复制平均 3.239 ms。
90 秒 5,352 次 TE、1,199 次呈现，越窗/丢弃/异常中断为零。
优化后滚动时每份命令缓冲的 CPU 执行约 2.7 ms，最后 GPU fence 约 7.5 ms；
GPU 与呈现等待成为后续测量重点，不能据平均帧率称为稳定满帧。

`--gpu-clock 315/379/443/540` 对照该机原始设备树的公共 OPP，并只允许已核对的 speed bin。
默认仍为 315 MHz。379 MHz 通过 10 秒 GPU 图案与返回测试；540 MHz 尚未实测。
443 MHz 的 `1792788400` 另通过同一套 88 + 2 + 4 项检查及 90 秒桌面测试，
读回实际 442,999,912 Hz。11 手势的手指按下期间为 55.92 FPS、全部活动窗口 54.80 FPS，
仅比默认频点小幅提高；P95 32.777 ms、最大 132.664 ms，复制均值 3.145 ms。
5,352 次 TE、1,207 次呈现，越窗/丢弃/异常中断为零。GPU 传感器为 50.9 / 50.6°C。
这不是更高频率的长期稳定性验收；默认值未提高，也没有修改显示 PLL 或面板刷新模式。

默认 315 MHz 的五分钟 `1792788769` 追加了 192.022 秒连续往返滚动，并在结束后
重新执行全部 88 + 2 + 4 项像素检查，全部通过。连续段 11,233 帧，平均 58.32 FPS，
间隔 P50 16.516 ms、P95 30.729 ms、最大 35.575 ms；首帧等待 27.244 ms。
整轮 14 个输入动作、12,780 次呈现、18,076 次 TE，越窗/丢弃/异常中断为零。
GPU 两路传感器最高 57.6 / 56.9°C，退出时显示中断与两项时钟请求恢复。
这组连续工作负载不同于早先 11 手势或 GLES 的 96 秒场景，不能混算提升百分比。
它也未达到 30 分钟验收，持续动态温控及日常使用仍未确认。

## 复现

先准备项目的 Linux 构建环境、iOS 16.5 SDK、核验过的系统镜像和有权使用的 Metal 编译器。
完整设备身份、系统文件、SDK、固件及测试日志均留在 `local/`，不提交。

```sh
AIR=1 tools/mk-mesa.sh
tools/lemonade-build-metal-shaders.sh
tools/lemonade-build-metal.sh
python3 tools/lemonade-build-graphics-image.py
tools/lemonade-build-kernel.sh
python3 tools/test-lemonade-usb.py
python3 tools/lemonade-usb.py run --profile performance --metal-test --display sync --metrics --seconds 100
```

系统发现镜像由 `lemonade-build-graphics-image.py --system-metal` 单独打包，添加 Metal bundle、
正常启动前的设备发现属性和独立验收程序；普通 root 和 System 镜像保持原值。
每次启动核对镜像、任务列表、构建记录和源码哈希；源代码变化后必须重新构建。
逐提交 GPU 日志改为显式 `--gpu-trace`。普通性能测试保留帧与输入统计，避免 Metal 的大量
小提交耗尽设备日志缓冲而掩盖较早的验收结果。

五分钟复现使用 `common/devices/lemonade-metal-soak.rc.jobs` 临时替代系统测试 jobs，
重新构建和归档系统镜像，运行 300 秒。结束后恢复普通 `lemonade-metal-system.rc.jobs`
并重新构建；专用归档中保留该轮实际 jobs，避免把两种工作负载误认成相同测试。

## 尚未达到的边界

系统所有应用的 Metal 兼容、深度 MSAA resolve、完整 texture view、heaps/events 和全部私有 SPI
尚未全面验收。DPU SMMU 映射、双/三缓冲直接扫描、生产者 release fence、硬件缩放、
Wi-Fi 与 GPU 联合运行、120 Hz 模式及长时间温控也不能据本页结果宣称完成。
当前安全窗口内复制仍有约 3 ms 成本，动画绘制也有长帧；GPU 可用并不意味着所有刷新瓶颈消失。

系统 Metal 的多轮 85–90 秒运行在同步保护后未复现先前的 `fd6_vsc_update_sizes` 空 batch 崩溃。
MPS Gaussian blur 的常量和脉冲图像均已通过；脉冲红通道能量 234，与 Apple M4 对照一致。
`1792787251` 的 90 秒运行记录 5,350 次 TE、721 次呈现，越窗/丢弃/异常中断均为零。
此结果不替代长期稳定性验收。

本地汇总在 `local/lemonade/graphics-20261024/progress-evidence.json`，性能统计在
`hardware-vsync-performance.json`。使用 `lemonade-archive-graphics.py --system-metal LABEL`
核验并保存该轮镜像、构建记录、源码和二进制快照。历史六个错误归档已隔离并在
`archive-audit.json` 中标明缺少当时的系统镜像，不能作为精确二进制回滚点。

半精度 varying 的存储宽度修复后，`1792784740-screen.png` 的实际 Metal 桌面已恢复黑色背景、
文字和正常图标透明度，大块青色色块消失。该修复仅在着色器阶段边界扩展为 32 位，内部
继续按原始半精度计算。RGBA8、BGRA8、RGBA16Float、R8 的 float/half 采样与原生 Apple
Metal 对照全部一致；本轮 90 秒记录 5,351 次 TE、634 次呈现，越窗/丢弃/异常中断均为零。
后续 Stencil8 的存储和采样、语义链接以及共享图像直接读写已通过。
`1792785706-screen.png` 当时仍有圆角杂点。随后用公共 CARenderer 的四种圆角场景复现，
定位为 AIR 缺少 quad helper invocation 标志；半精度导数另需先转换为 32 位再调用 Adreno 指令。
两项修复后 `1792787267-screen.png` 的主屏幕圆角裂缝已消失，四种 CA 图像也逐像素通过。
此视觉验收仅覆盖该桌面画面及测试场景，不能替代所有应用或面板扫描的检查。

本阶段结束后已返回原 Android（`android-roundtrip-20261024T045545.json`）：
精确 USB 身份、启动完成、原槽与锁状态相符；boot/vendor_boot/dtbo/vbmeta/xbl
五个活动分区哈希均与原备份一致。root 可用，默认外网路由经 wlan0，三次受控 ping 成功。
