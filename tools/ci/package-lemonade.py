#!/usr/bin/env python3
"""Assemble a complete, credential-free RAM runtime from fresh compiled outputs."""
import hashlib
import gzip
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
    for name, expected in json.loads((root / 'out/lemonade-build-inputs.json').read_text()).items():
        if digest(checked_file(root, 'xnu/files/' + name)) != expected:
            raise ValueError('Kernel source differs from build: ' + name)
    for relative in ('out/lemonade-metal', 'out/lemonade-base-runtime'):
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


launch = module('public_launch', 'tools/lemonade-public-launch.py')


class SplitWriter:
    """Stream an archive into assets below GitHub's 2 GiB per-file limit."""
    def __init__(self, output, name, limit=1500 * 1024 * 1024):
        self.output, self.name, self.limit = output, name, limit
        self.paths, self.stream, self.size = [], None, 0

    def write(self, data):
        total = len(data)
        while data:
            if self.stream is None or self.size == self.limit:
                if self.stream:
                    self.stream.close()
                path = self.output / f'{self.name}.part-{len(self.paths):02d}'
                self.paths.append(path)
                self.stream, self.size = path.open('wb'), 0
            count = min(len(data), self.limit - self.size)
            self.stream.write(data[:count])
            self.size += count
            data = data[count:]
        return total

    def close(self):
        if self.stream:
            self.stream.close()


def assemble_runtime(folder):
    base = module('public_base', 'tools/lemonade-build-base.py')
    inputs = ROOT / '.cache/lemonade-runtime/runtime-inputs'
    receipt = json.loads((inputs / 'inputs.json').read_text())
    if receipt['userland_commit'] != git('rev-parse', 'HEAD', userland=True):
        raise SystemExit('Refresh runtime template when changing the paired userland revision')
    for name, expected in receipt['files'].items():
        if digest(checked_file(inputs, name)) != expected['sha256']:
            raise SystemExit('Runtime template file changed: ' + name)
    tree = base.base.contents(inputs / 'root.apfs')
    actual = {p: {'mode': mode, 'uid': uid, 'gid': gid, 'sha256': hashlib.sha256(data).hexdigest()}
              for p, (mode, uid, gid, data) in tree.items()}
    if actual != receipt['root_inventory']:
        raise SystemExit('Root template inventory differs')
    replacements = {
        'sbin/init': 'out/lemonade-base-runtime/init',
        'bin/metal-enable': 'out/lemonade-base-runtime/metal-enable',
        'lib/libredwood-safari-refresh.dylib': 'out/lemonade-base-runtime/libredwood-safari-refresh.dylib',
        'lib/libOSMesa.8.dylib': 'userland/obj/libOSMesa.8.dylib',
        'lib/libredwood-gl.dylib': 'userland/obj/libredwood-gl.dylib',
    }
    replacements.update({'lib/' + name: 'out/lemonade-metal/' + name for name in base.RUNTIME_METAL})
    for name, source in replacements.items():
        mode, uid, gid, old = tree[name]
        tree[name] = (mode, uid, gid, (ROOT / source).read_bytes())
    tree['etc/motd'] = (0o100644, 0, 0, b'OnePlus 9 experimental CI runtime\n')
    base.validate_tree(tree)
    base.pack_tree(tree, folder / 'root.apfs')
    system = inputs.parent / 'system.apfs'
    if digest(system) != receipt['system_sha256']:
        raise SystemExit('Clean system image changed')
    os.link(system, folder / 'system.apfs')
    for source, target in [('out/lemonade-kernel', 'kernel'), ('tools/lemonade-public-launch.py', 'launch.py'),
                           ('xnuloader/mkpayload.py', 'mkpayload.py')]:
        shutil.copyfile(ROOT / source, folder / target)
    payload = folder / 'loader-payload.tmp'
    subprocess.run(['python3', str(ROOT / 'xnuloader/mkpayload.py'), '-o', str(payload),
                    'loader.efi=' + str(ROOT / 'xnuloader/loader.efi'), '--usb'], check=True)
    blob = (inputs / 'BootShim.bin').read_bytes() + (inputs / 'SILICIUM_UEFI.fd').read_bytes() + payload.read_bytes()
    blob += b'\0' * (-len(blob) % 16)
    kernel = folder / 'loader-kernel.tmp'
    kernel.write_bytes(gzip.compress(blob, compresslevel=6, mtime=0) + (inputs / 'boot.dtb').read_bytes())
    subprocess.run(['python3', str(inputs / 'mkbootimg.py'), '--kernel', str(kernel), '--ramdisk',
                    str(inputs / 'ramdisk'), '--header_version', '3', '-o', str(folder / 'boot.img')], check=True)
    header = (folder / 'boot.img').read_bytes()
    if header[:8] != b'ANDROID!' or struct.unpack_from('<I', header, 40)[0] != 3:
        raise SystemExit('Invalid loader boot image')
    payload.unlink()
    kernel.unlink()
    shutil.copyfile(inputs / 'inputs.json', folder / 'runtime-inputs.json')


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
    assemble_runtime(folder)
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
        'profile': 'lemonade-public-runtime-v1', 'kernel_commit': kernel, 'userland_commit': userland,
        'target': 'OnePlus 9 LE2110 / 8 GiB / AMB655X / lemonade / ARM64', 'build_host': platform.machine(),
        'memory_reg_sha256': launch.MEMORY_REG_SHA256,
        'run_url': os.environ.get('LEMONADE_CI_RUN_URL', ''),
        'hardware_verified': False, 'bootable_image_included': True,
        'apple_system_included': True, 'device_firmware_included': True,
        'boot_arguments': launch.BOOT_ARGUMENTS,
        'runtime_inputs': json.loads((ROOT / 'tools/ci/runtime-inputs.json').read_text()),
        'uefi_rebuilt': False, 'root_template_rebuilt': False,
        'wifi_credentials_included': False, 'wifi_history_included': False,
        'persistent_user_data_included': False, 'partition_writes': False,
        'files': files,
    }
    (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    writer = SplitWriter(output, name + '.tar.gz')
    with tarfile.open(fileobj=writer, mode='w|gz', compresslevel=1) as package:
        package.add(folder, arcname=name)
    writer.close()
    shutil.copyfile(folder / 'manifest.json', output / 'manifest.json')
    shutil.copyfile(ROOT / 'docs/LEMONADE-CI.md', output / 'README.md')
    assets = writer.paths + [output / 'manifest.json', output / 'README.md']
    (output / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + p.name + '\n' for p in assets))
    shutil.rmtree(folder)
    print(f'Complete experimental RAM runtime: {len(writer.paths)} parts')



if __name__ == '__main__':
    main()
