# 一加 9 · iOS 120 Hz

2026-10-25 更新：本功能已并入 `main`，并成为
[`2026.10.25-base.1`](LEMONADE-BASE-120HZ.md) 的默认设置。下文保留首次实验与应用验收记录。

2026-10-24，`codex/lemonade-120hz` 在 LE2110 / AMB655X 上完成两轮 100 秒真机验收：
iOS 16.5 的系统显示模式、UIKit 帧回调和实际变化画面的 DPU 输出均达到 120 Hz 级别。
这里的 100 秒包含系统启动、约 47 秒应用测试与随后桌面操作，不能当作 100 秒连续应用
压力测试。原 `2026.10.24-base.1` 发布目录、压缩包与标签保持不变，默认仍为 60 Hz。

## 结果

验收使用同一内核与同一套用户空间文件。内核 SHA-256：
`e0a3da6a5f86a24a5a0a69f2f066a01a3fa5fff06d03444fbc9aebb15cfd0484`。

| 测量 | 第一轮 | 第二轮 |
| --- | --- | --- |
| 面板外部 TE，64 个周期 | 121.179 Hz | 121.202 Hz |
| 系统当前模式 / UIKit 最高帧率 | 120 / 120 | 120 / 120 |
| CADisplayLink 稳态回调 | 约 120.000 fps | 约 120.000 fps |
| CADisplayLink 驱动画面的 DPU 完成速率 | 119.954–119.959 fps | 119.574–119.770 fps |
| Core Animation 动画的 DPU 完成速率 | 121.172–121.185 fps | 121.151–121.165 fps |
| 全程已完成直接显示提交 | 6628 | 6610 |
| 回退 / 失败 / 退出残留映射页 | 0 / 0 / 0 | 0 / 0 / 0 |
| 面板、GOP、SMMU、IRQ 恢复 | 通过 | 通过 |

应用先用 `CADisplayLink` 移动矩形并更新逐帧编号，20 秒后改用 `CABasicAnimation`。
检查器将应用的单调时钟与驱动的 5 秒测量窗口对齐，只接纳完整落在相应阶段内的窗口。
每个窗口必须同时满足完成帧数、实际耗时、硬件帧变化和 MISR 签名变化要求，因此仅增加
回调次数或重复显示同一帧不能通过。第一阶段每个完整窗口实际变化 598–600 帧，第二阶段
每个窗口完成 606 帧、变化 605–606 帧。

原厂“120 Hz”模式在本机的电气节拍约为 121.2 Hz；UIKit 使用 120 Hz 帧时钟。二者的
节拍差会产生偶发的双 TE 间隔，应用驱动阶段最长实际完成间隔约 16.6 ms，Core Animation
阶段最长约 8.32 ms。以上是 TE、DPU 完成与内容签名测量，没有高速相机的光学无撕裂证据。

同一新内核及用户空间另跑 100 秒 **60 Hz 对照**：UIKit 为 60.000 fps，应用驱动的变化画面
为 59.975–59.986 fps，Core Animation 为 60.588–60.594 fps；3482 次显示提交全部完成，
无失败、回退或残留映射。测试应用的 `late` 字段使用 12.5 ms 的 120 Hz 门槛，60 Hz 对照
中的计数不表示回归。未请求 120 Hz 时，系统模式兼容补丁不会运行。

最后返回原 Android：启动完成、USB 身份、槽位与锁状态一致，`boot`、`vendor_boot`、
`dtbo`、`vbmeta`、`xbl` 五个当前槽位分区的哈希均与原备份相同。全程仅从 RAM 启动。

## 改动

1. 在用户进程启动前建立直接显示路径，按原厂序列切换面板，先实测至少 64 个 TE 周期。
   只有测量成功才发布 120 Hz 的 `TimingElements` 和 `RedwoodMeasuredRefreshHz`；启动参数
   `fb_hz=120` 本身不能授权这个模式。
2. `IOMobileFramebuffer` 的黑屏提交改为使用内核持有的黑色缓冲区，遵守相同的 DMA 完成、
   解映射与生产者释放规则。移除所有图层是正常的桌面过渡，不再因此退回 CPU 拷贝和 60 Hz。
3. iOS 16.5 / 20F66 的显示服务对“单时序、单颜色模式”有一个快捷分支，跳过时序解析并留下
   原生 60 Hz 默认值。`inject.c` 仅在请求 120 Hz、驱动报告实测 120 Hz、指令序列完全匹配时，
   对 backboardd 私有 COW 代码页关闭这一分支，让原系统解析真实时序。没有修改应用的 FPS
   getter、计时器或机型身份，也不改磁盘上的 Apple 文件。其他系统版本不在已验收范围。
4. 新增真实 UIKit 应用、注册启动器、独立 RAM 测试镜像与日志检查器。镜像打包后逐文件核对
   内容、权限与所有者，并绑定源文件及构建回执；旧源码或错配镜像会被启动工具拒绝。

## 构建与启动

先按 [移植环境](LEMONADE.md) 配置本机基线、Docker 工具链、Metal/AIR 依赖与合法取得的
iOS 16.5 镜像。下面是在既有环境中使用本分支的命令；不会替换原 base 发布包。
这些功能最初在两个 `codex/lemonade-120hz` 分支验证，现已随历史合入单仓 `main`；源码来源见
[`sources.json`](../devices/oneplus-9/sources.json)。新工作区一次 clone 即包含全部源码，无需 `USERLAND_REF`。

```sh
docker exec hackios-lemonade-env bash -lc \
  'cd /work && JOBS=8 devices/oneplus-9/tools/lemonade-build-kernel.sh'
docker exec -e THEOS=/work/local/lemonade/toolchain/theos hackios-lemonade-env bash -lc \
  'cd /work && bash devices/oneplus-9/tools/lemonade-build-metal.sh && bash devices/oneplus-9/tools/lemonade-build-refresh-app.sh'
python3 devices/oneplus-9/tools/lemonade-build-graphics-image.py --system-metal
python3 devices/oneplus-9/tools/lemonade-build-refresh-image.py
```

取得设备独占租约，在原 Android 中核对面板后从 RAM 启动加载器。工具要求宿主直接访问 USB。

```sh
python3 devices/oneplus-9/tools/lemonade-usb.py panel
python3 devices/oneplus-9/tools/lemonade-usb.py boot
# 等 USB 加载器枚举完成，status 应显示 xnuloader
python3 devices/oneplus-9/tools/lemonade-usb.py status
python3 devices/oneplus-9/tools/lemonade-usb.py ramdisk --refresh-test
python3 devices/oneplus-9/tools/lemonade-usb.py run --profile performance \
  --display direct --display-hz 120 --display-fences --metal-system desktop \
  --refresh-test --metrics --touch on --seconds 100
python3 devices/oneplus-9/tools/lemonade-check-ios-refresh.py local/lemonade/<运行编号>-run-cp0.log
```

此验收入口会自动打开 Refresh 应用，完成两阶段动画，然后回到 SpringBoard 做滑动。
将 `--display-hz 120` 改为 `--display-hz 60` 即为对照；120 Hz 检查器不会把 60 Hz 当作通过。
每次加载器重启都必须重新上传对应的 System RAM 镜像。

运行普通桌面时可使用已有的 `--metal-system desktop` 图形镜像，省略 `--refresh-test`，
并用不带该选项的 `ramdisk` 上传原 System。120 Hz 的驱动和系统兼容路径不依赖测试应用；
该图形开发镜像仍带诊断作业，不是新的干净 base 发布包。完整可重复的帧率证据以上述验收入口
为准。返回 Android 后再核对：

```sh
python3 devices/oneplus-9/tools/lemonade-usb.py reboot
python3 devices/oneplus-9/tools/lemonade-verify-android.py
```

## 验证与边界

主机验证：54 项 USB 参数、身份与镜像来源测试；17 组显示恢复测试；5 组真实 C 的 DSI／
超时／中断恢复测试；5 项驱动验收器反例测试；5 项 iOS 验收器反例测试。C 测试使用
ASan/UBSan，内核、系统注入库和 UIKit 应用均已实际编译并用于上述真机运行。

这是每次启动选择的固定 60/120 Hz 模式。尚未验收运行中的设置切换、自适应 ProMotion、
长时间温控与续航、睡眠唤醒或 120 Hz 与 Wi-Fi 的联合负载；不能保证所有应用或复杂场景都
有 120 fps。应用自身需要允许高帧率，参考 Apple 的
[ProMotion 应用指南](https://developer.apple.com/documentation/quartzcore/optimizing-iphone-and-ipad-apps-to-support-promotion-displays)
与 [`CADisableMinimumFrameDurationOnPhone`](https://developer.apple.com/documentation/bundleresources/information-property-list/cadisableminimumframedurationonphone)。
当前测试应用启用了该键，并给 `CADisplayLink` 与 `CAAnimation` 设置了 120 的帧率范围。

私有证据归档：`local/lemonade/ios120-verified-20261024/`。两轮 120 Hz 日志编号为
`1792856790`、`1792856945`，60 Hz 对照为 `1792857178`；每轮包含最终日志、绑定哈希的
49 份实时快照和检查报告。Android 回归为 `android-roundtrip-20261024T235706.json`。
原始日志、设备身份、Apple 镜像与反汇编资料均不提交到公开仓库。
