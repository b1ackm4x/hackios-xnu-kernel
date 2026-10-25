# OnePlus 9 自动构建与下载

`main` 每次更新都会运行主机测试、从固定 userland 提交编译，并在全部成功后发布 `ci-lemonade-<commit>` 实验版。PR 运行相同测试、编译和组包校验，不发布 Release；也可以在 Actions 手动运行。

- [通用内核与驱动包](https://github.com/b1ackm4x/hackios-xnu-kernel/releases)
- [构建记录](https://github.com/b1ackm4x/hackios-xnu-kernel/actions/workflows/lemonade-ci.yml)

## 下载与使用

选择 `OnePlus 9 CI ...` Release，下载同一版本的 `lemonade-ci-*.tar.gz`、`SHA256SUMS`、`README.md` 和 `manifest.json`。GitHub 自动生成的 Source code 只是源码快照。

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
| `loader/loader.efi` | 使用通用 USB 标识 `lemonade-ci` 的 EFI 开发产物 |
| `sources/`、`licenses/` | 配套源码、ath11k 源码和许可证 |

除 `loader/` 外，产物目录结构与源码树相同。将所需文件导入对应源码树后，按 [设备准备与本地构建](https://github.com/b1ackm4x/hackios-xnu-kernel/blob/main/docs/LEMONADE.md) 准备自己设备的基线、脱敏 DTB、Mu-Silicium UEFI、GPU / Wi-Fi 固件和本地系统镜像，再按 [base 构建说明](https://github.com/b1ackm4x/hackios-xnu-kernel/blob/main/docs/LEMONADE-BASE.md) 组包。

本地设备工具使用设备专属 loader 身份，须由 `tools/lemonade-pack.py` 为目标手机重新生成；不能直接用包内通用 EFI 替换设备绑定的 loader。新内核仍需通过其自身的 DMA guard 与联合验收，才可走正式 base 启动器。不得复制旧验收记录来绕过检查。

## 内容与隐私

每次 CI 从源码编译 XNU 与 lemonade 驱动、ath11k/WLFW、通用 EFI 加载器、Mesa/GLES、Metal/AIR、init、Metal 启动助手和 Safari 刷新策略库。工具链与 SDK 下载后先核对固定 SHA-256。

打包器只复制明确列出的构建产物，并检查内核、源码与构建收据匹配。它不读取作者手机或本地系统镜像，不打包 `local/`、APFS、启动镜像、原机固件、DTB、Wi-Fi 密码/连接历史、个人账户、密钥或设备日志。系统和设备文件由使用者在本地补齐。

`manifest.json` 记录内核与 userland 提交、文件大小、SHA-256 和构建链接。每个提交有独立 Release；上传到草稿后，校验服务端的全部文件与哈希再公开。重跑不会覆盖已经完整发布的版本。产物直接上传 Release，不占用额外 Actions artifact 存储。

## 验证边界

Linux 测试覆盖实际显示合成代码、fence/退出路径、触控/MHI 协议、网络与设备选择等；macOS 另运行 Metal 对象生命周期和异步完成测试。两组测试通过后才编译、打包和发布。

CI 不操作手机，不能替代真机验收；每个包均明确记录 `hardware_verified: false`。目标是 OnePlus 9 lemonade，现有设备证据来自 LE2110 / 8 GB / AMB655X 面板，不代表其他型号、内存布局或面板已验证。

## 构建资源与维护

编译使用 `ubuntu-24.04-arm`、4 核、16 GB 内存，无需 GPU。Metal 主机测试使用 `macos-14`。公开仓库的标准 GitHub runner 免费，规格以 [GitHub runner 文档](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) 为准。本地建议 ARM64 Linux、4 核以上、16 GB 内存、至少 15 GB 可用磁盘；主要耗时在下载源码与编译。

流水线为 `.github/workflows/lemonade-ci.yml`。工具链与 SDK 版本/哈希在 `tools/ci/dependencies.json`，userland 固定版本在 `common/devices/lemonade.sources.json`。userland 独立更新不会自动改变构建输入，需要更新 kernel 仓库里的固定提交。无需配置个人 GitHub token；发布 job 使用本仓库临时 `GITHUB_TOKEN`，PR 没有发布权限。

从干净 kernel checkout 复现：安装 workflow 中的依赖，执行 `python3 tools/ci/checkout-userland.py`，指定一个尚不存在的 `THEOS` 路径，再运行 `bash tools/ci/build-lemonade.sh`。输出为 `out/ci-release/`。源码归档、构建时间等会影响文件哈希；这里保证输入配套与产物完整性，不宣称跨机器逐字节可复现。
