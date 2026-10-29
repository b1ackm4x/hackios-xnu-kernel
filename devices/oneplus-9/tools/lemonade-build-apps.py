#!/usr/bin/env python3
"""Append locally authorized app assets to a copy of the verified System image.

Inputs are a private JSON file with files[name]={path,sha256}, versions,
applications and sources. No account, network, or device state is collected.
Every original APFS entry is read back with identical bytes and metadata.
"""
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
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('apps_base',ROOT/'devices/oneplus-9/tools/lemonade-build-base.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)
apfs=build.base.apfs
PREFIX='usr/share/lemonade-apps'
FILES={'TrollStore.tar','TrollApps.ipa','ldid','trollstorehelper','Delta.ipa','Bilibili.ipa','Vulkanon.gba'}


def validate_inputs(config):
    if set(config.get('files',{}))!=FILES:
        raise ValueError('The selected app set is incomplete or unexpected')
    for name,entry in config['files'].items():
        path=Path(entry['path'])
        if not path.is_absolute():path=ROOT/path
        if path.is_symlink() or not path.is_file() or not re.fullmatch('[0-9a-f]{64}',entry['sha256']):
            raise ValueError('Invalid local asset: '+name)
        if build.digest(path)!=entry['sha256']:raise ValueError('Local asset changed: '+name)
    if config.get('versions')!={'TrollStore':'2.1.1','TrollApps':'2.3'}:
        raise ValueError('Unsupported store installer versions')
    apps=config.get('applications',[])
    expected={'com.rileytestut.Delta':('Delta.ipa','1.7.1'),'tv.danmaku.bilianime':('Bilibili.ipa','8.89.0')}
    if len(apps)!=len(expected) or {a['identifier']:(a['file'],a['version']) for a in apps}!=expected:
        raise ValueError('Unexpected app identity or version')
    sources=config.get('sources',[])
    if not 1<=len(sources)<=20:raise ValueError('Invalid source count')
    for source in sources:
        url=urlsplit(source['url'])
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Only public HTTPS catalog URLs are accepted')


def entries(image):
    container=apfs.Container(str(image))
    try:
        volume=apfs.pick_volume(container,None)
        pending=[('',apfs.ROOT_DIR_INO_NUM,4)]
        while pending:
            path,fid,kind=pending.pop()
            inode=volume.inode(fid)
            if kind not in (4,8,10):raise ValueError('Unsupported System inode')
            data=b'' if kind==4 else inode.contents()
            yield path,(inode.mode,inode.owner,inode.group,data)
            if kind==4:
                for name,child,child_kind in volume.dir_entries(fid):
                    if name in ('.','..') or any(c in name for c in '/\n\t\0'):
                        raise ValueError('Invalid System path')
                    pending.append((path+'/'+name if path else name,child,child_kind))
    finally:
        container.close()


def record(entry):
    mode,uid,gid,data=entry
    return {'mode':mode,'uid':uid,'gid':gid,'sha256':hashlib.sha256(data).hexdigest()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    options=parser.parse_args();os.umask(0o077)
    config=json.loads(options.inputs.read_text());validate_inputs(config)
    output=options.output.resolve()
    if not output.is_relative_to(ROOT/'out'):parser.error('Output must be a new directory under out/')
    source=ROOT/'userland/system.apfs'
    original_sha=json.loads((ROOT/'local/lemonade/images.json').read_text())['userland/system.apfs']['sha256']
    if build.digest(source)!=original_sha:raise SystemExit('Original System image is not verified')
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    inventory={};asset_records={}
    with tempfile.TemporaryDirectory(prefix='apps-system-',dir=output) as temporary:
        work=Path(temporary);tree=work/'system';tree.mkdir()
        for name,entry in entries(source):
            if name==PREFIX or name.startswith(PREFIX+'/'):raise ValueError('Original System already contains app assets')
            mode,uid,gid,data=entry;path=tree/name
            path.parent.mkdir(parents=True,exist_ok=True)
            if stat.S_ISDIR(mode):path.mkdir(exist_ok=True)
            elif stat.S_ISLNK(mode):path.symlink_to(os.fsdecode(data))
            else:path.write_bytes(data)
            if not stat.S_ISLNK(mode):path.chmod(stat.S_IMODE(mode))
            inventory[name]=record(entry)
        original_inventory=dict(inventory)
        print('Original System extracted with metadata; adding only sealed app assets.',flush=True)
        target=tree/PREFIX
        for directory in ('usr','usr/share',PREFIX):
            if directory in inventory:
                if not stat.S_ISDIR(inventory[directory]['mode']):raise ValueError('Asset parent is not a directory')
            else:
                (tree/directory).mkdir(exist_ok=True);(tree/directory).chmod(0o755)
                inventory[directory]=record((0o40755,0,0,b''))
        for name,info in config['files'].items():
            path=Path(info['path']);path=path if path.is_absolute() else ROOT/path
            dest=target/name;shutil.copyfile(path,dest)
            mode=0o100755 if name in ('ldid','trollstorehelper') else 0o100644
            dest.chmod(stat.S_IMODE(mode))
            inventory[PREFIX+'/'+name]={'mode':mode,'uid':0,'gid':0,'sha256':info['sha256']}
            asset_records[name]={'sha256':info['sha256'],'bytes':dest.stat().st_size}
        runtime={'profile':'lemonade-private-apps-v1','files':asset_records,
                 'versions':config['versions'],'applications':config['applications']}
        sources=[{'name':s['name'],'url':s['url']} for s in config['sources']]
        preferences={'profile':'lemonade-app-preferences-v1','sources':sources,
                     'revision':hashlib.sha256(json.dumps(sources,sort_keys=True).encode()).hexdigest(),
                     'game_sha256':asset_records['Vulkanon.gba']['sha256']}
        for name,value in [('manifest.plist',runtime),('setup.plist',preferences)]:
            data=plistlib.dumps(value,fmt=plistlib.FMT_BINARY);(target/name).write_bytes(data);(target/name).chmod(0o644)
            inventory[PREFIX+'/'+name]=record((0o100644,0,0,data))
        metadata=work/'metadata.tsv'
        metadata.write_text(''.join(f"{r['mode']:o}\t{r['uid']}\t{r['gid']}\t{name}\n" for name,r in sorted(inventory.items())))
        subprocess.run(['python3',str(ROOT/'tools/apfs/mkapfs.py'),'-z','--plain',PREFIX,
                        '-o',str(output/'system.apfs'),f'System={tree},manifest={metadata}'],check=True)
    actual={name:record(entry) for name,entry in entries(output/'system.apfs')}
    if actual!=inventory:raise SystemExit('System APFS readback differs')
    if any(actual[name]!=entry for name,entry in original_inventory.items()):raise SystemExit('Original System entry changed')
    if build.digest(source)!=original_sha:raise SystemExit('Original System file changed during packaging')
    result={'profile':'lemonade-apps-system-v1','system_sha256':build.digest(output/'system.apfs'),
            'system_bytes':(output/'system.apfs').stat().st_size,'original_system_sha256':original_sha,
            'original_entries_verified':len(original_inventory),'total_entries_verified':len(inventory),
            'added_paths':sorted(set(inventory)-set(original_inventory)),
            'assets':asset_records,'applications':config['applications'],'versions':config['versions'],
            'offline_restore':True,'foreground_launches':False,'credentials_included':False,
            'sources':{'devices/oneplus-9/tools/lemonade-build-apps.py':build.digest(Path(__file__))},
            'hardware_verified':False}
    (output/'build.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('system_sha256','original_entries_verified','total_entries_verified')}),flush=True)


if __name__=='__main__':main()
