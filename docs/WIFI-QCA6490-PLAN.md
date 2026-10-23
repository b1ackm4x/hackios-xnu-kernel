# OnePlus 9：QCA6490 major 1 的适配方案

调查时间：2026-10-23。对象是本机实测 `17cb:1103`、`TCSR_SOC_HW_VERSION=0x400c0110`，即 major 1 / minor `0x10`。本文记录适配依据和验收计划；后续实现已通过 DMA、完整固件启动和 IPCR/WLFW 发现，实测状态以 [Wi-Fi 记录](WIFI-LEMONADE.md) 为准，尚未联网。

结论：优先保留已经通过测试的 PCIe 底层，按原厂流程启动本机固件，再为 ath11k 增加独立的 QCA6490 major 1 配置及 PCIe/MHI 适配。已核对的硬件定义有较大重合，支持这条路线；不能把版本伪装成 major 2，也不能把“支持 QCA6490”的其他设备案例视为本机兼容证据。

## 查到了什么

### 版本判断和固件选择都有源码依据

- 项目固定的 Linux 7.0.14 `ath11k/pci.c` 对 `17cb:1103` 只接受 major 2。官方支持列表也列出 WCN6855 hw2.0 / hw2.1；本轮没有找到可直接用于本机 major 1 的已验证上游方案。[版本选择][linux-pci]、[官方列表][wireless]
- OnePlus MHI 从 BAR `0x224` 提取版本；vendor major mask 为 `0xff00`，ath11k 为 `0x0f00`，对本机 `0x400c0110` 都得到 major 1。原厂 `cnss_pci_update_fw_name()` 对 QCA6490 major 2 用 `amss20.bin`，其余版本用 `amss.bin`。因此此前的识别结果和固件选择一致。[版本读取][vendor-init]、[固件选择][vendor-pci]
- 已保存的本机 `amss.bin` 包含构建标识 `WLAN.HSP.1.1-01737-QCAHSPSWPL_V1_V2_SILICONZ-1.499991.13.512711.3`。它已通过真实 BHI/BHIE 状态及 mission EE 事件验证启动；这不代表无线网络 ready。`m3.bin` 是后续 QMI/M3 交换所需材料，不能当作首段 BHI 引导镜像。
- 原厂公开模块中存在专门的 `hal/wifi3.0/qca6490/`、`hif/src/qca6490def.c` 和 `fw-api/hw/qca6490/v1/`。目录名 `v1` 本身不证明 silicon major 1 的全部运行行为；本轮把它作为原厂 QCA6490 定义来逐项比较。[HAL][vendor-hal]、[硬件定义][vendor-def]

### 18 项有界源码比较一致，但不是完整兼容认证

以 OnePlus 模块提交 `1e7b5673e7cc29d9af9fb0f58597055d07de6c6a` 和 Linux 7.0.14 的 WCN6855 定义为对象，脚本检查结果如下：

| 范围 | 比较结果 | 实现上的意义 |
| --- | --- | --- |
| CE0/CE1 的 SRC、DST 地址，共 4 项 | `0x1b80000`、`0x1b81000`、`0x1b82000`、`0x1b83000` 均一致 | 可以从 WCN6855 的寄存器访问适配开始 |
| TCL1、REO1、WBM idle ring 的相对地址，共 3 项 | `0x690`、`0x244`、`0x870` 均一致 | 已核对的关键队列地址可复用 |
| RX attention、MPDU end/start、MSDU end/start 的尺寸，共 5 项 | 12、8、92、76、36 字节均一致 | 现有接收结构是合理候选 |
| MSDU 长度、解封装类型、MCS、first/last、L3 padding 的偏移和掩码，共 6 项 | 均一致 | 已核对的字段无需另写一套解析 |

这些检查只覆盖表中项目；没有覆盖完整 TLV 排列、所有 WMI/HTT 命令、能力位、缓存一致性、实际收发或固件行为。证据保存在忽略目录 `local/lemonade/wifi/compat-research-20261023/layout-audit.json`，包含每项数值和输入文件哈希。[原厂地址][vendor-base]、[ath11k 寄存器/操作][linux-hw]、[ath11k 接收描述符][linux-rx]

同时已经发现必须处理的差异：

| 项目 | 本机/原厂参考 | ath11k WCN6855 当前路径 | 处理方式 |
| --- | --- | --- | --- |
| WLAN CE 数量 | 9；CE9～11 留给 MHI | 9；同样保留 CE9～11 | 保持这个边界，不把 CE9～11 当网络队列使用 |
| CE2 接收缓冲上限 | host 与 target 配置均为 3200 字节 | target CE2 为 2048 字节 | 建独立 CE 配置，并同步核对 host 分配，防止固件消息被截断 |
| IPCR 通道 | 本机 DT 为 20/21，各 32 项，EE mask `0x14` | QCA6390/WCN6855 配置为各 64 项、`0x4` | 初版以本机 DT 为候选，记录真实执行环境后决定收敛范围 |
| MHI 总体参数 | 本机 DT 为 30 个通道、10 秒超时、32768 字节缓冲 | 128 个通道、2 秒配置超时、8192 字节缓冲 | 不直接套用现成控制器配置 |
| 事件环 | 本机 DT 有 3 个：控制 32、数据 256、类型 3 的专用环 32 | 常用 ath11k 配置有 2 个 | 先确认 vendor 类型语义及固件需求；不凭环数量直接断言不兼容 |
| 总线与启动 | 本机 PCIe/MHI | 本项目 `lx_bus.c` 写死 AHB / WCN6750，`lx_os_rproc_boot()` 检查 WPSS | 新增总线后端和平台分派，保留 redwood 原路径 |

原厂的 `0x14` 允许 AMSS/WFW 两种执行环境，单凭 DT 不能确定本机最终进入哪一种。本轮 Android 只读查询没有取得这条启动日志，仍需在新 MHI 引导日志中确认。[原厂 CE 配置][vendor-ce]、[ath11k CE 配置][linux-hw]、[ath11k MHI 配置][linux-mhi]

## 具体怎么实现

### 1. 先完成 DMA 地址隔离

扩展 `xnu/files/bsd/dev/lemonade_wifi.c` 的分阶段入口，并把页表/缓冲区所有权封装为独立组件：

1. 按原厂 `msm_pcie_config_sid()` 设置 PARF 的 BDF→SID 表；端点 `01:00.0` 对应 SID `0x1c01`。保存并恢复所改表项和控制位，处理 CRC8 索引及碰撞，不能只配置 SMMU 就假设请求身份正确。
2. 占用空闲的 SMMU 匹配项和 context，保存完整旧状态；映射明确分配的固件、描述符和接收缓冲区，其他地址保持不可访问。XNU 页大小与 SMMU 4 KiB 粒度需要显式转换。
3. 先做页表与边界检查，再接入实际 BHI 传输。21:30 实机及原厂源码核对确认：本机 `IDR0.ATOSNS=1` 表示**不支持**标准 ATOS，不能把 PAR 读数当作翻译结果。软件遍历仅验证页表；还必须用端点对未映射地址的真实请求验证拦截、FAR 和 SID，再以匹配固件的正常传输验证允许路径。
4. 缓存维护、屏障、IOVA 宽度、MSI 地址必须有明确处理。退出时先让设备停止访问并关闭 Bus Master，再撤销映射、释放内存。

验收：限定缓冲区可访问、越界被拒绝；重复返回加载器后地址空间释放。原厂和实机均显示未启用 context 的 CBAR 类型可能保留，须明确区分类型位保留与活跃映射：端点先复位，SMR/S2CR、SCTLR 和页表寄存器恢复，页表释放前完成 TLB 同步。此阶段不启动扫描。[原厂 SID 配置][vendor-pcie]

### 2. 独立做通 MHI 固件启动

建议新增 `lemonade_mhi.c`，先实现受限的启动与 IPCR 通路。原厂流程明确指定 `sbl_size=512 KiB`、`seg_len=512 KiB`、完整镜像下载：

1. 从已核对哈希的原机 `amss.bin` 取前最多 512 KiB，通过 BHI 交给 PBL，检查传输完成或错误状态。
2. 建立 MHI context、command/event ring，完成 RESET→READY→M0，并等到 SBL；轮询阶段也必须真实消费事件，不能以虚假中断完成代替。
3. 将完整固件按 512 KiB 分段，通过 BHIE 向量表传输；检查对应 sequence/status，然后确认执行环境为固件运行态。
4. 每次重置或阶段变化后重读并校验相关偏移/状态；PBL 的 `MHIVER=0xdeadbeef` 不是成功证据。BHI/BHIE 返回值、执行环境、超时和 SMMU fault 分开记录。

验收：重复得到成功传输、合理执行环境及 IPCR 通道启动。所有镜像仍在 RAM；不写手机分区。若 BHI/BHIE 拒绝镜像，先查 DMA、时序和匹配固件，不通过修改固件版本字段绕过失败。[原厂 MHI 引导][vendor-boot]、[原厂初始化][vendor-init]

### 3. 用原机固件验证 QMI，再决定芯片参数

- 把 MHI 的 IPCR 20/21 通道接到 QRTR/QMI 交换；复用已有 QMI 编解码和工作队列，替换 redwood 的 WPSS/共享内存传输假设。
- 先完成服务发现、能力查询和固件内存请求；记录 chip/board ID、固件版本、服务能力和请求尺寸。只分配实际请求且有上限的内存。
- 根据真实请求处理匹配的 M3、board data 和校准材料。校准数据保持原机私有；目前仅保存了 `amss.bin` / `m3.bin`，不把板级文件已准备齐全作为前提。
- 此时才添加独立的 major 1 profile：精确匹配已验证版本、私有固件路径、独立 CE 配置及经过核对的 WCN6855 寄存器/描述符操作。未知修订继续拒绝。

验收：QMI 能力响应、内存交换和固件 ready；不能用“请求成功发出”代替“芯片返回正确响应”。

### 4. 接 ath11k 到现有网络接口

| 代码位置 | 需要的变化 |
| --- | --- |
| `tools/mk-ath11k.sh` | 增加固定来源、可校验的芯片适配补丁流程；只加入已经验证需要的改动 |
| `wifi/lx/src/lx_bus.c` | 从写死 AHB/WCN6750 改为平台选择；为 PCIe 提供带锁的动态寄存器窗口、正确 CE 配置和中断/轮询服务 |
| `xnu/files/bsd/dev/redwood_lx.c` 的 `lx_os_*` 桥 | 按平台分派 DMA、固件读取、启动状态和底层消息传输，避免调用另一块板的 WPSS/SMMU 函数 |
| `wifi/lx/src/lx_80211.c`、`lx_wpa.c`、现有 `redwood_net` | 优先复用现有扫描、关联、认证和 Ethernet 帧接口；只修实际发现的协议差异 |
| `lemonade-check-wifi.py` | 增加分别对应 DMA、固件、QMI、WMI、扫描和联网的证据要求；寄存器通过仍不算联网 |

验收顺序为 HTC/WMI ready → 扫描 → 指定网络关联/认证 → DHCP → DNS → 实际 HTTP 传输 → 断线重连 → 退出与 Android 恢复。先做基本 STA；省电、休眠唤醒、AP、漫游和峰值速度后置。

## 下一次实机的目标与停止条件

后续真机测试已完成 BDF→SID、受限 SMMU 映射、真实越界 DMA 拦截，并确认 BHI 把芯片从 PBL 带到 SBL，详见 [实测记录](WIFI-LEMONADE.md)。MHI M0、BHIE 全镜像启动和 IPCR/WLFW 服务发现也已通过；首次直接能力查询被拒绝后，已按顺序完成 WLFW 注册、内存握手和成功能力查询。下一步是板级数据/M3 和 ath11k major 1 配置。

如果 BHI 未完成、出现 SMMU fault、无法停止设备访问或不能恢复加载器，停在对应阶段修正。只有进入固件运行态后才处理 QMI/CE 差异。若真实 WMI/HTT 与现有 ath11k 不兼容，再从公开 QCA6490 HAL/协议定义补差异；目前没有证据表明必须整体搬入 Android qcacld 或重做网络栈。

最初研究轮次仅运行源码比较；随后硬件实现和各次失败/通过记录见 [实测记录](WIFI-LEMONADE.md)。网络验收仍未完成。

[linux-pci]: https://kernel.googlesource.com/pub/scm/linux/kernel/git/stable/linux/+/refs/tags/v7.0.14/drivers/net/wireless/ath/ath11k/pci.c
[linux-hw]: https://kernel.googlesource.com/pub/scm/linux/kernel/git/stable/linux/+/refs/tags/v7.0.14/drivers/net/wireless/ath/ath11k/hw.c
[linux-rx]: https://kernel.googlesource.com/pub/scm/linux/kernel/git/stable/linux/+/refs/tags/v7.0.14/drivers/net/wireless/ath/ath11k/rx_desc.h
[linux-mhi]: https://kernel.googlesource.com/pub/scm/linux/kernel/git/stable/linux/+/refs/tags/v7.0.14/drivers/net/wireless/ath/ath11k/mhi.c
[wireless]: https://wireless.docs.kernel.org/en/latest/en/users/drivers/ath11k.html
[vendor-init]: https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/bus/mhi/core/mhi_init.c
[vendor-boot]: https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/bus/mhi/core/mhi_boot.c
[vendor-pci]: https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/net/wireless/cnss2/pci.c
[vendor-pcie]: https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/pci/controller/pci-msm.c
[vendor-hal]: https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8350/blob/1e7b5673e7cc29d9af9fb0f58597055d07de6c6a/vendor/qcom/opensource/wlan/qca-wifi-host-cmn/hal/wifi3.0/qca6490/hal_6490.c
[vendor-def]: https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8350/blob/1e7b5673e7cc29d9af9fb0f58597055d07de6c6a/vendor/qcom/opensource/wlan/qca-wifi-host-cmn/hif/src/qca6490def.c
[vendor-base]: https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8350/blob/1e7b5673e7cc29d9af9fb0f58597055d07de6c6a/vendor/qcom/opensource/wlan/fw-api/hw/qca6490/v1/wcss_seq_hwiobase.h
[vendor-ce]: https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8350/blob/1e7b5673e7cc29d9af9fb0f58597055d07de6c6a/vendor/qcom/opensource/wlan/qca-wifi-host-cmn/hif/src/ce/ce_assignment.h
