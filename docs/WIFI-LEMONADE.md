# OnePlus 9 Wi-Fi 移植记录

截至 2026-10-23，Wi-Fi 尚不能扫描、连接或联网。当前已完成 Android 硬件基线、XNU 只读探测、独立驱动入口和分阶段 PCIe 初始化框架。首次整段 PCIe 上电试验进入高通故障模式；修订后的分阶段上电流程尚待真机验证，不能视为修复成功。

开发分支：`codex/lemonade-wifi`。`device/lemonade` 保留此前 GPU、实体触控和研究文档的基线。本轮不修改手机分区、启动槽位、Android Wi-Fi 配置或设备校准数据。

## 已确认的硬件

同一台 LE2110 / SM8350 的 USB 身份通过私有基线哈希匹配。Android 的实时 PCI/sysfs 信息与设备树相互核对：

| 项目 | 实机结果 |
| --- | --- |
| PCIe 根控制器 | `0000:00:00.0`，`17cb:010c`，`pci-msm-rc` |
| Wi-Fi 端点 | `0000:01:00.0`，`17cb:1103`，revision 1，subsystem `17cb:0108`，`cnss_pci` |
| Android 芯片命名 | CNSS QCA6490；Linux ath11k 将同一 PCI ID 纳入 WCN6855 路径，但还需读取芯片硬件版本才能选择具体修订 |
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
| `rails` | 请求 L6/L5 原厂电压 | 新框架待测 |
| `domain` | 继续开启 PCIe0 GDSC | 待测 |
| `clocks` | 继续开启 PCIe 路径时钟 | 待测 |
| `resets` | 继续执行根控制器/PHY 复位 | 待测 |
| `controller` | 继续设置 PARF 根模式、DBI 地址、时钟复位和地址窗口 | 待测 |
| `root` | 读取根控制器 ID，要求匹配 Android 的 `17cb:010c` | 待测 |

每级都停止在指定边界，不使能无线端点、DMA、MHI 固件或射频。GCC 仅恢复本阶段涉及的位，PARF 在关闭时钟前恢复；退出加载器也调用清理入口。RPMh 模拟电源请求保留至重启，尚未实现完整 regulator 所有权管理。主机工具与内核均拒绝将上电探测与触控、GPU 或 CPU 调频实验同时运行，避免共享资源并发修改。

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

验证器检查日志哈希、模式、返回状态、阶段完成标记和恢复标记；即使全部通过，也固定输出 `networking_verified: false`。主机测试包含旧内核能力拒绝、错误目标拒绝、阶段参数和并发资源限制等场景。

后续按依赖推进：

1. 逐级确认上述电源/时钟/复位/PARF/DBI 操作与退出恢复；每级保留日志和内核哈希。
2. 按这台设备的 PHY 表和 WLAN_EN 时序训练链路，通过 iATU 读取 `17cb:1103` 端点，先保持 Bus Master 关闭。
3. 配置隔离的 SMMU DMA 映射、中断和 MHI BHI/BHIE，读取硬件版本，再选择匹配的私有 Android 固件；先确认固件执行环境和 QMI 服务。
4. 为已有 ath11k 兼容层补 PCIe/MHI 与相应芯片数据路径，接入扫描、关联和 en0；不将上游 AHB 总线假设继续套用。
5. 使用用户指定网络验证 DHCP、DNS、实际 HTTP 传输、重连及退出恢复。只有这些结果完成后，才能报告 Wi-Fi 可用。

固件内容、板级校准、MAC、SSID、密码及原始设备树只留在被 Git 忽略的私有目录。本轮采集器不读取网络凭据，也不输出网络标识。

## 可复核的公开来源

- OnePlus 官方内核固定版本：[PCIe 控制器](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/pci/controller/pci-msm.c)、[Lahaina PCIe 设备树](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/arch/arm64/boot/dts/vendor/qcom/lahaina-pcie.dtsi)、[CNSS 电源时序](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/net/wireless/cnss2/power.c)、[GDSC](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/drivers/clk/qcom/gdsc-regulator.c)。
- Linux v6.12：[ath11k PCIe](https://github.com/torvalds/linux/blob/v6.12/drivers/net/wireless/ath/ath11k/pci.c)、[MHI host](https://github.com/torvalds/linux/tree/v6.12/drivers/bus/mhi/host)。这些提供实现参考，不等于现成的 XNU 驱动。
- [OnePlus 官方强制重启说明](https://service.oneplus.com/in/search/search-detail?id=2235001)。
