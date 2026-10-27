# 2026-10-27 上游同步

本次以 `oosexgang/hackios-xnu-kernel` 的 `88513176b1e96ecf0b764cd62c6f4e6ac71345e3` 为来源。
该提交时间为 2026-10-26 18:14（Asia/Shanghai）。上游现称 HakiOS，把 kernel 与 userland 合并为一个仓库，
并改写了 `main` 历史；原 userland 仓库的 `2573c4a8d80ed143e534d16b5a386efa5b06ccbb` 仅保留迁移说明。
这与我们最初导入的两个历史没有共同祖先，因此本次按功能移植差异，保留我们的双仓维护方式和已有提交。
具体来源路径见 [同步清单](../common/upstream/20261027.json)。

## 合入内容

- MPSS modem 启动、QRTR/QMI 独立传输、SIM/UIM、NAS 注册与信号、运营商信息，以及数据会话准备与重试。
- IPA/GSI 环形队列和接收预填充修复；内核内的 ARP/DHCP 桥接；为 modem 增加 `en1`，Wi-Fi 保持 `en0`。
- 音频 DSP 与 modem 之间的 RPMh 序列器互斥；供驱动使用的只读 UFS 分区接口。
- SpringBoard 蜂窝信号与运营商入口，保留现有 Metal/Siri、显示和电池代码。
- 从设备树生成 16 个参考参数的工具、离线固件范围检查，以及对应 NAS/QMI 回归测试。
- XNU 构建脚本的 macOS 分支，以及从使用者自备 IPSW 生成 loader 身份头和纯开源占位头的工具。

可追溯的上游功能提交包括 `03a098b`（GSI 空一槽修复）、`82e0292`（DPM/WDA/WDS 顺序）、
`150d30f`（NAS 信号保留）、`0aaf719`（RPMh 锁）、`c84b463`（SoC 参数回归）。

## 下游适配与保留

- 新 modem 驱动必须同时满足显式 `modem` 参数和 `/arm-io/device_type=redwood` 检查，才能执行初始化。
  **LE2110 / lemonade 仍不启用蜂窝驱动**；`soc_*` 地址参数不能绕过机型检查。上游 IPA 的宽范围映射及 SRAM 布局
  仍属 SM7325 实验实现，不能拿参数生成成功当成 SM8350 移植完成。原有 QCA6490 DMA 防护保持不变。
- `cellular_enabled` 由内核只读上报；未获准启动 modem 时不安装蜂窝 UI hooks。信号条只使用内核测量结果，
  不采用上游“没有读数也强制一格”的兜底。
- 保留 `redwood_*` 符号、`kern.redwood`、库名及现有目录；没有执行上游全局 `hakios` 改名。
  为此修正了上游接口名固定偏移：向 Wi-Fi 服务明确传递 `en0`，不依赖项目前缀长度。
- 保留 Metal/AIR 编译器、资源生命周期、异步完成、双缓冲、120 Hz、安全退出、Safari 策略、原生蓝牙、电池与 Sileo。
  上游删除的 Metal 实现、测试和镜像内容未被删除，也没有用 POCO 的 loader 平台表或预编译二进制覆盖本机版本。
- 保留我们的双仓 `common/ugit`、CI、构建器、README 和既有许可证。上游仓库整体改名、目录重排、发布材料与
  新法律说明未直接套用为本派生项目的声明；上游原始版权声明和历史仍保留。
- `xnuloader/identity.h` 现在只在本地生成，不再纳入新源码快照。CI 通过 `LOADER_PLACEHOLDER=1` 在临时目录生成空身份头，
  不下载 IPSW，也不覆盖维护者的本地头文件。该占位 loader 不具备运行 iOS 所需的身份属性。
- 可选 `MODEM=1` 固件暂存必须同时提供本机 `MODEM_DTB`、`MODEM_NODE`；不默认使用 POCO 参数。固件不提交或发布。

## 验证与交付边界

本次完成主机 QMI、NAS、双接口收发/失败路径、机型拒绝、SoC 参数、固件边界、IM4P 截断与占位头测试，
并运行现有显示、GPU 退出、DMA 池、USB、Wi-Fi、刷新、base、Sileo、电池、Metal 和蓝牙回归。
ARM64 Linux 下重新编译 XNU、loader 和 SpringBoard/运行时库；CI 对固定的 kernel/userland 组合重新构建组件。
具体 CI 结果以对应提交的 GitHub Actions 为准，不能从这份说明推定未来运行一定通过。
macOS 构建分支已合入并检查脚本，未完成原生 macOS 全量 XNU 构建。

**这次是源码与主机构建同步，没有进行新二进制的手机验收，也没有制作或覆盖新 base。**
最近完成整套设备验收的包仍是 `2026.10.27-base.2`，内核来源 `c8033b632ec9dd76733486e7eb401e4fa02d431d`、
userland 来源 `3278f7f01796e74ecd634c0d348d2b255a37658f`。这些历史硬件结果不能自动沿用给同步后的内核。
CI 产物继续标记 `hardware_verified: false`；一加 9 蜂窝注册、通话、短信和数据均未因此获得支持。

## 下一次同步

继续 fetch 上游单仓 main，以本次 SHA 为差异起点，将文件按清单投影到我们的两个仓库。
上游的旧 userland 仓库只作迁移历史参考。优先采用功能提交，保留机型检查和我们的实现；先提交 userland，
再更新 `lemonade.sources.json` 中的固定 SHA，运行主机回归和构建，最后合入两个 main。
涉及已启用硬件路径的新运行包仍需独立设备验收，不使用 `reset --hard` 或强推追平上游。
