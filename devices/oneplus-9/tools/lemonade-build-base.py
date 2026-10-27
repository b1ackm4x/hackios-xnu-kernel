#!/usr/bin/env python3
"""Assemble a private, credential-free OnePlus 9 runtime base."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'devices/oneplus-9/tools' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


base = module('base_image', 'lemonade-build-wifi-image.py')
graphics = module('graphics_image', 'lemonade-build-graphics-image.py')
launch = module('base_launch', 'lemonade-base-launch.py')
usb = module('base_usb', 'lemonade-usb.py')
digest = base.digest
RUNTIME_METAL = ('libredwoodmetal.dylib', 'libredwoodair.dylib', 'libredwood.dylib',
                 'RedwoodMetal.bundle/RedwoodMetal', 'RedwoodMetal.bundle/Info.plist')
REMOVED_JOBS = ('com.redwood.demo', 'com.redwood.wifi', 'com.redwood.varstore')
DEBUG_ENV = ('REDWOOD_METAL_TRACE', 'OS_ACTIVITY_DT_MODE', 'CA_ASSERT_MAIN_THREAD_TRANSACTIONS')
JAILBREAK_JOB = 'com.lemonade.jailbreak-preparation'
DESKTOP_SERVICES = {
    'com.apple.geod': ('/System/Library/PrivateFrameworks/GeoServices.framework/geod', 'com.apple.geod'),
    'com.apple.navd': ('/System/Library/PrivateFrameworks/MapsSupport.framework/navd', 'com.apple.navigationService'),
    'com.apple.sleepd': ('/System/Library/PrivateFrameworks/SleepDaemon.framework/sleepd', 'com.apple.sleepd.sleepserver'),
}


def jailbreak_services(system):
    """Prepare and register a present rootless bootstrap on every fresh RAM boot."""
    container = base.apfs.Container(str(system))
    try:
        volume = base.apfs.pick_volume(container, None)
        if not volume.lookup('/jb'):
            return {}, None
        if volume.lookup('/Applications/Sileo.app'):
            raise ValueError('System contains a conflicting second Sileo bundle')
        required = {'/private/preboot/jb-rw': 0o40755, '/jb/prep.sh': 0o100755,
                    '/jb/usr/bin/login': 0o104755,
                    '/jb/Applications/Sileo.app/giveMeRoot': 0o104755}
        for path, mode in required.items():
            entry = volume.lookup(path)
            inode = volume.inode(entry[0]) if entry else None
            if inode is None or (inode.mode, inode.owner, inode.group) != (mode, 0, 0):
                raise ValueError('Invalid rootless bootstrap path or permissions: ' + path)
        entry = volume.lookup('/jb/Applications/Sileo.app/Info.plist')
        if not entry:
            raise ValueError('Sileo bundle metadata is missing')
        info = plistlib.loads(volume.inode(entry[0]).contents())
        if info.get('CFBundleIdentifier') != 'org.coolstar.SileoStore':
            raise ValueError('Unexpected Sileo bundle identity')
        prep = volume.inode(volume.lookup('/jb/prep.sh')[0]).contents()
        # prep.sh starts bounded registration retries in the background. Let
        # them finish if the parent exits before SpringBoard becomes ready.
        job = {'Label': JAILBREAK_JOB, 'RunAtLoad': True, 'AbandonProcessGroup': True,
               'ProgramArguments': ['/var/jb/bin/sh', '/var/jb/prep.sh'],
               'StandardOutPath': '/dev/console', 'StandardErrorPath': '/dev/console'}
        return {'LaunchDaemons/' + JAILBREAK_JOB + '.plist':
                (0o100644, 0, 0, plistlib.dumps(job, fmt=plistlib.FMT_BINARY))}, {
                    'bundle_identifier': info['CFBundleIdentifier'],
                    'version': info.get('CFBundleShortVersionString'),
                    'canonical_prefix': '/private/preboot/jb-rw/procursus',
                    'prep_sha256': hashlib.sha256(prep).hexdigest(),
                    'automatic_registration': True, 'persistent_installs': False}
    finally:
        container.close()


def desktop_services():
    """Register actual stock providers requested by SpringBoard and widgets."""
    system = ROOT / 'userland/system.apfs'
    expected = json.loads((ROOT / 'local/lemonade/images.json').read_text())['userland/system.apfs']['sha256']
    if digest(system) != expected:
        raise SystemExit('Normal System image differs from its verified manifest')
    container = base.apfs.Container(str(system))
    entries, receipts = {}, {}
    try:
        volume = base.apfs.pick_volume(container, None)
        for name, (program, service) in DESKTOP_SERVICES.items():
            source_path = ROOT / 'sysimage/rootfs/System/Library/LaunchDaemons' / (name + '.plist')
            source = source_path.read_bytes()
            job = plistlib.loads(source)
            executable = volume.lookup(program)
            if not executable:
                raise SystemExit('Stock desktop service is missing from System: ' + name)
            program_hash = hashlib.sha256(volume.inode(executable[0]).contents()).hexdigest()
            if digest(ROOT / 'sysimage/rootfs' / program.lstrip('/')) != program_hash:
                raise SystemExit('Unpacked service differs from verified System: ' + name)
            if (job.get('Label') != name or job.get('ProgramArguments') != [program] or
                    job.get('Program', program) != program or job.get('UserName') != 'mobile' or
                    service not in job.get('MachServices', {})):
                raise SystemExit('Unexpected stock desktop service identity: ' + name)
            # Preserve stock MachServices, executable and user. Launch on demand
            # without eager background work or unattended crash-restart loops.
            for key in ('KeepAlive', 'RunAtLoad', 'LaunchEvents'):
                job.pop(key, None)
            job['ThrottleInterval'] = 30
            job['StandardOutPath'] = job['StandardErrorPath'] = '/dev/console'
            data = plistlib.dumps(job, fmt=plistlib.FMT_BINARY)
            entries['LaunchDaemons/' + name + '.plist'] = (0o100644, 0, 0, data)
            receipts[name] = {'program': program, 'system_sha256': expected,
                'stock_plist_source': str(source_path.relative_to(ROOT)),
                'stock_plist_sha256': hashlib.sha256(source).hexdigest(),
                'program_sha256': program_hash, 'job_sha256': hashlib.sha256(data).hexdigest()}
        return entries, receipts
    finally:
        container.close()


def checked_receipt(directory):
    receipt = json.loads((directory / 'build.json').read_text())
    for relative, expected in receipt['sources'].items():
        if digest(ROOT / relative) != expected:
            raise SystemExit('Source differs from runtime receipt: ' + relative)
    for relative, expected in receipt['artifacts'].items():
        if digest(directory / relative) != expected['sha256']:
            raise SystemExit('Binary differs from runtime receipt: ' + relative)
    return receipt


def validate_tree(tree):
    binaries = {p for p in tree if p.startswith(('bin/', 'sbin/'))}
    if binaries != {'bin/metal-enable', 'sbin/init', 'bin/lemonade-bt-bootstrap'}:
        raise ValueError('Base contains unexpected helper/test executables')
    if 'etc/rc.jobs' in tree or 'share/pattern.bin' in tree or 'fw/lemonade-network' in tree:
        raise ValueError('Base contains a test workload or private network input')
    if tree['etc/rc'][3] != b'/redwood/bin/metal-enable\nexec /sbin/launchd.ios\n':
        raise ValueError('Base boot does not directly hand over to launchd')
    for name in ('amss.bin', 'm3.bin', 'board.bin', 'regdb.bin'):
        if 'fw/' + name not in tree:
            raise ValueError('Missing QCA6490 runtime firmware')
    for relative, (mode, uid, gid, data) in tree.items():
        if relative.endswith(('.metallib', '.log')) or 'lemonade-net-test' in relative or 'lemonade-wifi-ui' in relative:
            raise ValueError('Diagnostic artifact in base')
        if relative.startswith('LaunchDaemons/') and stat.S_ISREG(mode):
            job = plistlib.loads(data)
            if job.get('Label') in REMOVED_JOBS or any(k in job.get('EnvironmentVariables', {}) for k in DEBUG_ENV):
                raise ValueError('Test job or tracing environment in base')
            program = job.get('Program') or job.get('ProgramArguments', [''])[0]
            if program.startswith('/redwood/') and not (
                    relative == 'LaunchDaemons/org.lemonade.bluetooth-bootstrap.plist' and
                    job.get('Label') == 'org.lemonade.bluetooth-bootstrap' and
                    job.get('ProgramArguments') == ['/redwood/bin/lemonade-bt-bootstrap'] and
                    job.get('MachServices') == {'com.apple.BlueTool': True}):
                raise ValueError('Unwanted automatic helper in base: ' + program)
    for name in ('hpbtfw10.tlv', 'hpnv10.bin', 'bt-address'):
        if 'fw/' + name not in tree or tree['fw/' + name][:3] != (0o100600, 0, 0):
            raise ValueError('Missing or incorrectly protected Bluetooth input')
    if len(tree['fw/bt-address'][3]) != 6:
        raise ValueError('Invalid private Bluetooth address')
    injection = tree.get('etc/inject.conf', (0, 0, 0, b''))[3]
    if b'/redwood/lib/liblemonade-bt-compat.dylib bluetoothd' not in injection.splitlines():
        raise ValueError('Native Bluetooth compatibility is missing')
    if any('bt-trace' in p or 'bt-ui-trace' in p or 'bt-client' in p for p in tree):
        raise ValueError('Bluetooth diagnostic artifact in base')
    if b'bt-trace' in injection or b'bt-ui-trace' in injection:
        raise ValueError('Bluetooth diagnostic injection in base')
    for name in ('com.apple.bluetoothd', 'com.apple.bluetoothuserd', 'com.apple.nanoregistryd',
                 'com.apple.nanoregistrylaunchd', 'org.lemonade.bluetooth-bootstrap'):
        if 'LaunchDaemons/' + name + '.plist' not in tree:
            raise ValueError('Missing native Bluetooth service: ' + name)
    for name in ('com.apple.wifid', 'com.apple.configd', 'com.apple.mDNSResponder', 'com.apple.chronod', *DESKTOP_SERVICES):
        if 'LaunchDaemons/' + name + '.plist' not in tree:
            raise ValueError('Missing runtime service: ' + name)


def pack_tree(entries, destination):
    with tempfile.TemporaryDirectory(prefix='pack-', dir=destination.parent) as temporary:
        work = Path(temporary)
        tree = work / 'root'
        tree.mkdir()
        metadata = []
        for relative, (mode, uid, gid, data) in sorted(entries.items()):
            path = tree / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISDIR(mode):
                path.mkdir(exist_ok=True)
            elif stat.S_ISLNK(mode):
                path.symlink_to(os.fsdecode(data))
            else:
                path.write_bytes(data)
            if not stat.S_ISLNK(mode):
                path.chmod(stat.S_IMODE(mode))
            metadata.append(f'{mode:o}\t{uid}\t{gid}\t{relative}\n')
        description = work / 'metadata.tsv'
        description.write_text(''.join(metadata))
        candidate = work / 'root.apfs'
        subprocess.run(['python3', str(ROOT / 'tools/apfs/mkapfs.py'), '-z', '--plain', 'fw',
                        '-o', str(candidate), f'redwood={tree},manifest={description}'],
                       stdout=subprocess.DEVNULL, check=True)
        if base.contents(candidate) != entries:
            raise SystemExit('APFS readback differs in contents, ownership, mode or links')
        shutil.copy2(candidate, destination)


def checksums(folder):
    manifest = json.loads((folder / 'manifest.json').read_text())
    names = sorted(set(manifest['files']) | {'manifest.json'})
    for name in names:
        path = folder / name
        if Path(name).name != name or path.is_symlink() or not path.is_file():
            raise ValueError('Invalid release file: ' + name)
    (folder / 'SHA256SUMS').write_text(''.join(digest(folder / n) + '  ' + n + '\n' for n in names))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default=launch.BASE_VERSION)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9][0-9A-Za-z.-]{0,55}', args.version):
        parser.error('Use a simple version identifier')
    destination = ROOT / 'out/releases' / ('lemonade-' + args.version)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    images = json.loads((ROOT / 'local/lemonade/images.json').read_text())
    baseline = json.loads((ROOT / 'local/lemonade/baseline.json').read_text())
    panel = usb.verified_refresh_panel(baseline['hardware_serial_sha256'])
    for name in ('userland/root.apfs', 'userland/system.apfs'):
        if digest(ROOT / name) != images[name]['sha256']:
            raise SystemExit('Normal base image differs from its manifest')
    runtime_dir, metal_dir = ROOT / 'out/lemonade-base-runtime', ROOT / 'out/lemonade-metal'
    runtime, metal = checked_receipt(runtime_dir), checked_receipt(metal_dir)
    kernel = json.loads((ROOT / 'out/lemonade-kernel.manifest.json').read_text())
    if kernel.get('display_refresh_supported') != 'amb655x-measured-desktop-v1':
        raise SystemExit('Kernel lacks the measured 120 Hz desktop path')
    if kernel.get('battery_telemetry_supported') != launch.BATTERY_TELEMETRY:
        raise SystemExit('Kernel lacks the base battery and charge-state protocol')
    if digest(ROOT / 'out/lemonade-kernel') != kernel['sha256']:
        raise SystemExit('Kernel differs from its manifest')
    inputs = json.loads((ROOT / 'out/lemonade-build-inputs.json').read_text())
    for relative, expected in inputs.items():
        if digest(ROOT / 'xnu/files' / relative) != expected:
            raise SystemExit('Kernel source differs from build: ' + relative)
    if b'lemonade_runtime' not in (ROOT / 'xnu/files/bsd/dev/lemonade_wifi.c').read_bytes():
        raise SystemExit('Kernel lacks the integrated station entry point')
    before = base.contents(ROOT / 'userland/root.apfs')
    tree = {p: v for p, v in before.items() if not p.startswith('bin/') and
            p not in {'etc/rc.jobs', 'share/pattern.bin', *('LaunchDaemons/' + n + '.plist' for n in REMOVED_JOBS)}}
    tree['sbin/init'] = (0o100755, 0, 0, (runtime_dir / 'init').read_bytes())
    tree['bin/metal-enable'] = (0o100755, 0, 0, (runtime_dir / 'metal-enable').read_bytes())
    tree['lib/libredwood-sb.dylib'] = (0o100755, 0, 0, (runtime_dir / 'libredwood-sb.dylib').read_bytes())
    tree['lib/libredwood-safari-refresh.dylib'] = (0o100755, 0, 0,
        (runtime_dir / 'libredwood-safari-refresh.dylib').read_bytes())
    mode, uid, gid, injection = tree['etc/inject.conf']
    tree['etc/inject.conf'] = (mode, uid, gid,
        injection.rstrip() + b'\n/redwood/lib/libredwood-safari-refresh.dylib MobileSafari\n')
    tree['etc/rc'] = (0o100644, 0, 0, b'/redwood/bin/metal-enable\nexec /sbin/launchd.ios\n')
    tree['etc/motd'] = (0o100644, 0, 0, ('OnePlus 9 base ' + args.version + '\n').encode())
    tree['lib/RedwoodMetal.bundle'] = (0o40755, 0, 0, b'')
    for name in RUNTIME_METAL:
        tree['lib/' + name] = (0o100644 if name.endswith('.plist') else 0o100755, 0, 0, (metal_dir / name).read_bytes())
    tree.update(base.network_services(browser=True))
    widgets, widget_receipt = graphics.widget_services()
    tree.update(widgets)
    desktop, desktop_receipts = desktop_services()
    tree.update(desktop)
    jailbreak, jailbreak_receipt = jailbreak_services(ROOT / 'userland/system.apfs')
    tree.update(jailbreak)
    for p, (mode, uid, gid, data) in list(tree.items()):
        if p.startswith('LaunchDaemons/') and stat.S_ISREG(mode):
            job = plistlib.loads(data)
            for key in DEBUG_ENV:
                job.get('EnvironmentVariables', {}).pop(key, None)
            tree[p] = (mode, uid, gid, plistlib.dumps(job, fmt=plistlib.FMT_BINARY))
    firmware_dir = ROOT / 'local/lemonade/wifi/firmware'
    firmware = json.loads((firmware_dir / 'manifest.json').read_text())
    selected = {}
    if firmware.get('device') != 'lemonade' or not firmware.get('usb_identity_verified'):
        raise SystemExit('Firmware device identity is unverified')
    for name in ('amss.bin', 'm3.bin', 'board.bin', 'regdb.bin'):
        entry = firmware['files'][name]
        if not entry['matches_device'] or digest(firmware_dir / name) != entry['sha256']:
            raise SystemExit('Original radio firmware differs: ' + name)
        tree['fw/' + name] = (0o100644, 0, 0, (firmware_dir / name).read_bytes())
        selected[name] = {'sha256': entry['sha256'], 'bytes': entry['bytes']}
    bluetooth = module('base_bluetooth', 'lemonade-build-bt-firmware.py')
    tree, bluetooth_receipt = bluetooth.runtime_entries(tree, ROOT / 'userland/system.apfs',
        baseline['hardware_serial_sha256'], argparse.Namespace(native=True, board_bootstrap=True,
            board_compat=True, native_trace=False, native_client=False, settings_launch=False, ui_trace=False))
    validate_tree(tree)
    pack_tree(tree, destination / 'root.apfs')
    boot = json.loads((ROOT / 'out/lemonade-boot.manifest.json').read_text())
    if boot.get('battery_telemetry_supported') != launch.BATTERY_TELEMETRY:
        raise SystemExit('Rebuild the loader for the base battery and charge-state protocol')
    if (digest(ROOT / 'out/lemonade-boot.img') != boot['sha256'] or
            boot.get('dtb_sha256') != panel['dtb_sha256']):
        raise SystemExit('Loader boot image differs from its manifest or verified panel DTB')
    for source, target in [('out/lemonade-boot.img', 'boot.img'), ('out/lemonade-kernel', 'kernel'),
                           ('userland/system.apfs', 'system.apfs'), ('devices/oneplus-9/tools/lemonade-base-launch.py', 'launch.py'),
                           ('xnuloader/mkpayload.py', 'mkpayload.py')]:
        shutil.copy2(ROOT / source, destination / target)
    for name, action in (('Start.command', 'start'), ('Return-to-Android.command', 'android')):
        script = '#!/bin/bash\nset -e\ncd "$(dirname "$0")"\nexec python3 launch.py ' + action + '\n'
        (destination / name).write_text(script)
        (destination / name).chmod(0o700)
    readme = f'''# OnePlus 9 Base {args.version}

现阶段功能基线：Adreno 660 GLES/Metal、SpringBoard 桌面、小组件、默认 120 Hz DPU 直接显示、实体触控、QCA6490 Wi-Fi、系统网络服务，真实电量和充电状态显示，以及原生蓝牙发现。

本版本包含异步 Metal 提交、原生 Core Animation 帧时钟，以及双缓冲 BGRA 多层合成：下一帧合成与上一帧显示传输重叠，完成通知和退出排空仍等待真实硬件完成。Safari 在首次使用时关闭“优先 60 fps”的网页策略，之后可在 Safari 设置中改回；只在驱动实测 120 Hz 的 RAM 环境启用。具体真机验证结果记录在 manifest.json；应用实际帧率取决于负载。

## 使用

Mac 安装好 Python 3.11+、adb 和 fastboot，将这台 LE2110 通过 USB 接入并允许 USB 调试，双击 `Start.command`。首次上传系统到内存约两分钟，随后直接进入桌面。返回原 Android 双击 `Return-to-Android.command`。

此包没有开机自检、图形/网络测试程序、自动手势、调试录制或定时退出。启动只用 RAM，不刷写分区、不改槽。Wi-Fi 密码、账号和先前测试的网络信息没有打入包内。`python3 launch.py verify` 可检查所有运行文件。

## 当前范围

这是现阶段 base，并非所有功能都完成的手机系统。默认请求 120 Hz，只有面板测量通过才向系统发布该模式；应用可使用最高 120 fps，实际帧率仍取决于应用和负载。自适应刷新率切换、完整 Metal 兼容、睡眠唤醒、长期温控、电源管理和未移植外设仍待后续版本。当前数据卷在 RAM 中，重启后设置和会话不保留；蜂窝通信等未列入本次功能基线。详细验证状态在 `manifest.json`。旧的 `2026.10.24-base.1` 保留为 60 Hz 回退包。

Wi-Fi 驱动与系统网络服务已集成；联网验收使用临时验证副本覆盖关联、地址获取、DNS、HTTP 和断线重连。正式包不包含该联网程序或网络配置；原生设置中首次输入密码的完整流程尚未单独验收。

蓝牙使用本机控制器和原生“设置 → 蓝牙”页面。启动器会在 Android 中暂时关闭蓝牙以完成冷启动，正常返回 Android 时恢复原设置。再次启动时，启动器会先返回 Android 后重新进行冷启动；当前不复用已经运行过系统的 RAM 加载器。蓝牙配对、音频及外设数据仍未验收。

Sileo 在开机时准备并注册 rootless 软件环境，图标可能稍后出现。软件安装在 RAM 中，重启后不保留。已修复安装触发图标缓存更新时退出以及 bootstrap 特殊权限丢失的问题；本包不自动安装测试应用。

电池数据每 5 秒从原厂电量计读取，交由 iOS 原生电源服务显示；仅采集状态，不操作充电启停或快充。单独实测已确认真实电量及接电满电显示；物理 USB 拔插后日志通道重连失败，拔插状态切换和未满电充电图标尚未完成验收。采样中断时内核会撤回过期读数，但 powerd 仍可能保留最后值。

原作者为 hackios 项目的 oosexgang；本包是 b1ackm4x 的 OnePlus 9 适配版本。它包含本机系统与固件材料，仅留在本机使用，不作为可公开转发的固件下载包。源代码分别维护在 hackios-xnu-kernel 与 hackios-xnu-userland 公开仓库。
'''
    (destination / 'README.md').write_text(readme)
    manifest = {'profile': 'lemonade-base-v1', 'version': args.version, 'device': 'OnePlus 9 LE2110',
        'hardware_serial_sha256': baseline['hardware_serial_sha256'], 'boot_arguments': launch.BASE_ARGUMENTS,
        'default_display_hz': 120, 'refresh_panel': panel,
        'battery_telemetry_supported': launch.BATTERY_TELEMETRY,
        'charger_control': False,
        'display_return_drain': True,
        'direct_refresh_recovery': True,
        'direct_multilayer_composite': True, 'gpu_return_call_drain': True,
        'direct_multilayer_pipeline': True,
        'automatic_tests': False, 'automatic_gestures': False, 'boot_self_tests': False,
        'debug_recording': False, 'timed_exit': False, 'credentials_included': False,
        'partition_writes': False, 'persistent_user_data': False,
        'dma_guard_verified': False, 'integration_verified': False,
        'base_root_sha256': images['userland/root.apfs']['sha256'], 'kernel_inputs': inputs,
        'runtime_build': runtime, 'metal_runtime': {n: metal['artifacts'][n] for n in RUNTIME_METAL},
        'bluetooth': bluetooth_receipt, 'bluetooth_discovery_verified': False,
        'bluetooth_coexistence_verified': False,
        'wifi_firmware': selected, 'widget_service': {k:v for k,v in widget_receipt.items() if k!='job_sha256'},
        'desktop_services': desktop_receipts,
        'jailbreak_bootstrap': jailbreak_receipt,
        'root_file_inventory': {p: {'mode': oct(v[0]), 'uid': v[1], 'gid': v[2], 'sha256': hashlib.sha256(v[3]).hexdigest()}
                                for p,v in sorted(tree.items())},
        'files': {p.name: {'sha256': digest(p), 'bytes': p.stat().st_size} for p in sorted(destination.iterdir()) if p.is_file()}}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    checksums(destination)
    if digest(ROOT / 'userland/root.apfs') != images['userland/root.apfs']['sha256']:
        raise SystemExit('Normal root changed during packaging')
    print(json.dumps({'release': str(destination), 'root_files': len(tree), 'root_bytes': (destination/'root.apfs').stat().st_size,
                      'credentials_included': False, 'integration_verified': False}, indent=2))


if __name__ == '__main__':
    main()
