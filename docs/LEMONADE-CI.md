# OnePlus 9 自动构建与下载

`main` 每次更新都会运行主机测试、从固定 userland 提交编译，并在测试和打包成功后发布 `ci-lemonade-<commit>` 实验版。PR 运行相同测试和完整组包校验，不发布 Release；也可以在 Actions 手动运行。

- [完整运行包](https://github.com/b1ackm4x/hackios-xnu-kernel/releases)
- [构建记录](https://github.com/b1ackm4x/hackios-xnu-kernel/actions/workflows/lemonade-ci.yml)

## 下载与使用

选择 `OnePlus 9 CI ...` Release，下载同一版本的全部 `lemonade-ci-*.tar.gz.part-*`、`SHA256SUMS`、`README.md` 和 `manifest.json`。`runtime-inputs` Release 是流水线依赖，不能单独启动。GitHub 自动生成的 Source code 也不是运行包。

macOS / Linux 在这些文件所在目录执行（目录里只放同一版本）：

```sh
# macOS；Linux 也可以用 sha256sum --check SHA256SUMS
shasum -a 256 -c SHA256SUMS
cat lemonade-ci-*.tar.gz.part-* | tar -xz
cd lemonade-ci-*/
python3 launch.py verify
python3 launch.py start --experimental
# 退出并回到原 Android
python3 launch.py android
```

需要 Python 3.11+、Android Platform Tools 的 `adb` 和 `fastboot`。当前启动器仅接受 **OnePlus 9 LE2110、8 GB 对应内存布局、SM8350、AMB655X 面板、已解锁 bootloader，并且 Android 有 root**。先启动 Android、允许 USB 调试，只连接一台目标手机。启动器会读取当前面板类型和内存布局，再进入 bootloader；不会保存或显示完整 Android 启动参数，也不绑定作者的手机序列号。其他 OnePlus 9 型号、12 GB 内存配置和面板没有通过此启动路径验证。

这是 **RAM 临时启动包**：使用 `fastboot boot`，随后上传镜像到内存，没有 `fastboot flash`、格式化、分区安装或用户数据持久化。Wi-Fi 在系统设置内手动连接；启动时没有已保存的网络，重新启动后连接信息也不保留。桌面没有自动测试、自动手势或定时退出。

## 包含什么

包内有 `boot.img`、XNU `kernel`、`root.apfs`、`system.apfs`、启动器、构建收据、源码快照和许可证。包含 GPU / QCA6490 固件及完整系统运行内容；不包含家中 Wi-Fi 密码、连接历史、个人账户、SSH 密钥、设备日志或个人 USB 绑定。

每次 CI 重新编译：XNU 与 lemonade 驱动、ath11k/WLFW、通用 EFI 加载器、Mesa/GLES、Metal/AIR、init、Metal 启动助手、Safari 刷新策略库。系统镜像、其余 root 模板、Mu-Silicium UEFI/shim 和脱敏 DTB 从固定 Release 下载，逐个验证哈希；这部分是复用输入，并非每次从源码重建。root 模板的所有文件先核对清单，再替换本次新编译的库、固件驱动运行入口，重新生成 APFS 并读回验证。userland 提交变化时必须更新模板，防止新旧配套混用。

大文件直接发布到 Release，不进入 Git 历史，也不在 Actions artifact 存储中另存一份。完整压缩包被切成不超过 1500 MiB 的分卷，以符合 GitHub Release 单文件小于 2 GiB 的限制。`manifest.json` 记录内核与 userland 提交、输入版本、文件大小、SHA-256 和构建链接。

## 验证边界

CI 检查实际显示合成代码、fence/退出路径、触控/MHI 协议、网络与设备选择等主机测试；macOS 另外运行 Metal 对象生命周期和异步完成测试。随后在 ARM64 Linux 编译与组包。

**这些不能替代真机验收。** 每个 CI 包都记录 `hardware_verified: false`。`--experimental` 明确允许尚未经该二进制真机验收的集成 GPU / 显示 / Wi-Fi 路径，包括开启驱动需要的 DMA 固件探测开关；这不是已通过 DMA 验证的证明。现有本地验收版的严格启动检查保持独立。CI 不会自动操作手机，也不会将旧版本验收结果冒充新版本结果。

## 构建资源与维护

编译使用 `ubuntu-24.04-arm`、4 核、16 GB 内存；无需 GPU。Metal 主机测试使用 `macos-14`。公开仓库的标准 GitHub runner 免费，资源规格以 [GitHub runner 文档](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) 为准。本地建议 ARM64 Linux、4 核以上、16 GB 内存、至少 20 GB 可用磁盘；网络主要用于下载源码、SDK 和约 2.5 GB 的系统输入。

流水线为 `.github/workflows/lemonade-ci.yml`。工具链与 SDK 版本/哈希在 `tools/ci/dependencies.json`，运行输入在 `tools/ci/runtime-inputs.json`，userland 固定版本在 `common/devices/lemonade.sources.json`。无需配置个人 GitHub token；发布 job 仅使用本仓库临时 `GITHUB_TOKEN`，PR 没有发布权限。

从干净 kernel checkout 复现：安装 workflow 中的依赖，执行 `python3 tools/ci/checkout-userland.py`，指定一个尚不存在的 `THEOS` 路径，再运行 `bash tools/ci/build-lemonade.sh`。输出为 `out/ci-release/`。源码归档、构建时间等会影响文件哈希；这里保证输入配套与产物完整性，不宣称跨机器逐字节可复现。
