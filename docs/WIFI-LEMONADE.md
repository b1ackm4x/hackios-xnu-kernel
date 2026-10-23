# OnePlus 9 Wi-Fi 移植记录

截至 2026-10-23，Wi-Fi 尚不能扫描、连接或联网。当前已完成 PCIe 上电/链路、BDF→SID、独立 SMMU 地址空间、真实越界 DMA 拦截、BHI/SBL、MSI/MHI M0、BHIE 全镜像启动，以及 IPCR 20/21 双向收发和 WLFW 服务发现。QMI 客户端登记、主机能力、固件内存握手和能力查询均已通过；射频初始化与网络收发尚未实现。首次整段上电的历史故障及后续修正见下文。

major 1 的源码核对结果和具体实施顺序见 [QCA6490 major 1 适配方案](WIFI-QCA6490-PLAN.md)。目前有明确的原厂参考和部分寄存器/描述符复用证据，目前已有首段固件进入 SBL 的本机证据，尚无完整运行固件的握手或联网结果。

开发分支：`codex/lemonade-wifi`。`device/lemonade` 保留此前 GPU、实体触控和研究文档的基线。本轮不修改手机分区、启动槽位、Android Wi-Fi 配置或设备校准数据。

## 已确认的硬件

同一台 LE2110 / SM8350 的 USB 身份通过私有基线哈希匹配。Android 的实时 PCI/sysfs 信息与设备树相互核对：

| 项目 | 实机结果 |
| --- | --- |
| PCIe 根控制器 | `0000:00:00.0`，`17cb:010c`，`pci-msm-rc` |
| Wi-Fi 端点 | `0000:01:00.0`，`17cb:1103`，revision 1，subsystem `17cb:0108`，`cnss_pci` |
| 芯片修订 | Android CNSS QCA6490；XNU 两次读取 `TCSR_SOC_HW_VERSION=0x400c0110`，major 1 / minor `0x10`，与现有 ath11k WCN6855 major 2 支持范围不同 |
| BAR0 | `0x60400000..0x605fffff`，2 MiB |
| 传输 | PCIe / MHI，存在 IPCR、DIAG 等 MHI 节点 |
| 控制器寄存器 | PARF `0x01c00000`，PHY `0x01c06000`，DBI `0x60000000`，iATU `0x60001000`，配置窗口 `0x60100000` |
| 引脚 | WLAN_EN 64；BT_EN 65（只读）；PERST 94；CLKREQ 95；WAKE 96；SW_CTRL 153 |
| 根控制器模拟电源 | PM8350 L6 1200 mV、L5 880 mV，以实际设备树为准，不能按 `vreg-1p8` 属性名误设为 1800 mV |
| SMMU | Apps SMMU `0x15000000`，根控制器 SID `0x1c00`、端点 SID `0x1c01` |

这与上游 redwood 的 WCN6750 / WPSS / AHB 路径不同。新增 `lemonade_wifi.c`，并给 `redwood_wifi.c` 增加平台检查，防止 SM8350 因共享 Qualcomm SoC 基址进入 SM7325 的寄存器初始化。

## 实测边界与失败记录

只读测试内核 SHA-256：`9e966540be1580eb2c22b31bbe8b3383f7be50aadafc8fe955bb60b8c2eebf15`。在 RAM 系统镜像下运行 30 秒后，加载器返回 `returned 213923 0`。探测显示：

- PCIe0 GDSC 为 `0x0022f001`，未上电；collapse vote 为 `0x3`。
- CFG/AUX/AXI/PIPE 时钟均未启用；WLAN_EN、BT_EN、PERST 为低电平。
- 驱动因电源/时钟条件不满足，主动跳过 PARF、DBI 和端点访问。
- 记录包含 `inspection complete; hardware writes=0`，自动返回正常；这仅证明只读探测通过。

不附带 root RAM 镜像的一次测试虽然正常返回，但未执行到 Wi-Fi 初始化；验证器明确拒绝将它算作成功。

随后整段上电试验使用内核 `af742daa486556f2705c9ae9001fe0c5612a88025da946082dbf3558c6439bef`，USB 进入 `05c6:900e`，没有取得最终 XNU 日志，也未能验证自动恢复。不能据此确定异常发生在供电、时钟、复位、寄存器读取还是退出恢复中的哪一步。

源码复核发现旧试验在读取 DBI 前遗漏了 `pci-msm.c` 中的 PARF 配置顺序，并未包含完整的 PCIe 时钟列表。这是需要修正的差异，尚未证明就是本次异常的唯一原因。旧的 `--wifi power` 整段入口已经移除。新的阶段如下：

| 参数 | 操作上限 | 当前证据 |
| --- | --- | --- |
| `inspect` | GCC/GPIO 只读；电源就绪时只读 PARF，不访问 DBI/端点 | 前述只读版本实机通过 |
| `rails` | 请求 L6/L5 原厂电压 | 2026-10-23 19:11 实机通过 |
| `domain` | 继续开启 PCIe0 GDSC | 实机通过 |
| `clocks` | 继续开启 PCIe 路径时钟 | 实机通过 |
| `resets` | 继续执行根控制器/PHY 复位，保持 PERST 为低 | 实机通过 |
| `controller` | 继续设置 PARF 根模式、DBI 地址、时钟复位和地址窗口 | 实机通过 |
| `phy` | 写入板级 75 项 PHY 表，再启用 PIPE 并等待 PHY ready | 实机通过，状态 `0x86`、PIPE `0` |
| `root` | PHY ready 后读取根控制器 ID | 实机通过，`17cb:010c` 与 Android 一致 |
| `radio` | 请求无线电源、保持 BT 不变、等待 PMU 时序后拉高 WLAN_EN | 实机通过，仍保持 PERST 为低 |
| `link` | 释放 PERST，以 Gen1 训练链路 | 实机通过，2.5 GT/s x1，ELBI `0x11401`、link status `0x30110008` |
| `endpoint` | 配置 CFG0 iATU 窗口并读取 `17cb:1103`，要求 Bus Master 关闭 | 实机通过，revision 1、command `0x00100000`（低 16 位为 0） |
| `mmio` | 临时配置 BAR0/桥窗口/MEM iATU，读取版本与 MHI/BHI 状态 | 实机通过，BAR 和内存解码已恢复，未读取序列号或密钥哈希寄存器 |
| `smmu` | 只读 Apps SMMU 全局寄存器与端点匹配项 | 实机通过：89 个匹配项中 8 个在用，0 个覆盖端点；没有写入 SMMU |
| `sid` | 临时设置 BDF→SID 表，仍关闭 Bus Master | 实机通过：`01:00.0` 的 CRC8 索引 `0x15`，表值 `0x01000100`；退出恢复 |
| `dma` | 独占 context、映射 512 KiB，软件遍历页表及未映射边界 | 实机通过：128 个 4 KiB 页；不声称软件遍历等于实际 DMA |
| `dma-fault` | 让 PBL 读取未映射的 4 KiB，要求硬件故障地址与 SID 一致 | 两次实机通过：`FAR=0xa0084000`、`SID=0x1c01`、`FSR=0x80000402`；无全局故障 |
| `bhi` | 用单独的 RAM 镜像传送原机固件前 512 KiB | 连续两次实测进入 SBL；同一内核的 `dma-fault` 验收是主机工具的强制前置条件 |

每级都停止在指定边界。`inspect` 至 `dma` 不使能端点 DMA；`dma-fault` 仅发出预期被拒绝的读取，`bhi` 仅启动首段固件到 SBL。`radio` 及后续阶段允许无线芯片上电，尚未建立无线网络或启动射频业务。GCC 仅恢复本阶段涉及的位，PARF 在关闭时钟前恢复；PHY 先停止再关闭时钟。后续阶段还需撤销 iATU、停止链路、恢复 DBI 控制位以及 WLAN_EN/CLKREQ/PERST。退出加载器也调用清理入口。RPMh 电源请求保留至重启，尚未实现完整 regulator 所有权管理。主机工具与内核均拒绝将上电探测与触控、GPU 或 CPU 调频实验同时运行，避免共享资源并发修改。

19:20 前完成的七个独立阶段均为 25 秒 RAM 测试，正常返回加载器。根控制器验证版本为 `662a136520d5eaffe8ca14a6ee08e3275ad94672effc9a977d3fa3ef15666c16`。复核修正了 PIPE mux 地址（`0x6b054`，不是 `0x6b070`），补上 PHY 时钟请求，使用板级 PHY revision 21101 的表，并把 PHY ready 设为 DBI 访问的前置条件。PHY、DBI 和退出恢复均已取得实际日志；这组修正消除了本次重复测试中的早期异常，但没有通过逐项反向试验确定旧异常的唯一原因。

到 19:30 完成 `rails` 至 `smmu` 的 12 次独立阶段测试，全部正常返回。最后两个阶段使用内核 `9f03b3215437da1b6467572216905e66cf19a48491bea5bff74444b358fbd8cf`，覆盖前面全部初始化步骤。阶段间可比较的 20 个公共 GCC 寄存器初始读数保持一致。完整私有证据与日志哈希在 `local/lemonade/wifi/resume-20261023T1908/stages.json`。

19:32 恢复原 Android 后，精确 USB 身份、启动完成、B 槽和原有五个启动分区哈希均通过校验。Android 的 `cnss_pci`、5 GT/s x1 链路和 `wlan0` 恢复，向实际 Wi-Fi 网关发出两次探测均收到响应。电池 33°C。验证工具同时修复了 root 哈希输出为空时的异常：明确引用完整远端命令、有限重试，并拒绝没有有效哈希的成功记录。

## 新确认的兼容性边界

PCI ID `17cb:1103` 相同不代表芯片修订相同。项目 `tools/mk-ath11k.sh` 固定的是 Linux 7.0.14，其 `pci.c` 的 WCN6855 分支只接受 major 2；major 1 会返回 `-EOPNOTSUPP`。本轮两次实测 major 1 / minor `0x10`，不能仅修改判断条件就宣称兼容。后续还需要核对 QMI、内存请求、CE/DP 寄存器和固件服务差异。

OnePlus 原厂 `cnss_pci_update_fw_name()` 对 QCA6490 major 2 选择 `amss20.bin`，其他修订选择 `amss.bin`。已按此规则从同一台原 Android 读取 `amss.bin`（5,178,848 字节）和 `m3.bin`（266,684 字节），与手机端哈希匹配；只保存在被 Git 忽略的 `local/lemonade/wifi/firmware/`。本轮把 `amss.bin` 加入独立的私有 RAM 镜像，前 512 KiB 已通过 BHI 传输；`m3.bin` 尚未发送给固件。23:05 又只读取得原机板级 ELF（59,908 字节）和原厂日志确认使用的监管数据库（19,348 字节），设备端 SHA-256 与主机一致；它们与 M3 已加入新的私有测试镜像，正常镜像未改变。

MHI/BHI 的可读性已验证：BHI offset `0x100`，BHIE offset `0x224`，execution environment `0`（PBL），状态 `0`；PBL 阶段 MHIVER 读数为 `0xdeadbeef`，不作为运行固件已经就绪的证据。19:30 之前的测试保持 endpoint Bus Master 关闭。后续受限 DMA/BHI 结果见下节；后续 MSI/MHI/IPCR 结果见下节；CPU 中断处理、扫描、关联或 IP 网络仍未验证。

已完成 **BDF → SID → SMMU → 受限 DMA 缓冲区 → BHI/SBL**。原厂 `msm_pcie_config_sid()` 使用的 PARF CRC8 索引表已在独立阶段设置和恢复；实际端点故障确认请求确实携带 `0x1c01`。现已进一步验证 MHI context/event ring、READY→M0 和完整固件 BHIE；下一边界为 WLFW 内存/板级数据握手和网络驱动接入。现有 redwood 驱动的 SMMU/缓存维护代码可作参考，但它的 WPSS/AHB 启动和固定 WCN6750 配置不能直接调用。

## 21:00 后的 DMA 与 BHI 实测

测试内核 SHA-256：`d8405ea2c64a169614bd5b86e78c8919ee1b456dc0c2dc117feb6c99ce89e02e`。完整日志、输入/输出哈希、早期失败记录和已验证内核副本保存在私有目录 `local/lemonade/wifi/dma-20261023/`。

- 独占 SMR 8 / context 7，仅匹配端点 SID `0x1c01`，没有 identity mapping 或 MSI 寄存器页。DMA 区间 `0xa0004000..0xa0083fff`，512 KiB；16 KiB XNU 页按实际物理地址拆成 4 KiB SMMU 页。
- 两次真实越界请求均在预定地址被拦截，报告 `FSR=0x80000402`、`FAR=0xa0084000`、`SID=0x1c01`、全局状态 0。请求只读未映射页，不提供固件内容。
- 连续两次正常 BHI 传输使用原机 `amss.bin` 前 524,288 字节。全文件 SHA-256 在主机核验，RAM 镜像解析和 DMA 拷贝后再核验首段 CRC32；芯片返回 `BHI=0x80000001`、`EE=1`（SBL）、`MHISTATUS=0x101`、`FSR=0x400`、全局状态 0、错误码 0。`0x400` 是翻译格式位，不是故障。
- 退出时读回 Bus Master 已清除，端点复位后再撤销 SMMU 映射、同步 TLB、恢复翻译寄存器并释放缓冲区；之后恢复 SID 表、PCIe/GPIO/时钟。RPMh 电源请求仍保留至重启。

本轮最初三次 `dma` 预检没有通过，均正常返回加载器，没有传送固件。修正点有：高通虚拟化层需要明确的寄存器读写指令；`IDR0.ATOSNS=1` 表示标准 ATOS **不支持**；FSR 的格式位 `[10:9]` 不属于故障掩码。软件页表检查与真实 DMA 拦截分开验收，不把 PAR 为零当作成功。

虚拟化层可能不允许闲置 context 的 CBAR 从 stage-1 类型恢复到 reset 的 stage-2 类型；这不是完整逐位恢复。释放验收要求 SMR/S2CR、SCTLR、TTBR/TCR/MAIR 等翻译寄存器恢复，context 禁用且没有流指向它，类型位可能保留到 Android 重启。原厂驱动的 context 销毁路径也禁用 SCTLR 而不复位 CBAR。

`tools/lemonade-build-wifi-image.py` 从已校验的正常 APFS 镜像提取文件，独立重建并逐文件比较内容、权限和符号链接；仅新增 `fw/amss.bin`。正常 `userland/root.apfs`、`system.apfs` 均保持原哈希。固件与生成镜像被 Git 忽略，不进入提交。

21:47 最终恢复 Android 后，USB 身份、启动完成、B 槽、五个启动分区哈希均通过核验。`cnss_pci` 恢复 5 GT/s x1，`wlan0` 为 up，向当前 Wi-Fi 网关的三次探测均收到响应；电池 32.9°C。

构建工具改为完整读取、原子替换源文件，并在链接后核验源文件与实际编译副本一致，以避免共享目录中旧文件长度造成的截断。失败的构建不会替换已核验内核。

## 22:30 后的 MHI / BHIE / IPCR 实测

新增测试模式为 `mhi`（18）、`bhie`（19）、`ipcr`（20）和 `qmi`（21）。后者仍要求成功能力响应才算通过，不把收到错误响应作为成功。

- 新增 8 MiB 独立 DMA 区，逐页核对 2048 个映射，保留区外 guard；MHI context 为原厂协议要求的 44 字节，TRE/向量为 16 字节，共享控制页使用无缓存别名。
- 初次只配置事件环、不配置 MSI 时，固件已写入 M0 事件，但 context 中的 read pointer 被异常内容覆盖，测试拒绝通过。端点能力实测为 **32 位 MSI、支持逐向量屏蔽**；配置四个 MSI 消息到自有 DMA mailbox 后，相同事件环得到有效指针与 M0/SBL 事件。该对比确定了初始化缺项，不声称已经确定芯片内部异常成因。
- 内核 `53d75def30b04603e92aafcb9a228f4ba804c79f00972cd8434bf98d740192e2` 在日志 `1792766239` 通过 MHI：`MHISTATUS=0x201`，设备写入 M0 事件，MSI mailbox 为 `0x101`，没有 SMMU fault。这里通过轮询处理事件，没有安装 CPU IRQ handler。
- 同一内核日志 `1792766347` 将原机 `amss.bin` 全部 5,178,848 字节分为 10 个 BHIE 向量；`BHIE=0x80000002`、`EE=2`，并收到设备产生的 mission EE 事件。主机核验完整 SHA-256，内核核验完整 CRC32，固件没有修改。
- 内核 `af96a3a230762f75faa28cac287a9ef8b84fd1ecd7fa6e8d6358467ddd32b18b` 的日志 `1792766944` 中，IPCR 20/21 的 START 命令均返回 completion 1；发送与完成计数均为 2、接收 2，收到 HELLO 和 service `0x45` / version 1 / instance 1 公告。MSI 原配置、Bus Master、BAR、SMMU 和 SID 清理均有读回证据，正常返回加载器。
- 同内核日志 `1792767031` 的首次能力查询得到匹配的 QMI response，但结果为 `result=1, error=90`，阶段 21 **失败**。原厂流程先登记客户端、声明主机能力并处理固件内存请求；后续修复按此顺序推进。

内核 `c8721e264033227ba40cd7db3fd1fade5878eacaa9fac70edf470bdf4ff15809` 的日志 `1792767713` 已修复 QMI 顺序：登记（`0x20`）、主机能力（`0x34`）均返回成功；固件申请 type 1 / 5,111,808 字节和 type 4 / 3,735,552 字节，独立分配后核对 1248 / 912 页及两侧 guard，内存响应（`0x36`）成功，收到 memory-ready（`0x37`）后能力查询（`0x24`）返回 `result=0, error=0`。最终 TX 完成 7、RX 8，SMMU `FSR=0x400`、全局状态 0，阶段 21 通过并正常退回加载器。

以上都不是射频 ready、扫描、关联或获取 IP 的证据。原始日志、镜像和失败尝试仅在忽略目录 `local/lemonade/wifi/mhi-20261023/` 下保存。23:03 已恢复同一 USB 目标的原 Android，五个活动启动分区哈希与备份全部一致；随后 `wlan0` 实际 IP 连通测试 3/3 成功。记录为 `android-roundtrip-20261023T230331.json`。

## 验证与后续顺序

先恢复原 Android，核对启动状态与既有五个启动分区备份哈希，再继续临时 RAM 测试。高通故障模式没有 ADB/Fastboot 通道时，需要实体键重启，不使用刷机或清除数据作为恢复手段。

只读探测的复现入口如下；应先持有精确目标的 Device Hub 租约。Fastboot 在 macOS 上需要能访问 USB 的执行环境。

```sh
python3 tools/lemonade-wifi-collect.py
python3 tools/lemonade-verify-android.py --timeout 30
python3 tools/lemonade-usb.py boot
python3 tools/lemonade-usb.py ramdisk
python3 tools/lemonade-usb.py run --kernel built --boot-cpu 7 --cpus 1 --userland --wifi inspect --seconds 30
python3 tools/lemonade-check-wifi.py local/lemonade/<本次时间戳>-run-cp0.log
```

复现 BHI 首段测试时，先用同一个已构建内核运行并记录真实 DMA guard 验收，再生成私有固件镜像：

```sh
python3 tools/lemonade-usb.py run --kernel built --boot-cpu 7 --cpus 1 --userland --wifi dma-fault --seconds 25
python3 tools/lemonade-check-wifi.py local/lemonade/<本次时间戳>-run-cp0.log --record-guard
python3 tools/lemonade-build-wifi-image.py
python3 tools/lemonade-usb.py run --kernel built --boot-cpu 7 --cpus 1 --userland --wifi bhi --seconds 30
python3 tools/lemonade-check-wifi.py local/lemonade/<本次时间戳>-run-cp0.log
python3 tools/lemonade-usb.py reboot
python3 tools/lemonade-verify-android.py
```

内核变化后，旧 guard 记录不再满足前置条件。`bhi` 使用单独的 `out/lemonade-wifi/root.apfs`；正常镜像仍保留原哈希。

验证器检查日志哈希、模式、返回状态、阶段完成标记和恢复标记；即使全部通过，也固定输出 `networking_verified: false`。主机测试包含旧内核能力拒绝、错误目标拒绝、阶段参数和并发资源限制等场景。

最新主机验证包含 32 项 USB/固件前置条件、15 项阶段证据与故障掩码反例，以及独立 C 协议布局/环指针测试，全部通过。此前 3 项 Android 校验边界测试也已通过。另验证了首段 CRC32 标准向量及 APFS 内容/权限/符号链接读取。它们不替代上述实机日志，也不证明网络协议栈可用。

后续按依赖推进：

1. 已完成电源/时钟/复位/PARF/PHY/DBI 与退出恢复；每级保留日志和内核哈希。
2. 已训练链路、读取 `17cb:1103` 端点及内部硬件版本；固件阶段仅在受限 SMMU 已就绪后开启 Bus Master，退出前确认清零。
3. 已验证 PARF BDF-to-SID、SMMU DMA 隔离、BHI/SBL、MHI M0、BHIE 全镜像及 IPCR/WLFW；继续补齐 QMI 初始化、内存请求和板级文件传输。
4. 补充 QCA6490 major 1 的兼容性证据和 ath11k 参数，为已有兼容层补 PCIe/MHI 与相应芯片数据路径，再接入扫描、关联和 en0；不将上游 AHB 总线假设继续套用。
5. 使用用户指定网络验证 DHCP、DNS、实际 HTTP 传输、重连及退出恢复。只有这些结果完成后，才能报告 Wi-Fi 可用。

固件内容、板级校准、MAC、SSID、密码及原始设备树只留在被 Git 忽略的私有目录。本轮采集器不读取网络凭据，也不输出网络标识。

## 可复核的公开来源

- SMMU 的厂商依据：[寄存器代理限制、ATOS 能力判断与 context 销毁](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/iommu/arm-smmu.c)。
- OnePlus 官方内核固定版本：[PCIe 控制器](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/pci/controller/pci-msm.c)、[Lahaina PCIe 设备树](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/arch/arm64/boot/dts/vendor/qcom/lahaina-pcie.dtsi)、[CNSS 电源时序](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/net/wireless/cnss2/power.c)、[GDSC](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/clk/qcom/gdsc-regulator.c)。
- Linux v6.12：[ath11k PCIe](https://github.com/torvalds/linux/blob/v6.12/drivers/net/wireless/ath/ath11k/pci.c)、[MHI host](https://github.com/torvalds/linux/tree/v6.12/drivers/bus/mhi/host)。这些提供实现参考，不等于现成的 XNU 驱动。
- 实际固定驱动版本：[Linux 7.0.14 ath11k PCIe 的硬件版本选择](https://kernel.googlesource.com/pub/scm/linux/kernel/git/stable/linux/+/refs/tags/v7.0.14/drivers/net/wireless/ath/ath11k/pci.c)；[MHI 寄存器定义](https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/refs/tags/v6.12/drivers/bus/mhi/common.h)。
- [OnePlus 官方强制重启说明](https://service.oneplus.com/in/search/search-detail?id=2235001)。
