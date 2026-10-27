# 单仓库协作

内核、加载器、用户态、镜像工具和设备支持现在由一个 Git 仓库、一个 `main` 分支维护。仓库地址继续使用 `https://github.com/b1ackm4x/hackios-xnu-kernel`，名称中的 `kernel` 不再表示只含内核。

```sh
git clone https://github.com/b1ackm4x/hackios-xnu-kernel.git hackios
cd hackios
common/setup.sh
make
```

一次 clone 已包含用户态，不需要第二次 clone、子模块或独立 userland 提交。普通 `git status`、`git diff`、`git commit` 处理全部源码。`common/ugit` 只为旧命令提供转发，执行的是同一个根仓库的 Git。

## 与上游一致的目录职责

| 路径 | 内容 |
| --- | --- |
| `xnu/`、`xnu-deps/`、`xnu-build.sh` | 内核补丁、驱动、依赖与构建 |
| `xnuloader/`、`mkxnuimg.py` | 通用加载器和启动镜像组装 |
| `userland/` | init、运行库、服务和根镜像构建 |
| `sysimage/` | iOS 系统文件的本地提取工具；提取内容不进入 Git |
| `devices/oneplus-9/` | OnePlus 9 配置、服务清单、UEFI 输入、设备工具与测试 |
| `devices/poco-x5-pro/` | 上游 POCO 配置、SoC 参数、UEFI 和公开预编译文件 |
| `devices/qemu-virt/` | QEMU 设备配置 |
| `tools/`、`qemu/`、`prebuilt/` | 共享开发工具、模拟器与公共组件 |
| `common/` | 保留的跨设备构建、运行和开发工作区辅助脚本 |

目录使用上游的产品名；已有运行时 codename `lemonade`、`redwood` 仍可用于 `common/build.sh`。`make` 默认显示帮助，默认设备是 `oneplus-9`。设备参数仍经过原有校验，一加不会使用 POCO 的 UEFI、DTB 或 modem 默认参数。

## 构建与版本

`make components` 在 Linux 中运行完整公共组件构建，要求准备 workflow 列出的主机依赖并设置 `THEOS`。它不需要手机、系统镜像或私有设备文件。详细流程见 [CI 文档](LEMONADE-CI.md)。

`make images` 使用当前设备配置，要求已准备本地系统文件。`make phone BASE=/absolute/path/to/sealed-base` 调用已封装 OnePlus base 中的启动器；源码编译成功不自动构成新版本的实机验收。

CI 只 checkout 一次。包内 `sources/source.tar.gz` 是完整源码；`manifest.json` 使用唯一的 `source_commit`，不再维护活动的 kernel/userland 提交配对。[设备来源清单](../devices/oneplus-9/sources.json) 保留上游基线、旧仓库导入点和已验收 base 的历史提交组合。

## 提交与历史

合并保留两个仓库的完整祖先历史：内核导入点 `3b45ed6879676eb9f844f38746f04ee15904e2a0`，用户态导入点 `f86a705e549ec1f8caca8e0bdd3b37450634dbd1`。两者都是合并提交的父历史，用户态文件保持原 Git mode 和内容导入。目录重组作为随后独立提交，便于查看改名和回溯。

根目录 `LICENSE` 保留原内核许可，用户态原 MIT 许可保存在 `licenses/userland-MIT.txt`。作者归属和旧提交不重写；旧用户态远端继续保留原历史，不再是构建依赖。

新增文件按明确路径添加：

```sh
git diff --check
git add -- <source-files>
git commit -m 'Describe the integrated change'
```

根目录使用白名单忽略规则。`local/`、`out/`、`fw-*`、系统提取缓存、私有 DTB/固件、身份头和密钥不得进入源码提交。`python3 tools/ci/check-source.py` 检查单仓库布局和关键忽略规则；需要添加被忽略的新源码时，检查内容后只对确切文件使用 `git add -f -- <file>`。
