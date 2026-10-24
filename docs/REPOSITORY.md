# 仓库与机型分支维护

当前适配目标是 OnePlus 9（LE2110，`lemonade`）。上游将内核和用户空间拆成两个 Git 仓库，
构建时将它们叠放到同一个工作目录。我们沿用这个结构，避免漏掉 GLES 桥接和镜像构建改动。

| 内容 | 我们的仓库 | 上游 |
| --- | --- | --- |
| 内核、loader、机型配置和测试工具 | `b1ackm4x/hackios-xnu-kernel` | `oosexgang/hackios-xnu-kernel` |
| 用户空间、GLES 桥接、Mesa 和镜像构建 | `b1ackm4x/hackios-xnu-userland` | `oosexgang/hackios-xnu-userland` |

两个仓库均为公开的独立派生仓库，不属于 GitHub 的 Fork 网络；
原许可证、作者和完整上游提交历史全部保留。

## 分支与远端

- `main` 是两个仓库的当前集成分支，已包含一加 9 的完整 base 改动；引入时的上游基线由 `lemonade.sources.json` 中的 `upstream_base` 保留。
- `origin` 指向自己的仓库；`upstream` 指向对应的上游。推送默认使用 `origin`。
- 以后新增机型，从适当的共同基线创建 `device/<codename>`，把机型参数和驱动保持隔离。
- 通用改进使用短期 `feature/<topic>` 或 `fix/<topic>` 分支，验证后合入 `main`；机型专用实验仍可使用机型分支。

阶段基线 `2026.10.24-base.1` 在两个仓库均有标签 `base-2026.10.24-1`。
配套提交见 `common/devices/lemonade.sources.json`，验收记录见 [LEMONADE-BASE.md](LEMONADE-BASE.md)。

## 重新取出项目

克隆公开源码无需登录；推送前通过 `gh auth login` 配置访问权限，不要把令牌放进远端 URL。

```sh
git clone --branch main https://github.com/b1ackm4x/hackios-xnu-kernel.git hackios
cd hackios
common/setup.sh
git remote add upstream https://github.com/oosexgang/hackios-xnu-kernel.git
common/ugit remote add upstream https://github.com/oosexgang/hackios-xnu-userland.git
git config remote.pushDefault origin
common/ugit config remote.pushDefault origin
```

`common/setup.sh` 初次取出用户空间时默认使用 `main`；已有工作区更新当前分支，如需跟随集成分支先运行 `common/ugit switch main`。
`common/devices/lemonade.sources.json` 记录配套用户空间提交、base 标签及两边的上游基线。
需要复现该组合时，可用 `common/ugit switch --detach <userland.commit>` 切到记录的提交；
继续开发前用 `common/ugit switch main` 回到当前集成分支。

这只取回可维护的源代码。设备原始备份、原机固件、Apple 系统文件、SDK、构建产物和私有验证日志
不随仓库分发；准备和构建步骤见 [LEMONADE.md](LEMONADE.md)。

## 日常提交

普通 `git` 管内核仓库；`common/ugit` 管同目录下的用户空间仓库（Git 数据位于 `.userland.git`）。
不要在 `userland/` 中再执行 `git init`，也不要把 `.userland.git` 当作源码提交。

```sh
git status --short
common/ugit status --short
git diff --check
common/ugit diff --check
python3 tools/test-lemonade-usb.py
cc -Wall -Wextra -Werror -I xnu/files/bsd/dev tools/test-lemonade-touch.c -o /tmp/lemonade-touch-test
/tmp/lemonade-touch-test

# 按文件归属分别检查、暂存和提交；不批量强制添加被忽略的目录。
git add <kernel-paths>
git commit -m 'Describe the kernel or device change'
common/ugit add -u -- <tracked-userland-paths>
common/ugit commit -m 'Describe the userland change'
git push origin HEAD
common/ugit push origin HEAD
```

两边同时修改时，先提交用户空间，再更新内核仓库内的 `lemonade.sources.json`，记录配套的用户空间提交。
真实设备测试的通过范围、性能数据和未完成项写入 `LEMONADE.md`，不要把构建成功写成硬件验收成功。
两个仓库共用内核的 `.gitignore`；新增用户空间源文件可能被顶层白名单隐藏，需要先检查内容，
再用 `common/ugit add -f -- <exact-new-source-file>` 精确添加，禁止对目录使用强制添加。

## 合并上游

```sh
git fetch upstream
common/ugit fetch upstream
git log --oneline HEAD..upstream/main
common/ugit log --oneline HEAD..upstream/main
```

工作区干净后，在短期同步分支分别合并 `upstream/main`，解决冲突并完成主机测试与相关设备验证，
再合入 `main`。不要用 `reset --hard upstream/main` 覆盖我们的机型适配。

## 留在本地的资料

`local/`、`out/`、`logs/`、`fw-*/`、系统镜像、设备身份、配对信息、密钥与凭据均不提交。
上游已公开的源码和随历史附带的预构建文件仍保留；新增的本机专用镜像与固件不上传。
源码仓库公开；本地 base 压缩包及以上私有资料不随仓库发布。
