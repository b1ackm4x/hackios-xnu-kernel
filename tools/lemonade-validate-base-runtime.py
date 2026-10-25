#!/usr/bin/env python3
"""Observe and stop the untouched base using its shipped launcher.

Requires the exact phone lease and successful combined-kernel acceptance.
No diagnostic workload is added to the release image. Logs are private.
"""
import argparse,hashlib,importlib.util,json,os,re,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--version',default='2026.10.25-base.4')
options=parser.parse_args()
if not re.fullmatch(r'[0-9][0-9A-Za-z.-]{0,55}',options.version):parser.error('Invalid release version')
PKG=ROOT/'out/releases'/('lemonade-'+options.version)
OUT=ROOT/'local/lemonade'/('release-'+options.version)
os.umask(0o077)
OUT.mkdir(exist_ok=True)
s=importlib.util.spec_from_file_location('base_launch',PKG/'launch.py');launch=importlib.util.module_from_spec(s);s.loader.exec_module(launch)
s=importlib.util.spec_from_file_location('base_direct',ROOT/'tools/lemonade-check-direct.py');direct=importlib.util.module_from_spec(s);s.loader.exec_module(direct)
m=launch.verify(PKG)
for name,entry in m['files'].items():
 assert launch.digest(PKG/name)==entry['sha256']
def sanitize(raw):
 raw=re.sub(rb'(?i)(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])',b'[radio address omitted]',raw)
 return b'\n'.join(b'[network identity detail omitted]' if re.search(rb'(?i)\b(?:ssid|bssid|passphrase|password)\b',line) else line for line in raw.splitlines())+b'\n'
def cli(action):
 r=subprocess.run(['python3',str(PKG/'launch.py'),action],capture_output=True,text=True)
 (OUT/('clean-'+action+'.log')).write_text(r.stdout+r.stderr)
 if r.returncode:raise SystemExit('Clean runtime launcher failed at '+action)
def collect(serial,name):
 path=OUT/name
 try:
  launch.fb(serial,'oem','log',timeout=15)
  launch.fb(serial,'get_staged',path,timeout=30)
 finally:
  if path.exists():path.write_bytes(sanitize(path.read_bytes()))
 return path
print('Starting the untouched package through its shipped launcher.',flush=True)
cli('start');serial=launch.loader(m);started=time.monotonic();live=[];states=[]
try:
 for i,at in enumerate((0.5,1,2,4,8,15,30,45,60,75,90)):
  time.sleep(max(0,started+at-time.monotonic()))
  state=launch.fb(serial,'getvar','result');states.append(state)
  if 'running' not in state:raise SystemExit('Clean runtime returned without a host stop')
  live.append(collect(serial,'clean-live-'+str(i)+'.log'))
  if at>=15:print('Clean runtime still running at '+str(at)+' seconds.',flush=True)
finally:
 cli('stop')
raw=collect(serial,'clean-final.log').read_bytes();result=launch.fb(serial,'getvar','result')
joined=b'\n'.join([*(p.read_bytes() for p in live),raw]);text=joined.decode(errors='replace')
cmdlines=list(dict.fromkeys(re.findall(r'^cmdline: (.*)$',text,re.M)))
args=cmdlines[0].split() if len(cmdlines)==1 else []
summary=re.findall(r'direct final completed=(\d+) fallbacks=(\d+) failed=(\d+) mapped_pages=(\d+)',raw.decode(errors='replace'))
checks={
 'unchanged_release_image':launch.digest(PKG/'root.apfs')==m['files']['root.apfs']['sha256'],
 'actual_default_arguments':[a for a in args if not a.startswith('epoch=')]==['debug=0x8','rd=md0']+launch.BASE_ARGUMENTS,
 'runtime_setup_complete':'init: runtime setup complete; failures=0' in text,
 'no_boot_self_tests':'=== 36 checks' not in text and '64 MB of anonymous memory' not in text,
 'no_test_workloads':not any(x in text for x in ('lemonade-net: RAM-only acceptance','lemonade_metal_features','lemonade_metal_mps','lemonade_metal_ca','metal_drv_test','/bin/rwtouch')),
 'no_timed_exit':'loader_return_after expired' not in text and not any(a.startswith('loader_return_after=') for a in args),
 'observed_running_without_timer':len(states)==11 and all('running' in x for x in states),
 'host_stop_observed':'the host asked for the end of the run: back to the loader' in text,
 'returned_cleanly':bool(re.search(r'result: returned \d+ 0(?:\s|$)',result)),
 'dpu_completed_frames':len(summary)==1 and int(summary[0][0])>0 and summary[0][2]=='0',
 'wifi_runtime_offered':'wifi-lemonade: station offered to iOS; runtime kept until normal stop' in text,
 'wifi_stage_completed':'wifi-lemonade: stage 24 passed;' in text,
 'no_kernel_panic':not re.search(r'panic\(cpu|Debugger called: <panic>|Debugger message:\s*panic|uncaught exception',text,re.I),
 'no_driver_failure':not re.search(r'direct failed|manual transfer failed|GPU completion missing|completion unproven|DMA cleanup failed|restoration failed|release failed|driver worker stop timed out|emergency endpoint reset',text),
 'dpu_restored':'source restore pixels=1 address=1/1 ' in text and 'scaler restored registers=1 count=50 before_gop=1' in text,
 'wifi_dma_stopped':'endpoint DMA stopped before unmap' in text and 'endpoint bus master clear verified' in text,
}
checks.update(direct.check_refresh(text))
if m.get('display_return_drain'):
 checks['display_drained_before_gpu_stop']=direct.check_return_drain(raw.decode(errors='replace'))
if m.get('gpu_return_call_drain'):
 checks['gpu_calls_drained_and_userspace_exit_clean']=direct.check_gpu_return(raw.decode(errors='replace'))
proof={'passed':all(checks.values()),'checks':checks,'kernel_sha256':m['files']['kernel']['sha256'],'root_sha256':m['files']['root.apfs']['sha256'],'elapsed_seconds':round(time.monotonic()-started,3),'direct_completed':int(summary[0][0]) if summary else 0,'log_sha256':hashlib.sha256(raw).hexdigest(),'live_logs':[{'path':str(p.relative_to(ROOT)),'sha256':launch.digest(p)} for p in live],'result':result,'native_settings_password_entry_verified':False}
proof['userspace_exit_sigsegv']=raw.count(b'exited due to SIGSEGV')
(OUT/'clean-runtime-evidence.json').write_text(json.dumps(proof,indent=2)+'\n')
print(json.dumps(proof,indent=2),flush=True)
raise SystemExit(0 if proof['passed'] else 1)
