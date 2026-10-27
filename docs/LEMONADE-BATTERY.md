# OnePlus 9 电池驱动：实现进度与调研

调研日期：2026-10-25。对象：本项目的一加 9 LE2110 / `lemonade`，SM8350，XNU + iOS 16.5。

**真实电量和充电状态已在本机贯通电量计、XNU、IORegistry、原生 IOPS 和状态栏，并纳入 base 源码默认功能。** 2026-10-25 先验证了 98% 真实电量，随后充电状态短测确认外接电源、满电标志及绿色 100% 状态栏。物理拔插后的更新仍未验收通过：USB 日志通道重连失败。采样中断时 powerd 仍保留最后值；旧 base.4 运行包保留原版本；新 `2026.10.27-base.2` 已完成与 Wi-Fi / 图形 / 蓝牙的联合验收，10 次不同更新的内核、IORegistry 和 IOPS 对照一致，充电控制尚未实现。

<img src="assets/lemonade-battery-98.png" width="300" alt="一加 9 上 iOS 桌面状态栏显示真实电量 98%">

图像来自正常复测退出前、已完成显示传输的源表面，像素未修改；不是面板的光学拍摄。

## 0. 已实现的第一版

链路为 **外置 BQ28Z610 → 驻留 UEFI I²C → 带时间戳缓存 → XNU → IOPMPowerSource**。这轮实现不依赖移植完整 ADSP，也没有充电控制。

新的 base 构建默认带 `lemonade_battery=1`，保留 core 0 给驻留 UEFI；打包时要求 loader 与内核均声明缓存协议 v2，启动器在访问设备前再次校验。维护者的联合验收要求至少三个不同的有效电池样本，以及至少三个不同更新的内核、IORegistry、IOPS 直接对照，核对容量与接电状态一致且没有过期读数。自动采样属于正常驱动，诊断程序、自动手势和录制仍不进入运行包。早期单独电池测试使用 Wi-Fi 关闭的配置；新 `2026.10.26-base.2` 已独立取得 10 次原生对照，与 Wi-Fi / 图形联合通过，没有沿用旧包记录。

| 部分 | 已实现内容 |
| --- | --- |
| [`xnuloader/lemonade_battery.c`](../xnuloader/lemonade_battery.c) | 核对 I²C 协议 revision；只打开 `i2c3` / `0x55`；先查询型号并要求原厂定义的 `0xffa5`，之后每 5 秒读取 SOC、整包电压、温度及可选遥测 |
| [`lemonade_battery.h`](../xnu/files/bsd/sys/lemonade_battery.h) | 固定版本/长度、成功采样序号、generation、时间戳、逐字段有效性；保留零电量/零循环；错误或重复样本不能延长有效期 |
| [`bsd/dev/lemonade_battery.c`](../xnu/files/bsd/dev/lemonade_battery.c) | 仅 lemonade 且显式启用时消费缓存；校验平台、地址、协议和读数；15 秒过期；提供 `kern.lemonade_battery.*` 诊断 |
| [`RedwoodBattery.cpp`](../xnu/files/iokit/Kernel/RedwoodBattery.cpp) | 复用原生电源服务类，注册名为 `LemonadeBattery`；清除缺失/过期属性并通知订阅者；恢复后更新同一服务；保留 Redwood 原路径 |
| [`lemonade-usb.py`](../devices/oneplus-9/tools/lemonade-usb.py) | 增加 `battery` 单次查询和显式 `run --battery`；要求适配内核与非零启动核，保留 core 0 的固件上下文 |

唯一的 gauge 命令数据写入是 `Control(DEVICE_TYPE)`（`0x00` / `0x0001`）查询；正常采样只有标准寄存器读。没有 unseal、复位、校准、数据闪存、充电启停或快充命令。型号不符时停止，不继续尝试其他地址或总线。充电状态由 `ChargerEx` 的外接电源查询与 BQ28Z610 的只读 `BatteryStatus()`（`0x0a`）共同提供；净电流仅作遥测，不用其正负抖动判断充电。

充电状态使用缓存协议 v2，旧 loader 与新内核不能混用。只有外接电源状态有效时才判定供电状态：断开为电池供电；接通后按电量计 FC 标志上报已充满，按 DSG 或充电告警标志上报接电但未充电，其余为正在充电。缺少必要字段时为未知。`FullyCharged` 来自 FC，不能由 SOC=100 推导；IOKit 的 `IsCharging`、`ExternalConnected` 与 `FullyCharged` 分别上报。未读取充电能力信息，因此不设置 `ExternalChargeCapable`。

已核对 pinned 固件的两项细节：

- 一加 `QcomChargerDxeLA` revision 为 `0x10009`，现有 Xiaomi 的 `GetChargingAction` 布局不能直接复用。本实现不调用它的 Initialize/TakeAction。
- 一加 `ChargerExDxe` revision 为 `0x10004`，第一槽的 `GetChargerPresence(BOOLEAN *)` ABI 已与 pinned 二进制核对；实现同时接受公开头文件的 `0x10003`，其他 revision 不调用。此前仅接受 `0x10003` 会导致本机外接电源字段始终无效。
- `I2CDxe` revision `0x10000` 的 Open/Read/Write/Transfer/Close 布局与 [公开 EFII2C.h](https://github.com/edk2-porting/edk2-msm/blob/e1952621f419f8db60ed28271264e1b5184c571d/Silicon/Qualcomm/QcomPkg/Include/Protocol/EFII2C.h) 一致。DAL 配置中 `/dev/i2c3` 指向 `0x900000 + 0x88000`，GPIO 13/12，时钟 `gcc_qupv3_wrap0_s2_clk`；这确认了固件总线映射，仍不替代芯片响应与运行期验证。

缓存由 loader 清理到物理内存，先发布奇数序号，再发布数据和偶数序号；XNU 检查同一序号的完整快照。两端读取 `CNTVCT_EL0` 并校验频率，首次接入也会拒绝已过期数据；接收端单调时钟继续计算剩余有效期。电流为充电正、放电负；电压是 2S 整包；容量是串联整包 mAh，不乘二。若固件 I²C 调用阻塞，XNU 可独立过期，但这不证明固件调用能够按传入的 50 ms 超时返回，须实机验证。

已通过的本地主机验证：

```sh
python3 devices/oneplus-9/tools/check-lemonade-battery-firmware.py
clang -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined \
  devices/oneplus-9/tools/test-lemonade-battery.c xnuloader/lemonade_battery.c -o /tmp/lemonade-battery-test
/tmp/lemonade-battery-test
python3 devices/oneplus-9/tools/test-lemonade-battery-service.py
python3 devices/oneplus-9/tools/test-lemonade-usb.py
python3 devices/oneplus-9/tools/test-lemonade-ci.py
```

前两个电池测试直接执行生产采集模块和生产 IOKit 适配代码，替代的是硬件/IOKit 环境；覆盖异常、单位、失效、属性删除、通知和恢复，**不验证真实芯片、powerd 或 SpringBoard**。测试已加入 CI 配置；这里记录的是本地执行结果。loader、内核与个人设备的 loader-only Android v3 镜像均已构建，产物在 `out/`。随后已在这台手机上执行 RAM 启动验证，未刷写分区。

本轮 Android 实机核对了 `6-0055` 的 `bq27541-battery` 绑定、`i2c@988000` 路径和原厂日志中的 `DEVICE_TYPE=0xffa5`，基准电量为 98%。临时 loader 查询回同一型号与 98% SOC，整包电压约 8.67 V。iOS 运行中读数贯通原生服务；诊断程序 [`lemonade-battery-test.c`](../devices/oneplus-9/tools/lemonade-battery-test.c) 只读取白名单电源属性，不操作充电器。

实机联调修复了两项问题：首次地址 NACK 只重试型号查询，最多三次；UEFI 的 PE 重定位可能使缓存地址仅按 4 KiB 对齐，内核按实际 4 KiB 地址校验并映射包含它的 16 KiB 页面。对应异常和实际地址均补入回归测试。

维护者可用 `python3 devices/oneplus-9/tools/lemonade-usb.py battery` 单次查询；`run --kernel built --boot-cpu 7 --cpus 1 --battery --seconds 90` 启用有时限的内核采集。在活动的电量会话中，`battery --battery-sampling pause` / `resume` 只暂停或恢复 RAM 中的采样调度，用来验证 15 秒过期和恢复，不修改电量计或充电器。

### 本轮真机验收与边界

| 场景 | 结果 |
| --- | --- |
| 180 秒正常运行 | 36 次采样，28 次用户空间对照；SOC 98%、IORegistry 98%、IOPS 98%，错误为零 |
| 180 秒暂停/恢复试验 | 第 65 秒暂停，15 秒过期后内核 SOC 为 -1、有效位为零、IORegistry 容量已撤回；第 110 秒恢复后重新采集并更新同一服务 |
| 原生缓存限制 | 过期期间 7 次对照均看到 IOPS 仍返回最后的 98%；**不能声称停更后原生 UI 已显示未知** |
| 90 秒正常复测 | 18 次采样、10 次用户空间对照，均为有效 98%；实际显示源图像的状态栏为 98% |
| 运行与退出 | 三轮均正常返回 loader，未观察到内核 panic；测试使用 Metal / 120 Hz 直接显示，Wi-Fi 关闭 |

保留最后值的原因与 [Apple powerd 的公开实现](https://github.com/apple-oss-distributions/PowerManagement/blob/main/pmconfigd/BatteryTimeRemaining.m) 一致：对于已安装电池，缺少 CurrentCapacity / MaxCapacity 时跳过更新。这里只把公开代码作为行为解释，本机 iOS 16.5 的结论以暂停试验为准。当前没有向 powerd 填入虚假的 0% 或改变物理电池安装状态；后续需补足用户空间的失效呈现。

原生小组件、真实 SOC 发生变化后的 UI 更新和低电量仍未单独验收。与 Wi-Fi 的组合运行已在 `2026.10.26-base.2` 通过；已有 base.4 包保持原版本。电量第一轮私有证据位于 `local/lemonade/battery-dev-20261025/`，不包含在公开运行包中。

### 充电状态扩展与实机边界

2026-10-25 增加充电状态 v2。主机测试直接覆盖生产采集和 IOKit 代码中的充电、放电、接电未充电、满电、缺失字段、读错误和微小电流噪声，连同旧 Redwood 路径通过 ASan/UBSan。内核、loader 和 ARM64 原生对照程序均已重新构建。

第一轮实机查询返回 `external=1`、`BatteryStatus=0xe0`（FC、DSG、INIT 置位）。原生运行的四次对照一致：内核与 IORegistry 电量 100%，`FullyCharged=1`、`IsCharging=0`，IOPS 电量 100%、满电标志为真。这里确认的是**已接电且已充满**；不能把它当作尚未充满时正在充电的实测。

随后保持 USB 连接的 90 秒正常复测通过：18 次内核采样、10 次原生对照均为 100%，IOPS 外接电源为真、满电为真、正在充电为假，读取失败为零；正常返回 loader。实际完成显示传输的源图像中，原生状态栏为绿色 100% 电池。证据位于 `local/lemonade/charging-dev-20261025/205416-ui/`，此图不是光学拍摄。

<img src="assets/lemonade-battery-full.png" width="300" alt="一加 9 上 iOS 原生状态栏显示绿色 100% 满电图标">

物理拔插时，主机记录到了断开与重新枚举，但重连后的 fastboot 日志命令持续超时，未取回断线期间的遥测。用户确认 iOS 触控仍正常，随后回到加载器；再次拔插后 USB 接口未重新枚举，经按键重启恢复 Android，并核对同一 LE2110 已完成启动、100%、Full、USB online=1。因此**拔插后的状态更新尚未验收通过**，USB 重连问题需要单独解决。该轮部分证据位于 `local/lemonade/charging-dev-20261025/203759-ui/`。

以下第 1–7 节保留开发前的调研基线；“拟”表示原计划，当前实现进度以本节为准。充电控制仍属于后续独立阶段。

## 1. 当前上下游位置

| 层级 | 本轮核查基线 | 可以复用什么 | 尚不能据此认定什么 |
| --- | --- | --- | --- |
| 本地内核分支 | `47350883ed7f3860f9382e96a9142ed8e147c98c` | 已有显示、触控、Wi-Fi；已有电池上报框架 | 电池读取或充电已适配 |
| 配套用户空间 | `dc773d94ce482d9b544accd62d82a5d1f8457e44` | `powerd` 启动配置、原生电池界面、维护者诊断程序 | 本机 `powerd` 已收到有效电池数据 |
| HackiOS 内核上游 | `d82c64a3a31cd27a0a0afbf6e213e467e44b1b4c` | Redwood 的 UEFI 缓存、ADSP 电池查询、IOKit 服务 | Redwood 的寄存器、协议布局适用于一加 9 |
| HackiOS 用户空间上游 | `a79ff0772a3454fd1ef127e181e5b3bce6c9ce18` | 电池展示和诊断入口 | 存在可直接安装的一加 9 电池驱动 |
| Mu-Silicium | `e51cd877e31c8fbb322032dc4cb6715e83d25ad3`，见 `devices/poco-x5-pro/uefi/BASE` | lemonade 配置已列入 I²C、PMIC GLINK、Charger DXE 模块 | 协议已在本次运行中发布，或其电量值真实、持续更新 |
| OnePlus 原厂内核 | `13ca08fdf0979fdd61d5e8991661874bb2d19150` | 对应 SM8350 的电量计、充电模块、设备树 | 与本机当前 Android 固件完全相同；Linux 驱动可直接由 XNU 加载 |

本机完整运行状态以 [README](../README.md) 和 [base 验收记录](LEMONADE-BASE-120HZ.md) 为准。早期 [日常使用路线图](DAILY-USE-ROADMAP.md) 中的显示、Wi-Fi 状态是历史快照。

## 2. 本机设备树能确认什么

从已保存的本机设备树中，仅提取硬件节点和非身份属性：

| 项目 | 快照内容 | 对实现的含义 |
| --- | --- | --- |
| 电池配置 | `qcom,vbatt_num = 2`、`qcom,external_gauge`、`qcom,batt_capacity_mah = 4500` | 双电芯、外置电量计；4500 是配置容量，不能直接当作整包库仑容量 |
| 电量计 | `i2c@988000/bq27541-battery@55`，`oplus,bq27541-battery`，`qcom,batt_bq28z610` | 首要候选为原厂 BQ28Z610 分支；7 位 I²C 地址 `0x55` |
| 电量计总线 | QUPv3 SE2，MMIO `0x988000`；pinctrl 为 GPIO 12、13 / `qup2` | 与触控 SE4 / GPIO 20、21 不同；SDA/SCL 顺序和总线所有权还需核对 |
| PMIC 电池服务 | `oplus,sm8350`；GLINK 通道 `PMIC_RTR_ADSP_APPS` | 后续 USB/充电状态可研究厂商 PMIC GLINK 协议 |
| 外置充电候选 | `i2c@980000/mp2650-charger@5c`，`oplus,mp2650-charger` | 需要确认实际绑定和用途，不能直接当作当前有线主充电器 |
| 快充控制候选 | 同一 SE2 下存在 `rk826_fastcg@0a`、`sy6610_fastcg@06` | 需识别实际生效器件；不应同时初始化所有候选 |
| PMIC 家族 | PMK8350、PM8350、PM8350B、PM8350C | 与 Redwood 电源硬件不同 |

这些是**设备树声明**，不是芯片 ID 读回或 Android 驱动绑定证据。节点 `status = okay` 仍不能证明器件在这块板上实际装配或承担某项功能。后续实施前，应读取当前 Android 的驱动绑定和电池属性进行核对。

原厂源码的入口为 [oplus_chg.dtsi](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/arch/arm64/boot/dts/vendor/qcom/oneplus/oplus_chg.dtsi)；本机快照用于确定板级配置，原始 DTB 和身份内容不纳入公共文档。

## 3. 已有代码与实际缺口

### iOS 上报框架已经存在

当前链路为：

```mermaid
flowchart LR
    A[Redwood ADSP 电池数据] --> N[redwood_battery_report]
    B[拟：lemonade UEFI 或电量计数据] --> C[拟：单位、有效性和来源归一化]
    C --> N
    N --> P[RedwoodBattery / IOPMPowerSource]
    P --> D[powerd / IOPS]
    D --> U[状态栏、锁屏、电池小组件与设置]
```

- [`RedwoodBattery.cpp`](../xnu/files/iokit/Kernel/RedwoodBattery.cpp) 已实现电量、外接电源、充电标记、电压、电流、温度和容量属性，以及 `updateStatus()` 通知。
- [`redwood_battery.c`](../xnu/files/bsd/dev/redwood_battery.c) 的 ADSP 上报入口已经调用 `RedwoodBatteryReport()`。但其 **UEFI 缓存轮询只更新 sysctl，没有调用该 IOKit 入口**。
- `userland/build.sh` 默认包含 `com.apple.powerd`。`userland/src/inject_sb.c` 的电池 hook 在检测到 `RedwoodBattery` 后让位于原生接口。因此第一阶段可以继续使用现有服务名称，避免无关的重命名改动。
- `userland/src/battery_test.c` 已能检查 IORegistry 与 IOPS。它适合维护者验收，不应加入普通用户运行包。

这意味着只让状态栏显示一个数字仍不完整；应把经过验证的数据送到统一电源服务，实际检查 `powerd`、IOPS 和 UI 的一致性。

### 当前不能直接启用的原因

| 位置 | 现有行为 | lemonade 需要的改变 |
| --- | --- | --- |
| `xnuloader/loader.c: charger_start()` | 限定 `platform_redwood`；调用 `initialize(1)` | 独立的 lemonade 探测流程；先审计初始化副作用 |
| `xnuloader/efi.h` | QcomCharger 函数布局来自 Xiaomi 二进制 | 核对一加固件的 GUID、revision、函数表、输出长度和单位 |
| `charger_poll()` | 只接受 2500–5000 mV；SOC 可从单节电压估算 | 明确单节/整包语义；使用真实 SOC；读失败返回未知 |
| `charger_poll()` | 插电且 SOC < 100 即标记充电 | 分离外接电源、充电器状态和电池净电流 |
| loader 共享缓存 | version 1；字段有效性有限，无成功采样时间 | 增加逐字段有效位、来源、成功采样序号和过期判定 |
| `redwood_battery_report()` | 默认把来源标成 DSP | 支持 UEFI / 外置电量计 / GLINK 来源，并保证只有一个发布者 |
| `RedwoodBattery::report()` | SOC 100 可直接标满；外接电源直接视为可充电 | 使用独立的充满、可充电和未知状态；避免把零/未知属性伪装成测量值 |
| `redwood_adsp.c` | Redwood 平台地址、远端启动流程和属性表 | lemonade 需单独适配，不能仅修改 `adsp=1` |

当前 base 启动参数为 `adsp=0`，也没有启用 `battery=1`。Wi-Fi 已工作的 QCA6490 PCIe/MHI 通路不能证明 ADSP 的 SMEM/GLINK 通路已实现。

## 4. 应以哪些原厂驱动为依据

所有 OnePlus 链接固定在上述提交，便于后续逐行核对。

| 源码 | 用途 | 移植边界 |
| --- | --- | --- |
| [`gauge_ic/oplus_bq27541.c`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/techpack/oneplus/power/oplus_chg/gauge_ic/oplus_bq27541.c) 与 [头文件](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/techpack/oneplus/power/oplus_chg/gauge_ic/oplus_bq27541.h) | 电量计型号识别、寄存器、双电芯电压、单位与电流符号 | 提取遥测协议；其 probe/维护路径还包含配置和数据闪存写入，不能整段照搬 |
| [`oplus_charger.c`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/techpack/oneplus/power/oplus_chg/oplus_charger.c) | gauge 数据怎样变成 Android 电池属性；UI SOC、双电芯容量换算 | 是数据语义和策略参考，不作为第一阶段整体移植目标 |
| [`charger_ic/oplus_sm8350.c`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/techpack/oneplus/power/oplus_chg/charger_ic/oplus_sm8350.c) 与 [头文件](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8350/blob/13ca08fdf0979fdd61d5e8991661874bb2d19150/techpack/oneplus/power/oplus_chg/charger_ic/oplus_sm8350.h) | `oplus,sm8350` 匹配、PMIC GLINK 属性、USB/充电控制 | 与本机 compatible 更直接相关；不能只看通用 `qti_battery_charger.c` |
| Linux [`qcom_battmgr.c`](https://github.com/torvalds/linux/blob/v6.12/drivers/power/supply/qcom_battmgr.c) | SM8350 消息格式、请求/响应和单位转换 | 作为协议交叉核对，不能证明一加外置 gauge 的值都由 DSP 提供 |
| Linux [`i2c-qcom-geni.c`](https://github.com/torvalds/linux/blob/v6.12/drivers/i2c/busses/i2c-qcom-geni.c) | 原生 GENI 总线的时钟、FIFO、传输与超时处理 | 后续需接入 XNU 的 MMIO、锁、中断和电源生命周期 |
| [TI BQ28Z610 技术参考手册](https://www.ti.com/lit/pdf/sluua65) | 确认型号后的标准命令、单位和只读属性 | 不用兼容字符串代替芯片识别；不改电池校准与保护配置 |

本轮没有把设备树中的 MP2650 节点当作已确认的主充电器。在选定的 OnePlus 源码树中查到 MP2650 的引用，但尚未定位并验证与本机绑定匹配的完整实现；它属于后续器件识别任务。

### 必须避免的四类语义错误

1. **双电芯电压。** `bq27541_get_battery_mvolts()` 在 `batt_bq28z610` 分支中读取两节电芯电压，返回较高的一节。TI 标准 `Voltage()` 则表示整包电压。两者必须分别标记，不能直接互换。
2. **电流方向。** 原厂 `bq27541_get_average_current()` 把 16 位值转为有符号数后返回 `-curr`；当前 XNU 接口约定正值为充电。应按具体来源转换一次，并用充、放电观测验证；不能把所有来源统一取反。
3. **容量口径。** 原厂上层部分路径把 gauge 容量乘以 `vbatt_num`，UI 又使用 `ui_soc`。应保留原始 SOC、展示 SOC、整包容量与单节等效容量的区别。串联包的电压与容量必须在同一口径下计算能量，不能同时把电压和容量都翻倍；循环数也不能照搬厂商所有乘数。
4. **属性编号。** 原厂 `oplus_sm8350.h` 和 Linux SM8350 表中的温度为 12，电荷计数/循环/设计容量/满容量为 14–17；当前 Redwood 实测表却是温度 13、后四项 15–18。这已足以否定直接复用 Redwood 属性表；本机固件仍需逐项确认。

此外，Android 展示电量经过平滑，部分厂商属性使用 mA/mAh，而标准 Linux 属性通常采用微单位；对照时不能仅凭属性名称猜单位，也不能要求重启前后的 UI 数字逐位相同。

## 5. 推荐实施顺序

### 阶段 A：确定数据来源，先做有限探测

**A1：建立本机基准。** 由维护者读取当前 Android 的：机型/固件版本、I²C 设备的驱动名与节点关系、芯片型号、`power_supply` 的电量/电压/电流/温度/状态/容量字段。使用字段白名单，保留时间戳和单位；不采集序列号、认证材料或整份私有系统转储。原始 gauge SOC 和 Android UI SOC 分开记录。

**A2：验证已有 UEFI 查询入口。** Mu-Silicium 的 lemonade [DXE 配置](https://github.com/Project-Silicium/Mu-Silicium/blob/e51cd877e31c8fbb322032dc4cb6715e83d25ad3/Platforms/OnePlus/lemonadePkg/Include/DXE.inc) 与 [APRIORI 配置](https://github.com/Project-Silicium/Mu-Silicium/blob/e51cd877e31c8fbb322032dc4cb6715e83d25ad3/Platforms/OnePlus/lemonadePkg/Include/APRIORI.inc) 已列出 `I2CDxe`、`PmicGlinkDxe`、`QcomChargerDxeLA` 和 `ChargerExDxe`。

先枚举协议及 revision，再核对 ABI；已核对的 `ChargerEx` 公开头提供电池存在、外接电源存在和电压查询，**没有 SOC 查询**。一加的 QcomCharger 输出布局仍需验证。`GetChargingAction` 名称本身不能证明无副作用；现有 `initialize(1)` 更不能被归入纯状态读取。

若查询在当前驻留 UEFI 模型中持续返回可信 SOC、电压和温度，优先把该路径接入缓存。应沿用 core 0 上的固件调用上下文，避免由任意 XNU 核心调用 UEFI；现有轮询还受 UFS 所有权条件限制，需要把停更纳入检测。

**退出条件：** 协议缺失、ABI 无法确认、只能读到固定/估算数据，或查询需不明充电初始化时，结束这条探测，转 A3；不要用电压曲线或固定 50% 填补。

**A3：外置 gauge 路径。** 先判断固件 I²C 协议能否在正确所有权下读取 SE2/`0x55`；模块存在不等于已提供可用总线 API。否则实现独立 lemonade I²C 后端。

触控已有开漏 GPIO I²C 代码，可复用超时、重复起始和引脚恢复思路做有边界的电量计探针；需先确认 SE2 引脚及固件/快充控制器是否仍占用总线。不能把触控引脚常量换成 12/13 就认为完成移植，也不能让 UEFI 和 XNU 同时驱动该总线。长期后端采用 GENI，补时钟、总线仲裁、恢复和休眠处理。

型号核对为 BQ28Z610 后，首批只读标准命令候选如下；地址表示低字节起点，返回值按手册解码：

| 量 | 地址 | 原始单位 |
| --- | --- | --- |
| 温度 | `0x06` | 0.1 K |
| 整包电压 | `0x08` | mV |
| 瞬时电流 / 平均电流 | `0x0c` / `0x14` | 有符号 mA |
| 剩余容量 / 满充容量 | `0x10` / `0x12` | mAh |
| 循环数 / SOC | `0x2a` / `0x2c` | 次 / % |

来源：[TI 手册第 12 章](https://www.ti.com/lit/pdf/sluua65)。两节独立电压可进一步核对厂商 `bq28z610_get_2cell_voltage()` 的遥测子命令。I²C 的寄存器选择写或已确认的遥测选择命令，与修改数据闪存/保护配置是不同操作；第一阶段只允许前者的明确白名单。

### 阶段 B：真实电量进入 iOS 原生接口

拟增设统一的 `lemonade_battery_sample`，至少包含：

- 来源、有效字段位图、成功采样序号、错误计数和新鲜度；切换来源时有 generation，旧响应不可覆盖新源。
- SOC（百分之一百分点）、整包电压（µV）、净电流（µA，充电为正）、温度（百分之一 °C）。
- 电芯电压单独存储；剩余/满充/设计容量标明口径，循环数保留真实值。
- 电池存在、外接电源、可充电、充电状态分别携带有效性；未知不等于 false。

拟先按 5 秒周期采样，连续三个周期没有新的成功样本便标为过期；这是起始策略，需按实机延迟调整。以成功采样序号和接收端单调时钟判断新鲜度，不把重复读取同一缓存计作成功更新，也不直接比较未经换算的两个时钟域时间戳。

归一化后调用统一上报入口，复用 `RedwoodBattery`。应修正现有接口的几个边界：零电量和零循环是有效值；负温度、放电电流不可被无符号转换破坏；不可用的容量与适配器功率不伪造；过期不能被当成电池拔除；SOC=100 不单独证明充满。首次有效数据之前展示未知，后续失效保留诊断信息并撤销未经确认的充电断言。具体 IOPS/UI 的失效表示需要一起验收。

**本阶段完成标准：** IORegistry、IOPS、状态栏与电池小组件读取同一份真实 SOC；能观察连续更新、读取失败与恢复。若尚无可信 USB 状态源，可以先交付真实电量，不能宣称已支持充电识别。

### 阶段 C：USB 与充电状态

先尝试经已核对的固件入口取得插拔状态，再补充电状态。仅有 gauge 电流可以证明电池净充/放电，不能完整描述 USB、PD 协商和充电器状态。

若必须走原生 PMIC GLINK，需要补齐 SM8350 的 ADSP 生命周期、SMEM、mailbox/中断、GLINK/RPMsg 及服务发现，再实现厂商电池协议。公开参考中的 owner 为 `32778`，电池 GET 为 `0x30`，USB GET 为 `0x32`；请求/响应的长度、property ID、返回码、超时和重启代次必须检查。第一轮只查询已核对的属性，不发送 SET 或快充协商命令。

此路径可以为音频等后续功能积累基础，但其工作量明显大于接入外置 gauge，不应为了显示百分比先移植完整 DSP 栈。也不能假定 DSP 能给出与一加外置 gauge 相同的 SOC。

**本阶段完成标准：** 能区分拔线、插线且未充电、实际充电、充满与状态未知；插线但电池净放电时，诊断信息应明确反映电源状态与净电流的差异。

### 阶段 D：充电控制与电源管理

单独确定实际主充电器、PMIC、外置芯片、快充 MCU 与固件各自负责的功能，之后才设计启停和电流/电压限制。先验证普通充电，Warp/VOOC 和其他快充另立阶段。

需要明确保留或实现温度分区限制、过压/过流、双电芯异常、充电终止/再充电、USB 输入限制、看门狗、通信失效后的行为，以及退出 XNU/重启后的控制权交接。固件里有 Charger 模块、界面出现闪电或 USB 接入，都不足以证明这些功能持续工作。

充电完成也不等于续航完成。120 Hz 显示、CPU/GPU 调频、Wi-Fi 省电、轮询和休眠唤醒需要另外测量和优化，电量驱动首先提供可用的测量基础。

## 6. 后续代码落点

| 文件或模块 | 拟改动 |
| --- | --- |
| `xnuloader/loader.c`、`xnuloader/efi.h` | lemonade 固件能力探测、已确认 ABI、版本化采样缓存；保留 Redwood 原路径 |
| 拟 `xnu/files/bsd/dev/lemonade_battery.c` | 数据源选择、有效性、归一化、轮询和错误恢复 |
| 拟 `xnu/files/bsd/sys/lemonade_battery.h` | 采样协议及单位定义；避免多处复制结构体漂移 |
| 拟 `xnu/files/bsd/dev/lemonade_i2c.c` | 确需直接读 gauge 时增加总线实现、互斥、超时与恢复 |
| `xnu/files/bsd/dev/redwood_battery.c` | 分离采样源与公共上报；修正来源被固定写成 DSP 的问题 |
| `xnu/files/iokit/Kernel/RedwoodBattery.cpp` | 逐字段有效性、未知/过期/零值、充满与外接电源语义 |
| `devices/oneplus-9/device.conf`、base 构建与启动工具 | 通过能力检查后启用电池；未通过的固件路径不进入默认运行包 |
| `userland/src/battery_test.c` 与维护者测试配置 | 观察 IOPS/IORegistry/数据源；与普通用户包分离 |
| 后续 SM8350 ADSP/GLINK 模块 | 在阶段 C 确有需要时拆分实现；不把 Redwood 地址改名后直接复用 |

第一份实现应以 **A 阶段探测 + B 阶段采样模型和原生接口** 为范围，不把快充控制混入。若 UEFI 已能提供可信 SOC，就先完成这条短路径；若不行，转外置 gauge，保留同一上报接口。

## 7. 验收由维护者完成

| 检查 | 所需证据 |
| --- | --- |
| 单位与状态模型 | 主机测试覆盖负电流、负温度、零电量/零循环、2S 容量口径、未知字段、过期和来源切换 |
| 编译 | 修改涉及的 loader、UEFI、XNU 与用户空间构建成功；不把构建成功作为芯片响应证据 |
| 真实读取 | 连续带时间戳样本，与 Android 的原始/展示读数分开对照；重启前后差异按采样时刻和 SOC 平滑解释 |
| 插拔与充电识别 | 多次插拔、插电带负载、非充电状态和恢复；同时检查状态与净电流 |
| iOS 全链路 | IORegistry → powerd/IOPS → 状态栏、小组件与设置；检查服务重启和数据失效 |
| 原功能回归 | 显示/触控/Wi-Fi、USB 通信和正常退出；确认总线操作没有破坏其他外设 |
| 真正充电 | 已确认控制责任、持续净充电与温度/故障处理证据；不以一次百分比上涨代替验收 |
| 发布 | 能力与固件组合固定，默认包只启用已验收功能；测试程序不交给普通用户执行 |

本轮结论是已有明确的源码依据和可实施路径。尚待实机确定的核心问题是：**准确 gauge 型号与固件版本、UEFI 读数的真实性、SE2 总线所有权、主充电器绑定及保护策略责任归属。** 这些决定采用哪条数据源，而不影响先复用 iOS 原生电源接口的总体方案。
