#!/usr/bin/env python3
"""Launch this device-specific base from RAM, or return to Android."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

HERE = Path(__file__).resolve().parent
BASE_ARGUMENTS = ('sb_delay=8 bootcpu=7 cpus=4 cpufreq=1 ddr=0 gpu=1 gpu_log=0 '
    'metal=1 metal_profile=1 camtl=1 lemonade_display=10 ca_vbl=1 fb_fences=1 '
    'touchscreen=1 buttons=0 wifi=0 lemonade_wifi=24 lemonade_runtime=1 lemonade_dma_verified=1 '
    'adsp=0 ufs=0 ufs_native=0 ufs_write=0 var_store=0').split()


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def command(argv, timeout=30, check=True):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise SystemExit('操作未完成：' + Path(argv[0]).name + '（设备状态或连接发生变化）')
    return result.stdout + result.stderr


def verify(folder=HERE):
    manifest = json.loads((folder / 'manifest.json').read_text())
    if manifest.get('profile') != 'lemonade-base-v1':
        raise SystemExit('不是 OnePlus 9 base 发布包')
    if manifest.get('boot_arguments') != BASE_ARGUMENTS:
        raise SystemExit('启动配置与此 RAM base 不匹配')
    required = {'boot.img', 'kernel', 'root.apfs', 'system.apfs', 'launch.py', 'mkpayload.py'}
    if not required.issubset(manifest.get('files', {})):
        raise SystemExit('发布包文件清单不完整')
    for name, entry in manifest['files'].items():
        path = folder / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.is_file():
            raise SystemExit('发布包中存在无效文件')
        if path.stat().st_size != entry['bytes'] or digest(path) != entry['sha256']:
            raise SystemExit('发布包校验失败：' + name)
    if (not manifest.get('dma_guard_verified') or manifest.get('dma_guard_kernel_sha256') != manifest['files']['kernel']['sha256']):
        raise SystemExit('此候选尚未完成对应内核的 Wi-Fi DMA 验证')
    if (not manifest.get('integration_verified') or
            manifest.get('integration_kernel_sha256') != manifest['files']['kernel']['sha256'] or
            manifest.get('integration_root_sha256') != manifest['files']['root.apfs']['sha256']):
        raise SystemExit('此候选尚未通过 GPU、显示与 Wi-Fi 联合验收')
    return manifest


def adb_target(manifest):
    expected = manifest['hardware_serial_sha256']
    for line in command(['adb', 'devices', '-l'], check=False).splitlines()[1:]:
        parts = line.split()
        usb = len(parts) >= 3 and (parts[2].startswith('usb:') or re.fullmatch(r'\d+-\d+(?:\.\d+)*', parts[2]))
        if usb and parts[1] == 'device' and hashlib.sha256(parts[0].encode()).hexdigest() == expected:
            return parts[0]
    return None


def fastboot_target(manifest):
    expected = manifest['hardware_serial_sha256']
    loader = 'lemonade-' + expected[:16]
    for line in command(['fastboot', 'devices'], check=False).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == 'fastboot' and (parts[0] == loader or hashlib.sha256(parts[0].encode()).hexdigest() == expected):
            return parts[0]
    return None


def fb(serial, *args, timeout=30):
    return command(['fastboot', '-s', serial, *map(str, args)], timeout)


def product(serial):
    text = fb(serial, 'getvar', 'product')
    for line in text.splitlines():
        if 'product:' in line:
            return line.split('product:', 1)[1].strip()
    return None


def loader(manifest):
    serial = fastboot_target(manifest)
    if not serial or product(serial) != 'xnuloader':
        raise SystemExit('目标手机当前没有运行 base 加载器')
    return serial


def wait_for(function, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(1)
    return None


def start(manifest):
    serial = fastboot_target(manifest)
    if not serial:
        android = adb_target(manifest)
        if not android:
            raise SystemExit('请用 USB 连接这台 OnePlus 9，并在 Android 中允许 USB 调试')
        for key, expected in (('ro.product.model', 'LE2110'), ('ro.soc.model', 'SM8350'),
                              ('ro.boot.flash.locked', '0')):
            value = command(['adb', '-s', android, 'shell', 'getprop', key]).strip()
            if value != expected:
                raise SystemExit('设备机型或引导状态与发布包不匹配')
        print('已核对目标手机，进入引导模式…', flush=True)
        command(['adb', '-s', android, 'reboot', 'bootloader'])
        serial = wait_for(lambda: fastboot_target(manifest), 60)
        if not serial:
            raise SystemExit('USB 引导接口未出现；请手动重启手机后重试。未发送启动镜像。')
    kind = product(serial)
    if kind != 'xnuloader':
        if kind not in ('lemonade', 'OnePlus9', 'LE2110', 'lahaina'):
            raise SystemExit('引导设备机型不匹配')
        print('临时启动加载器…', flush=True)
        fb(serial, 'boot', HERE / 'boot.img', timeout=60)
        serial = wait_for(lambda: (s if (s := fastboot_target(manifest)) and product(s) == 'xnuloader' else None), 40)
        if not serial or product(serial) != 'xnuloader':
            raise SystemExit('加载器 USB 接口尚未就绪')
    if 'running' in fb(serial, 'getvar', 'result'):
        raise SystemExit('base 已在运行；重新启动前请先退出')
    print('上传系统到手机内存，约需两分钟…', flush=True)
    fb(serial, 'oem', 'ramdisk')
    fb(serial, 'stage', HERE / 'system.apfs', timeout=300)
    args = manifest['boot_arguments'] + ['epoch=' + str(int(time.time()))]
    with tempfile.TemporaryDirectory(prefix='lemonade-base-') as temporary:
        payload = Path(temporary) / 'boot.bin'
        command(['python3', str(HERE / 'mkpayload.py'), '-o', str(payload),
                 'kernel=' + str(HERE / 'kernel'), 'ramdisk=' + str(HERE / 'root.apfs'),
                 '--cmdline', ' '.join(args)], timeout=60)
        if payload.stat().st_size > 0x03f00000:
            raise SystemExit('启动载荷超出加载器限制')
        fb(serial, 'stage', payload, timeout=60)
    fb(serial, 'oem', 'run', '0')
    print('base 已启动。没有自动测试、手势或定时退出；等待桌面出现后直接操作手机。')


def stop(manifest, android=False):
    serial = loader(manifest)
    result = fb(serial, 'getvar', 'result')
    if 'running' in result:
        fb(serial, 'oem', 'stop')
        result = wait_for(lambda: (value if 'returned' in (value := fb(serial, 'getvar', 'result')) else None), 45)
        if not result:
            raise SystemExit('系统尚未正常退出，请保留连接并检查状态')
    if android:
        fb(serial, 'reboot')
        serial = wait_for(lambda: adb_target(manifest), 90)
        if serial and wait_for(lambda: command(['adb', '-s', serial, 'shell', 'getprop', 'sys.boot_completed']).strip() == '1', 60):
            print('原 Android 已完成启动。')
        else:
            raise SystemExit('已请求返回 Android，但尚未确认启动完成')
    else:
        print('base 已退出到加载器。')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'android', 'status', 'verify', 'log'))
    args = parser.parse_args()
    manifest = verify()
    if args.action == 'start':
        start(manifest)
    elif args.action in ('stop', 'android'):
        stop(manifest, args.action == 'android')
    elif args.action == 'status':
        serial = fastboot_target(manifest)
        if serial and product(serial) == 'xnuloader':
            print(fb(serial, 'getvar', 'result'))
        else:
            print('Android USB 在线' if adb_target(manifest) else '目标设备 USB 不在线')
    elif args.action == 'log':
        serial = loader(manifest)
        folder = HERE / 'logs'
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / (time.strftime('%Y%m%d-%H%M%S') + '.log')
        fb(serial, 'oem', 'log')
        fb(serial, 'get_staged', path, timeout=60)
        print('运行日志已保存在：' + str(path))
    else:
        print(manifest['version'] + '：所有发布文件校验通过')


if __name__ == '__main__':
    main()
