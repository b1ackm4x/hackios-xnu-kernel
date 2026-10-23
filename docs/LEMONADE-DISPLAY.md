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
| 编码阶段资源持有修复 | `1792797825-run-cp0.log` | 系统 Metal 93 项特性 + 2 项 MPS + 4 项 CA 全部通过，默认 profile 1 的 90 秒桌面运行正常返回 |
| 资源持有修复的进程内回归 | `1792797958-run-cp0.log` | FD660 GLES 回读、26 项 IOSurface、15 项光栅和 93 项特性全部通过；100 秒正常返回，5,957 次 TE、1,339 次呈现，越窗/丢弃/异常中断为零 |

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

## DPU 映射和独立双缓冲

`--display smmu` 的只读实机记录 `1792789501-run-cp0.log` 确认，Apps SMMU 的
SMR 6 为 `SID 0x820 / mask 0x402`，四个非安全显示流进入 context 5。
固件把这个 AArch64 context 的地址翻译关闭，直接读取 `0xe4d00000` 的 GOP；
`SCTLR=0xe0`，两个 TTBR 和 TCR 均为零。原始 FSR `0x400` 是只读格式位，
不能把它误报为地址访问错误。错误检查使用 `0xc00001fe` 掩码。

`--display map` 在严格匹配上述固件状态时，临时为原 GOP 建立只读恒等映射，
保留未映射的外侧保护页。它不改 SMR、S2CR、CBAR、CBA2R、源地址、面板或缩放。
先取得原画面的两路 mixer MISR，再打开地址翻译，跨 64 次刷新检查 32 份输出签名。
首版退出检查发现 context 恢复顺序和 MISR 状态处理不正确，未作为合格版本保留。
修复后先关闭翻译并同步 TLB、恢复配置，最后恢复原 SCTLR；MISR 的可写控制位恢复，
本轮产生的只读结果就绪状态通过 clear 位清除。不能把读写属性不同的位逐位混比。

`--display flip` 另外分配两份独立的 1080×2400 BGRA 缓冲，各 2,532 个 4 KiB 页，
全部物理页面位于 4 GiB 以上；映射到 `0x10000000` 与 `0x12000000` 的 32 位 IOVA。
在已测得的空闲窗口，只改 VIG0/1 的源地址并提交 CTL0 两个对应的 flush 位。
每个测试画面保持两次刷新，以两路实际 mixer 签名校验切换；这不是帧率基准。
退出时先恢复 GOP 源地址，并确认两次完整传输和原图签名，再撤销地址翻译。
两份测试缓冲在整个受控运行期间保持分配，不会在 DPU 使用时释放。

| 实机记录 | 结果 |
| --- | --- |
| `1792790108-run-cp0.log` | 64/64 次切换、128 次刷新，两种画面签名分别为 `0x516eb0cb`、`0x923b1009`，左右 mixer 均一致 |
| `1792790222-run-cp0.log` | 1,000/1,000 次切换、2,000 次刷新，逐次签名一致；SMMU fault、underrun、异常中断均为零 |
| `1792797713-run-cp0.log` | 退出竞争修复候选完成 64/64 次切换、128 次刷新；所有输出、错误状态与恢复检查通过 |
| `1792798195-run-cp0.log` | 增加清理前故障快照后完成 1,000/1,000 次切换、2,000 次刷新；输出、错误状态及恢复全部通过 |

两轮均确认原画面签名和地址、九项 context 配置、MISR 控制位、TE/GIC 设置及两项总线
时钟请求恢复。32 位显示地址能够访问 4 GiB 以上的实际图像，且两种输出签名随切换改变，
这是 DPU 读取新映射的证据；不仅是寄存器写入读回。
`lemonade-check-display.py` 核验日志哈希、请求次数、实际输出、错误状态与恢复顺序，
并拒绝缺项、矛盾结果或被修改的日志。主机检查另覆盖验收证据的失效场景。

随后故意在双缓冲循环中途以 3 秒定时返回中断运行，设备进入 Qualcomm `05c6:900e`
调试模式，未取得此次 XNU 日志。因此异常退出未通过，尚不能确定出错位置；
用户物理重启后，`android-roundtrip-20261024T071819.json` 已确认 Android 正常启动、
精确 USB 身份、原槽和锁状态一致，五个活动分区哈希与备份一致；root 和网关连通性正常。
前述 1,000 次正常切换和退出结果仍保留，
不能把它外推为任意时刻中断都可安全恢复。此实验路径暂不用于日常默认启动。

静态检查发现退出线程与同 CPU 上的探测线程可能交错执行。新候选在退出开始时设置
单向停止标志，把时钟、地址和 SMMU 配置写入及对应恢复记录放入屏蔽本地中断的事务；
恢复期间保持中断关闭，随后拒绝所有恢复执行的探测读写。6 个主机场景实际编译该 C 驱动，
在重新打开中断时注入退出回调，并用不可访问内存检查停止后的寄存器访问；
AddressSanitizer / UndefinedBehaviorSanitizer 均通过。这只验证 CPU 执行顺序，
尚不能证明本次 `900e` 的根因或真机异常退出已经修好。新候选
`c6d9208ac7832c12ee55a8eaa7ef22adb6be979d01ef3619c96c89d43d7be49f`
已完成上述正常 64 次切换和完整恢复，且 `1792797984-run-cp0.log` 的 3 秒中断退出成功返回。
随后添加清理前故障快照，避免恢复动作清除 fault 后掩盖错误；新内核
`2c0e23f3faa7cb46b88ef3ed72261a7cd4c50508e83e26ad2393a59e32e05b33`
在 3 秒（`1792798085`）和 2 秒（`1792798104`）两个中断点均正常返回。
日志确认退出时映射仍开启、两路源均指向测试 IOVA，清理前 fault/global/underrun 为零；
随后恢复原画面，再恢复九项映射配置、TLB、MISR、TE/GIC 和时钟。
`lemonade-check-display.py --interrupted` 专门检查这一顺序，并拒绝已经正常完成、
未实际扫描测试缓冲、缺少清理前状态或恢复前出现错误的记录。中断时未记录完整切换计数，
报告使用 null，避免误写为零。两次中断后同一内核又通过上述 1,000 次正常切换。
这些证据覆盖已测中断点，不证明所有时序均安全，也不能单独确定旧版 `900e` 的唯一根因。

### 命令模式逐帧传输

`--display manual` 在上述独立缓冲路径中接管自动刷新：先断开外部 TE，关闭 autorefresh，
等待当前传输完成，再接回 TE。实测空闲 50 ms 内帧计数不变且 PP0 完成位为零；
每次只提交两路源地址、flush 和一次 CTL_START，等待主 PINGPONG0 完成、传输行计数归于
空闲且帧计数恰好增加 1，然后校验两路 mixer 签名。INTF1 的 INTF_MUX 低 4 位选择 PP0；
固件保留的高位 `0xf0000` 不属于通道编号，两路 DSC 也不等于两个独立完成中断。

首轮实验误等两个 PP 完成位，并在恢复 autorefresh 后额外发 CTL_START，未能确认原图恢复。
该候选已单独留档为失败版本，返回 Android 后五个活动分区哈希仍与原始备份一致。
参照 OnePlus 原厂命令 encoder 修正后，恢复流程先选择 GOP，再断开 TE、启用原 autorefresh、
接回 TE，期间不发送 CTL_START。确认两次实际传输和原图签名后才撤销映射。
第二个候选因误把 INTF_MUX 整个寄存器当作通道编号而在接管前被拒绝；原图和状态正常恢复。

内核 `28d66d67331ccdfc1a3ed8d61329d33b0bdd20a42d13310d527a78ae6b2a9b92` 的实机证据：

| 实机记录 | 结果 |
| --- | --- |
| `1792799104-run-cp0.log` | 64 次命令、64 帧，两种画面逐次签名正确；原图、映射、中断和时钟完整恢复 |
| `1792799138-run-cp0.log` | 2 秒定时中断时仍扫描测试 IOVA，清理前无 fault/underrun，原图先于撤销映射恢复 |
| `1792799231-run-cp0.log` | 1,000 次命令、1,000 帧，逐次签名正确，无超时、fault、underrun 或异常中断，完整恢复 |

检查器同时验证无自动传输、单命令单帧、主 PP 完成和恢复顺序；14 项证据失效测试通过。
实际 C 驱动的 9 组主机检查覆盖中断恢复、真实 INTF_MUX 高位以及拒绝错误 PP，
AddressSanitizer / UndefinedBehaviorSanitizer 通过；USB 工具的 41 项检查通过。
这仍是明确启用的独立缓冲实验。它建立了逐帧完成依据，尚未接入桌面生产者的缓冲区复用同步。

时序依据：[OnePlus SM8350 命令 encoder](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/oneplus/SM8350_R_11.0/techpack/display/msm/sde/sde_encoder_phys_cmd.c)、
[INTF 和通道选择](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/oneplus/SM8350_R_11.0/techpack/display/msm/sde/sde_hw_intf.c)。

桌面回归另发现测试工具省略 `--metal-profile` 时选择 Apple1 表，导致 MPS 拒绝初始化，
SpringBoard 反复 abort（`1792790381-run-cp0.log`）。工具已改为默认选择此前实测通过的
profile 1，并在运行记录中写入实际值；仍可显式选择 profile 0 做对照。
`1792797825-run-cp0.log` 已验证省略该参数时实际采用 profile 1，93 项 Metal、2 项 MPS
和 4 项 CoreAnimation 检查全部通过，90 秒桌面运行正常返回；这不代表已全面满足
Apple2 家族的全部能力。

这仍是独立显示缓冲测试，尚未把桌面 IOSurface 直接送屏，也没有完成生产者 release fence、
1125×2436 到 1080×2400 的硬件缩放或多图层合成。MISR 是 DPU 内部输出检查，
不等于实际面板的光学测量。默认桌面继续使用此前验证的同步 CPU 复制路径。

```sh
python3 tools/lemonade-usb.py run --kernel built --userland --display smmu --seconds 30
python3 tools/lemonade-usb.py run --kernel built --userland --display flip --display-flips 1000 --seconds 60
python3 tools/lemonade-usb.py run --kernel built --userland --display manual --display-flips 1000 --seconds 60
python3 tools/lemonade-check-display.py local/lemonade/RUN-run-cp0.log
python3 tools/lemonade-check-display.py local/lemonade/INTERRUPTED-run-cp0.log --interrupted
```

寄存器依据：[Linux v6.12 SM8350 设备树](https://github.com/torvalds/linux/blob/v6.12/arch/arm64/boot/dts/qcom/sm8350.dtsi)、
[ARM SMMU](https://github.com/torvalds/linux/blob/v6.12/drivers/iommu/arm/arm-smmu/arm-smmu.h)、
[MISR 控制](https://github.com/torvalds/linux/blob/v6.12/drivers/gpu/drm/msm/disp/dpu1/dpu_hw_util.c)。

### 编码阶段资源生命周期

后续主机检查发现，原先仅在 draw/dispatch 记录入队时保留资源，编码器的当前绑定、
未执行 clear 的颜色/深度附件此前都是弱引用。应用在绑定后、首次绘制前释放对象，
或者清空创建附件的 descriptor，就会留下悬空引用。原代码在新增的实际编码器方法
测试中于首次绘制之前触发释放断言，证据为 `metal-ownership-before.log`。

候选现在让编码器持有当前 pipeline、buffer、texture、sampler、深度状态和附件；
先保留新绑定再释放旧绑定，同一对象重复绑定安全。每条已记录的工作继续持有独立快照，
编码结束即释放编码器的引用，未用过的绑定和替换掉的资源也能释放；`set*Bytes` 不依赖
调用者的 autorelease pool。编码器另外持有 device，结束编码后该公开属性仍有效，
不再经已释放的 command buffer 访问。这与 [Metal 默认保留命令所需资源](https://developer.apple.com/documentation/metal/mtlcommandbufferdescriptor/retainedreferences)
的契约一致。

新增五项提前释放/清空 descriptor/pool drain 的像素与计算结果测试，Mac 原生 Metal
全部通过，整套达到 93 项（`native-metal-lifetime.log`）。另有五组主机测试编译实际
编码器方法、ivar 和工作对象持有逻辑，以析构计数检查提前释放、重绑、结束编码与最终释放，
AddressSanitizer / UndefinedBehaviorSanitizer 均通过（`metal-ownership-host-tests.log`）。
这些主机替身不模拟 GPU 或 iOS 私有父类。iOS 前端和两种独立测试镜像已构建、回读验证，
原 root/System 未改动。重启后的 `1792797825-run-cp0.log` 已在 Adreno 660 上通过全部
93 项特性检查，包括新增五项提前释放测试；MPS 2 项和 CA 4 项也全部通过。

同轮省略 `--metal-profile`，确认实际默认值为 1。90 秒、11 个固定输入动作记录
1,278 次呈现和 5,352 次 TE，越窗、丢弃和异常中断均为零；退出恢复显示中断与总线时钟。
手指按下期间 55.56 FPS，全部活动窗口 52.09 FPS，间隔 P95 33.060 ms、最大 215.363 ms；
CPU 复制平均 3.165 ms。GPU 两路温度均为 47.6°C。长帧仍保留，不能据平均值称为满帧。
`1792797836-screen.png` 显示正常文字、图标颜色和圆角；天气和日历小组件仍为空白占位。
验收汇总是 `graphics-20261024/metal-lifetime-device-evidence.json`，性能记录是
`graphics-20261024/metal-lifetime-device-performance.json`。

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
python3 tools/test-lemonade-display-stop.py
python3 tools/test-lemonade-check-display.py
python3 tools/test-lemonade-metal-lifetime.py
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
尚未全面验收。独立双缓冲的 DPU SMMU 读取已通过上述有界测试；桌面直接扫描、生产者 release fence、硬件缩放、
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

前一 Metal 阶段结束后已返回原 Android（`android-roundtrip-20261024T045545.json`）：
精确 USB 身份、启动完成、原槽与锁状态相符；boot/vendor_boot/dtbo/vbmeta/xbl
五个活动分区哈希均与原备份一致。root 可用，默认外网路由经 wlan0，三次受控 ping 成功。
该记录早于后续 DPU 中断退出失败；不能用它声称失败后已经恢复 Android。
