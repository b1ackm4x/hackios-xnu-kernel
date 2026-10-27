#!/usr/bin/env python3
"""Execute production multi-interface network code against a small host KPI shim."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'xnu/files/bsd/dev/redwood_net.c').read_text()
header = (ROOT / 'xnu/files/bsd/sys/redwood_net.h').read_text()

def function(name):
    at = source.index('\n' + name + '(') + 1
    start = source.rfind('\n\n', 0, at) + 2
    # Skip the comment before a function, if any.
    if source[start:].startswith('/*'):
        start = source.index('*/', start) + 3
    begin = source.index('{', at)
    depth = 1
    end = begin + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

link = header[header.index('struct redwood_net_link {'):header.index('\n};') + 3]
state = source[source.index('struct net_if {'):source.index('/*\n * Boot-arg net_trace')]
functions = '\n\n'.join(function(name) for name in (
    'net_output', 'redwood_net_input_unit', 'redwood_net_input',
    'redwood_net_link_state_unit', 'redwood_net_link_state', 'redwood_net_attach'))
with tempfile.TemporaryDirectory(prefix='hackios-net-') as folder:
    work = Path(folder)
    (work / 'production.inc').write_text(link + '\n' + state + '\n' + functions)
    exe = work / 'test'
    subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                    '-Wno-unused-parameter', '-Wno-format', '-fsanitize=address,undefined',
                    '-I' + str(work), str(ROOT / 'tools/test-upstream-net.c'), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
