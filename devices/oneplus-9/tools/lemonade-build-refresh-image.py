#!/usr/bin/env python3
"""Dedicated UIKit refresh acceptance images; never replace the 60-Hz images."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('usb',ROOT/'devices/oneplus-9/tools/lemonade-usb.py')
usb=importlib.util.module_from_spec(spec);spec.loader.exec_module(usb)
spec=importlib.util.spec_from_file_location('images',ROOT/'devices/oneplus-9/tools/lemonade-build-wifi-image.py')
images=importlib.util.module_from_spec(spec);spec.loader.exec_module(images)

def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def entries(image):
    container=images.apfs.Container(str(image))
    try:
        volume=images.apfs.pick_volume(container,None)
        root=volume.inode(images.apfs.ROOT_DIR_INO_NUM)
        yield '',(root.mode,root.owner,root.group,b'')
        pending=[(images.apfs.ROOT_DIR_INO_NUM,'')]
        while pending:
            parent,prefix=pending.pop()
            for name,fid,kind in volume.dir_entries(parent):
                if name in ('.','..') or any(c in name for c in '/\n\t'):raise SystemExit('Unsafe image path')
                p=prefix+name;i=volume.inode(fid)
                if kind not in (4,8,10):raise SystemExit('Unsupported inode')
                yield p,(i.mode,i.owner,i.group,b'' if kind==4 else i.contents())
                if kind==4:pending.append((fid,p+'/'))
    finally:container.close()

def identity(value):
    return (*value[:3],hashlib.sha256(value[3]).hexdigest())

def pack(original,destination,changes,volume):
    # Streaming extraction bounds memory to the largest file, not the whole OS.
    expected={}
    with tempfile.TemporaryDirectory(prefix='pack-',dir=destination.parent) as tmp:
        work=Path(tmp);tree=work/'root';tree.mkdir();metadata=[]
        def put(name,value):
            mode,uid,gid,data=value;p=tree/name;p.parent.mkdir(parents=True,exist_ok=True)
            if stat.S_ISDIR(mode):p.mkdir(exist_ok=True)
            elif stat.S_ISLNK(mode):p.symlink_to(os.fsdecode(data))
            else:p.write_bytes(data)
            if not stat.S_ISLNK(mode):p.chmod(stat.S_IMODE(mode))
            metadata.append(f'{mode:o}\t{uid}\t{gid}\t{name}\n');expected[name]=identity(value)
        for name,value in entries(original):
            if name not in changes:put(name,value)
        for name,value in sorted(changes.items()):put(name,value)
        manifest=work/'metadata.tsv';manifest.write_text(''.join(metadata))
        candidate=work/'candidate.apfs'
        subprocess.run(['python3',str(ROOT/'tools/apfs/mkapfs.py'),'-z','--plain','fw',
            '-o',str(candidate),f'{volume}={tree},manifest={manifest}'],check=True,stdout=subprocess.DEVNULL)
        actual={name:identity(value) for name,value in entries(candidate)}
        if actual!=expected:raise SystemExit('Refresh image contents or metadata failed readback')
        shutil.copy2(candidate,destination)
    return {'sha256':sha(destination),'bytes':destination.stat().st_size,'entries_verified':len(expected)}

def main():
    os.umask(0o077)
    out=ROOT/'out/lemonade-refresh';out.mkdir(exist_ok=True)
    app=ROOT/'out/lemonade-refresh-app';build=json.loads((app/'build.json').read_text())
    if build.get('profile')!='lemonade-refresh-app-v1':raise SystemExit('Wrong application build')
    for p,h in build['sources'].items():
        if sha(ROOT/p)!=h:raise SystemExit('Stale application source: '+p)
    for p,h in build['artifacts'].items():
        if sha(app/'LemonadeRefresh.app'/p)!=h:raise SystemExit('Wrong application binary: '+p)
    for p,h in build['helpers'].items():
        if sha(app/p)!=h:raise SystemExit('Wrong application launcher: '+p)
    metal,graphics=usb.verified_graphics_overlay(True)
    normal=json.loads((ROOT/'local/lemonade/images.json').read_text())
    system=ROOT/'userland/system.apfs';system_hash=sha(system)
    if system_hash!=normal['userland/system.apfs']['sha256']:raise SystemExit('Normal System image changed')
    jobs=ROOT/'devices/oneplus-9/lemonade-refresh.rc.jobs'
    root_record=pack(metal,out/'root.apfs',{
        'bin/refresh-launch':(0o100755,0,0,(app/'refresh-launch').read_bytes()),
        'etc/rc.jobs':(0o100644,0,0,jobs.read_bytes())},'redwood')
    additions={'Applications/LemonadeRefresh.app':(0o40755,0,0,b''),
        'Applications/LemonadeRefresh.app/Info.plist':(0o100644,0,0,(app/'LemonadeRefresh.app/Info.plist').read_bytes()),
        'Applications/LemonadeRefresh.app/LemonadeRefresh':(0o100755,0,0,(app/'LemonadeRefresh.app/LemonadeRefresh').read_bytes())}
    # A registered system application has ordinary files in its own bundle.
    # Reuse System only when those exact files and the base are unchanged.
    key=hashlib.sha256(json.dumps({k:identity(v) for k,v in additions.items()},sort_keys=True).encode()).hexdigest()
    old=json.loads((out/'manifest.json').read_text()) if (out/'manifest.json').exists() else {}
    if (old.get('base_system_sha256')==system_hash and old.get('system_additions_sha256')==key and
        (out/'system.apfs').exists() and sha(out/'system.apfs')==old.get('system',{}).get('sha256')):
        system_record=old['system']
    else:system_record=pack(system,out/'system.apfs',additions,'System')
    if sha(system)!=system_hash:raise SystemExit('Original System changed during packaging')
    report={'profile':'lemonade-refresh-v1','base_system_sha256':system_hash,
        'system_additions_sha256':key,'base_root_sha256':normal['userland/root.apfs']['sha256'],
        'root':root_record,'system':system_record,'graphics':graphics,'app':build,
        'jobs_sha256':sha(jobs),'original_images_unchanged':True}
    (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'root':root_record,'system':system_record,'original_images_unchanged':True},indent=2))

if __name__=='__main__':main()
