# OnePlus 9 CI 组件下载与维护者发布流程

普通用户请先阅读 [复现指南](https://github.com/b1ackm4x/hackios-xnu-kernel/blob/main/docs/LEMONADE-REPRODUCE.md)。编译和实机验收由维护者在发布前完成；用户只准备自己的必要设备文件与指定 Apple 系统，本地工具负责校验、组装和启动。用户不需要运行验收程序。

`main` 每次更新都会运行主机测试、从固定 userland 提交编译，并在全部成功后发布 `ci-lemonade-<commit>` 实验版。PR 运行相同测试、编译和组包校验，不发布 Release；也可以在 Actions 手动运行。

当前公开交付的是 **供维护者集成的通用内核与驱动组件包**。系统镜像、原机固件和设备启动文件仍需在开发工作区准备；下载包不能直接启动手机。面向普通用户的预编译启动组件、本地准备工具和已验收公开运行版本尚未交付，不能把这份 CI 说明当作完整用户安装教程。

- [通用内核与驱动包](https://github.com/b1ackm4x/hackios-xnu-kernel/releases)
- [构建记录](https://github.com/b1ackm4x/hackios-xnu-kernel/actions/workflows/lemonade-ci.yml)

## 已跑通的发布记录

2026-10-25，提交 [`3ea662b9283b`](https://github.com/b1ackm4x/hackios-xnu-kernel/commit/3ea662b9283b5b417d2057ea2f898dcc424162b8) 首次完成测试、编译、打包和自动公开发布的完整流程。

| 项目 | 该次结果 |
| --- | --- |
| CI | [运行 40179317522](https://github.com/b1ackm4x/hackios-xnu-kernel/actions/runs/40179317522)，全部必需 job 成功 |
| 下载 | [ci-lemonade-3ea662b9283b](https://github.com/b1ackm4x/hackios-xnu-kernel/releases/tag/ci-lemonade-3ea662b9283b)，公开实验版 |
| 配套 userland | `dc773d94ce482d9b544accd62d82a5d1f8457e44` |
| 耗时与大小 | 全流程 6 分 10 秒；组件压缩包 29,119,778 字节，约 29 MB |
| 下载复核 | 不带登录凭据下载全部 4 个附件，外层 SHA-256、包内 98 个文件及清单逐项匹配 |
| 真机边界 | CI 未操作手机；该二进制仍标记 `hardware_verified: false` |

这里记录的是一次实际运行结果，后续版本的提交、文件数量、大小和耗时以各自清单与 Actions 记录为准。

## 组件下载与校验

选择 `OnePlus 9 CI ...` Release，下载同一版本的 `lemonade-ci-*.tar.gz`、`SHA256SUMS`、`README.md` 和 `manifest.json`。GitHub 自动生成的 Source code 只是源码快照。

这 4 个附件无需登录即可下载。版本标记为 **Pre-release**，表示 CI 实验产物，不代表已经完成整机验收。

macOS / Linux 在这些文件所在目录执行（目录里只放同一版本）：

```sh
# macOS；Linux 也可以用 sha256sum --check SHA256SUMS
shasum -a 256 -c SHA256SUMS
tar -xzf lemonade-ci-*.tar.gz
```

这是供本地集成的组件包，**不包含可直接启动或刷入手机的镜像**。源码提交在 `manifest.json` 中；包内 `sources/kernel.tar.gz` 和 `sources/userland.tar.gz` 是配套源码。现有开发环境先切换到相应提交，再使用这些构建产物；保留旧产物与运行包以便回退。

| 包内路径 | 用途 |
| --- | --- |
| `out/lemonade-kernel`、同目录两份 JSON | XNU 内核、内核清单和编译输入哈希 |
| `out/lemonade-metal/` | Metal / AIR 运行库、bundle 和构建收据 |
| `out/lemonade-base-runtime/` | init、Metal 启动助手、Safari 刷新策略库与收据 |
| `userland/obj/` | GLES 与 OSMesa 运行库 |
| `out/lemonade-bt-runtime/` | 蓝牙生产 bootstrap、原生主机兼容库及构建收据；不含固件、控制器地址或诊断程序 |
| `loader/loader.efi` | 使用通用 USB 标识 `lemonade-ci` 的 EFI 开发产物 |
| `sources/`、`licenses/` | 配套源码、ath11k 源码和许可证 |

## 维护者集成与发布责任

本节面向构建和验收发布版的维护者，不属于普通用户操作。除 `loader/` 外，产物目录结构与源码树相同。维护者将所需文件导入对应源码树后，按 [设备准备与本地构建](https://github.com/b1ackm4x/hackios-xnu-kernel/blob/main/docs/LEMONADE.md) 准备测试设备基线、脱敏 DTB、Mu-Silicium UEFI / BootShim、GPU / Wi-Fi 固件和本地系统镜像，再按 [base 构建说明](https://github.com/b1ackm4x/hackios-xnu-kernel/blob/main/docs/LEMONADE-BASE.md) 组包。这些脚本依赖既有开发环境，并非从空目录开始的用户安装器。

现有开发工具使用设备专属 loader 身份，由 `tools/lemonade-pack.py` 重新编译并打包；不能直接用包内通用 EFI 替换设备绑定的 loader。当前 base 启动器仍要求候选内核的 DMA guard 和对应镜像的联合验收记录；这是维护者候选流程的现状，**不是要求每位用户执行测试**。不得复制旧验收记录或修改标志来绕过检查。

发布给普通用户前，维护者应完成确切版本的整机验收，提供预编译启动组件和本地准备工具，并让启动器认可已验收的发布组件及支持范围。用户工具只做本机兼容性、设备身份与文件完整性检查；GPU、DMA、显示和联网验收由发布方承担。CI 自动实验版不会因一次旧 base 验收而自动成为普通用户版本。具体交付缺口见 [复现指南](https://github.com/b1ackm4x/hackios-xnu-kernel/blob/main/docs/LEMONADE-REPRODUCE.md)。

每个 Release 的附件 `README.md`、包内说明和源码归档都是该次构建的快照。后续文档更新进入新的 CI 包；已发布版本的附件保持不变，避免破坏 `SHA256SUMS` 与包内清单。最新操作说明见[仓库中的本文](https://github.com/b1ackm4x/hackios-xnu-kernel/blob/main/docs/LEMONADE-CI.md)。

## 内容与隐私

每次 CI 从源码编译 XNU 与 lemonade 驱动、ath11k/WLFW、通用 EFI 加载器、Mesa/GLES、Metal/AIR、init、Metal 启动助手、Safari 刷新策略库及蓝牙 bootstrap/兼容库。工具链与 SDK 下载后先核对固定 SHA-256。

打包器只复制明确列出的构建产物，并检查内核、源码与构建收据匹配。它不读取作者手机或本地系统镜像，不打包 `local/`、APFS、启动镜像、原机固件、DTB、Wi-Fi 密码/连接历史、个人账户、密钥或设备日志。当前系统和设备文件由维护者在开发环境中准备；面向用户的本地准备工具还需接通。

Wi-Fi 驱动已经编入内核；芯片运行固件与板级配置（例如 `amss.bin`、`m3.bin`、`board.bin`、`regdb.bin`）是另一类运行输入，按当前分发方案在本地准备。这些固件文件与家庭 Wi-Fi 的 SSID、密码及连接历史不是同一种数据。

`manifest.json` 记录内核与 userland 提交、文件大小、SHA-256 和构建链接。每个提交有独立 Release；上传到草稿后，校验服务端的全部文件与哈希再公开。重跑不会覆盖已经完整发布的版本。产物直接上传 Release，不占用额外 Actions artifact 存储。

## 验证边界

Linux 测试覆盖实际显示合成代码、fence/退出路径、触控/MHI 协议、网络与设备选择，以及电池采集、缓存失效、充电状态和生产 IOKit 适配；macOS 另运行 Metal 对象生命周期和异步完成测试。两组均运行蓝牙生产协议、固件解析、队列、UART、重连和共享电源事务的 ASan/UBSan 测试；Apple Silicon 另检查真实 Mach 页保护与 ioctl ABI。两组测试通过后才编译、打包和发布。

电池与充电状态属于新的 base 默认功能，使用 `lemonade_battery=1`。CI 组件包含配套 loader 与内核；本地重新封装启动镜像后，base 打包器要求两者均支持 `bq28z610-uefi-cache-v2`。现有 base.4 包不自动改变，也不能借用它的联合验收记录发布新组合；电池实机边界见 [电池驱动说明](LEMONADE-BATTERY.md)。

CI 不操作手机，不能替代维护者的发布前真机验收；每个自动组件包均明确记录 `hardware_verified: false`。目标是 OnePlus 9 lemonade，现有设备证据来自 LE2110 / 8 GB / AMB655X 面板，不代表其他型号、内存布局或面板已验证。用户使用已经验收的配套发布版，不承担补做这些测试的任务。

## 构建资源与维护

编译使用 `ubuntu-24.04-arm`、4 核、16 GB 内存，无需 GPU。Metal 主机测试使用 `macos-14`。公开仓库的标准 GitHub runner 免费，规格以 [GitHub runner 文档](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) 为准。本地建议 ARM64 Linux、4 核以上、16 GB 内存、至少 15 GB 可用磁盘；主要耗时在下载源码与编译。

流水线为 `.github/workflows/lemonade-ci.yml`。工具链与 SDK 版本/哈希在 `tools/ci/dependencies.json`，userland 固定版本在 `common/devices/lemonade.sources.json`。userland 独立更新不会自动改变构建输入，需要更新 kernel 仓库里的固定提交。无需配置个人 GitHub token；发布 job 使用本仓库临时 `GITHUB_TOKEN`，PR 没有发布权限。

| 触发方式 | 执行结果 |
| --- | --- |
| 本仓库更新 `main`，包括合并 PR 和文档提交 | Linux 与 Metal 测试通过后，由 `publish` job 编译、打包并发布独立实验版；`build` job 显示跳过是正常情况 |
| 面向 `main` 的 PR | 测试通过后，由 `build` job 编译、校验；不发布 Release |
| Actions → OnePlus 9 build → Run workflow，选择本仓库 `main` | 与 `main` 自动触发相同；重跑已完整发布的提交时保留原附件 |
| 手动选择其他分支 | 运行测试与编译校验，不发布 Release |

自动发布限定在 `b1ackm4x/hackios-xnu-kernel` 的 `main`。同一分支的新运行会取消尚未结束的旧运行。仅修改 userland 时，应先推送该仓库，再更新 kernel 仓库的配套提交并推送 `main`。

发布脚本 `tools/ci/publish-release.py` 先检查本地产物的 `SHA256SUMS`，再创建草稿，直接使用创建接口返回的 Release ID 上传附件、核对服务端文件清单与 SHA-256，最后公开。创建草稿后不依赖标签或列表查询立即可见；上传失败会保留未公开草稿供检查或重跑。只有测试、编译、组包、上传和公开都成功，才能把一次完整流水线记为成功。

从干净 kernel checkout 复现：安装 workflow 中的依赖，执行 `python3 tools/ci/checkout-userland.py`，指定一个尚不存在的 `THEOS` 路径，再运行 `bash tools/ci/build-lemonade.sh`。输出为 `out/ci-release/`。源码归档、构建时间等会影响文件哈希；这里保证输入配套与产物完整性，不宣称跨机器逐字节可复现。
