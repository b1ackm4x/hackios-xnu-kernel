# Sileo 图形化安装与 rootless 应用

## 修复内容

RAM 系统原先同时保留 `/Applications/Sileo.app` 和 `/var/jb/Applications/Sileo.app`。
安装软件包触发 `uikittools` 重建图标缓存时，LaunchServices 的登记路径与包管理器扫描的路径不一致，
SpringBoard 会以 `uninstalling app` 结束正在运行的 Sileo。它不是已证实的 GPU 崩溃。

现在 RAM 与 flash 构建共用一个运行布局：

```text
只读镜像 /jb 或 /private/preboot/jb
  → init 在 /private/preboot/jb-rw 挂载临时 ramfs
  → /private/preboot/jb-rw/procursus
  ← /var/jb 符号链接
```

`Applications` 中的 bundle 文件复制到运行目录，普通工具和库仍尽量共享只读文件。
`prep.sh` 只从 `/var/jb/Applications` 注册应用，取消其他路径的回退和 `/Applications` 中的重复副本。
新下载的应用也由 `uikittools` 在这一目录注册。

另一个问题出现在宿主机处理权限时：复制文件可能把 `login` 和 Sileo 的 `giveMeRoot` 从 `4755` 变成
`0755`，即使 `chmod` 没有报错。NewTerm 因此能打开界面，但登录进程立即退出，显示 `Session Ended`。
`tools/jb-setid-manifest.py` 现在从 bootstrap 原始 tar 的元数据生成 `jb/setid.manifest`；
System 与 Boot 镜像在复制完成后直接应用这份权限清单，避免依赖宿主文件系统的 set-ID 行为。

## 构建约束

- 重新运行 `tools/mk-jb.sh`，生成 prefix 和 `jb/setid.manifest`。缺少清单时，包含 jailbreak prefix 的镜像构建会明确失败。
- 原有 System 镜像必须重新构建，才能移除重复的 Sileo bundle 并加入 `/private/preboot/jb-rw` 挂载点；只替换 init 不足以完成升级。
- 构建环境需支持区分大小写的 bootstrap 解包。Docker 的原生 Linux 目录已验证；宿主共享目录可能引起 tar 目录身份错误。
- 系统与应用仍从 RAM 运行，重启不保留本轮安装内容。这项修复不实现持久化。
- 图形化测试控制器、自动触摸、命令输入和 UI 读取只放在独立验证镜像，不加入正常运行包。

## 验证

```sh
python3 userland/tests/test_jb_setid_manifest.py
python3 tools/test-lemonade-base.py
python3 tools/test-lemonade-ci.py
bash -n tools/mk-jb.sh userland/build.sh
```

权限回归覆盖宿主副本丢失特殊权限后的真实 APFS 打包与读回、缺少 `login` 权限、危险路径及符号链接替换。
另需在目标机验证 Sileo 的获取、队列、确认、安装详情、应用注册和应用启动，最后正常退出并核对 Android 分区。

2026-10-26 的本机验证使用独立镜像，沿用已验收 base.4 的 kernel、loader 和图形库，替换运行时 init、
bootstrap 布局和权限清单。它不更新原 base.4，也不等于当前源码所有组件的新 base 联合验收。
测试应用为 Chariz 的 [NewTerm 3 Beta](https://chariz.com/get/newterm-beta)，版本 `3.0~beta1`、包名
`ws.hbang.newterm3`、架构 `iphoneos-arm64`。安装动作通过 Sileo 2.5.1 界面完成，没有用命令行预装 NewTerm。

最终实机流程为 Sources → Chariz → Terminal Support → NewTerm 3 Beta → GET → 队列 → Confirm。
安装详情显示 `Setting up ws.hbang.newterm3 (3.0~beta1)`，完成 `uikittools 2.1.7` 的图标缓存更新后，
Sileo 仍为原进程；随后核对 dpkg 已安装状态和两个应用的统一登记路径。
NewTerm 启动进入 `mobile` shell，完成首次 shell 初始化后，通过应用键盘输入并执行固定验收命令。
日志核对到 `SILEO_GUI_OK`、`mobile`、`Darwin`，并独立读回临时文件中的 `SILEO_GUI_OK` 标记。
测试观察器会将子进程标准输出导向 console；因此截图只作为应用界面和输入的证据，命令结果以日志及文件读回为准。
该观察器不随正常镜像交付。

本次没有刷写或实测 flash 运行路线；也不扩展到 IPA 安装、任意 tweak 注入、其他应用的兼容性或长期稳定性。

## 日常 base 集成（2026-10-26）

`2026.10.26-base.2` 已将上述 System 布局和权限修复与当前图形、Wi-Fi、电池驱动组合，并完成联合验收及正式包 90 秒运行。组包器检查唯一 Sileo 路径、挂载点和两个 set-ID 程序的镜像权限，再加入正常的开机准备服务；允许 `prep.sh` 的注册子进程等到桌面服务就绪。实机日志确认 Sileo 自动注册成功。图形化安装测试的独立镜像记录仍保持原归属，没有改写为此组合的重复测试。

2026-10-27 的 `2026.10.27-base.2` 在保留同一 Sileo 布局与权限修复的基础上集成蓝牙，并再次通过联合及正式包运行验收。Sileo 自动注册通过；没有重复宣称该版本执行过独立的图形化安装流程。实现已与蓝牙一起合入配套 `main`。
