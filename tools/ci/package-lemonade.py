#!/usr/bin/env python3
"""Package generic compiled components without system images or device material."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
# An allowlist, never a recursive copy of out/, local/, a rootfs or a firmware directory.
FILES = (
    'out/lemonade-kernel', 'out/lemonade-kernel.manifest.json', 'out/lemonade-build-inputs.json',
    'out/lemonade-base-runtime/init', 'out/lemonade-base-runtime/metal-enable',
    'out/lemonade-base-runtime/libredwood-safari-refresh.dylib', 'out/lemonade-base-runtime/build.json',
    'out/lemonade-bt-runtime/lemonade-bt-bootstrap',
    'out/lemonade-bt-runtime/liblemonade-bt-compat.dylib', 'out/lemonade-bt-runtime/build.json',
    'out/lemonade-metal/libredwoodmetal.dylib', 'out/lemonade-metal/libredwoodair.dylib',
    'out/lemonade-metal/libredwood.dylib', 'out/lemonade-metal/RedwoodMetal.bundle/RedwoodMetal',
    'out/lemonade-metal/RedwoodMetal.bundle/Info.plist', 'out/lemonade-metal/build.json',
    'userland/obj/libredwood-gl.dylib', 'userland/obj/libOSMesa.8.dylib',
)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_file(root, relative):
    path = root / relative
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Expected a regular build output: ' + relative)
    return path


def check_receipt(root, relative):
    directory = root / relative
    receipt = json.loads((directory / 'build.json').read_text())
    for source, expected in receipt['sources'].items():
        if digest(checked_file(root, source)) != expected:
            raise ValueError('Source does not match receipt: ' + source)
    for name, record in receipt['artifacts'].items():
        if digest(checked_file(directory, name)) != record['sha256']:
            raise ValueError('Binary does not match receipt: ' + name)


def check_outputs(root):
    for relative in FILES:
        checked_file(root, relative)
    kernel = json.loads((root / 'out/lemonade-kernel.manifest.json').read_text())
    if (kernel['sha256'] != digest(root / 'out/lemonade-kernel') or
            kernel.get('wifi_driver_profile') != 'qca6490-hw10-v1'):
        raise ValueError('Missing or mismatched QCA6490-enabled kernel')
    if kernel.get('battery_telemetry_supported') != 'bq28z610-uefi-cache-v2':
        raise ValueError('Missing base battery and charge-state protocol')
    for name, expected in json.loads((root / 'out/lemonade-build-inputs.json').read_text()).items():
        if digest(checked_file(root, 'xnu/files/' + name)) != expected:
            raise ValueError('Kernel source differs from build: ' + name)
    for relative in ('out/lemonade-metal', 'out/lemonade-base-runtime', 'out/lemonade-bt-runtime'):
        check_receipt(root, relative)
    for relative in FILES:
        if relative.endswith(('.json', '.plist')):
            continue
        data = (root / relative).read_bytes()
        if len(data) < 16 or struct.unpack_from('<II', data) != (0xfeedfacf, 0x100000c):
            raise ValueError('Not an ARM64 Mach-O: ' + relative)
    loader = checked_file(root, 'xnuloader/loader.efi').read_bytes()
    if loader[:2] != b'MZ' or b'lemonade-ci' not in loader:
        raise ValueError('Loader must be generic, without a per-phone USB identity')


def git(*args, userland=False):
    prefix = ['git'] if not userland else ['bash', 'common/ugit']
    return subprocess.check_output(prefix + list(args), cwd=ROOT, text=True).strip()



def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    check_outputs(ROOT)
    kernel = git('rev-parse', 'HEAD')
    userland = git('rev-parse', 'HEAD', userland=True)
    paired = json.loads((ROOT / 'common/devices/lemonade.sources.json').read_text())
    if userland != paired['userland']['commit']:
        raise SystemExit('The userland checkout is not the pinned revision')
    if git('status', '--porcelain', '--untracked-files=no') or git('status', '--porcelain', '--untracked-files=no', userland=True):
        raise SystemExit('Refusing to label modified tracked sources with a clean commit SHA')
    output = ROOT / 'out/ci-release'
    output.mkdir(parents=True, exist_ok=True)
    name = 'lemonade-ci-' + kernel[:12]
    folder = output / name
    folder.mkdir(exist_ok=False)
    for relative in FILES:
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        target.chmod(0o644)
    # Keep the generic EFI separately for development.
    (folder / 'loader').mkdir()
    shutil.copyfile(ROOT / 'xnuloader/loader.efi', folder / 'loader/loader.efi')
    (folder / 'sources').mkdir()
    for label, command in (('kernel', ['git']), ('userland', ['bash', 'common/ugit'])):
        subprocess.run(command + ['archive', '--format=tar.gz', '-o',
                       str(folder / 'sources' / (label + '.tar.gz')), 'HEAD'], cwd=ROOT, check=True)
    (folder / 'licenses').mkdir()
    for source, target in (
        ('LICENSE', 'project.txt'),
        ('xnu-src/xnu-8796.121.2/APPLE_LICENSE', 'xnu-apple.txt'),
        ('xnu-deps/build/ath11k/freebsd/COPYRIGHT', 'freebsd.txt'),
        ('xnu-deps/build/mesa/mesa-25.0.7/docs/license.rst', 'mesa.txt'),
    ):
        shutil.copyfile(checked_file(ROOT, source), folder / 'licenses' / target)
    # ath11k's source-level notices accompany the sources for the linked BSD driver.
    shutil.copytree(ROOT / 'xnu-deps/build/ath11k/src-lemonade', folder / 'sources/ath11k')
    shutil.copyfile(ROOT / 'docs/LEMONADE-CI.md', folder / 'README.md')
    files = {p.relative_to(folder).as_posix(): {'sha256': digest(p), 'bytes': p.stat().st_size}
             for p in sorted(folder.rglob('*')) if p.is_file()}
    manifest = {
        'profile': 'lemonade-public-build-v1', 'kernel_commit': kernel, 'userland_commit': userland,
        'target': 'OnePlus 9 lemonade / ARM64 components', 'build_host': platform.machine(),
        'run_url': os.environ.get('LEMONADE_CI_RUN_URL', ''),
        'hardware_verified': False, 'bootable_image_included': False,
        'apple_system_included': False, 'device_firmware_included': False,
        'wifi_credentials_included': False, 'wifi_history_included': False,
        'persistent_user_data_included': False, 'partition_writes': False,
        'files': files,
    }
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    archive = output / (name + '.tar.gz')
    def public_metadata(info):
        info.uid = info.gid = 0
        info.uname = info.gname = 'root'
        return info
    with tarfile.open(archive, mode='w:gz', compresslevel=1) as package:
        package.add(folder, arcname=name, filter=public_metadata)
    shutil.copyfile(folder / 'manifest.json', output / 'manifest.json')
    shutil.copyfile(ROOT / 'docs/LEMONADE-CI.md', output / 'README.md')
    assets = [archive, output / 'manifest.json', output / 'README.md']
    (output / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + p.name + '\n' for p in assets))
    shutil.rmtree(folder)
    print(f'Generic kernel/driver package: {archive.name} ({archive.stat().st_size} bytes)')



if __name__ == '__main__':
    main()
