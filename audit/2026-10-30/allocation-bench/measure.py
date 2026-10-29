"""Compare class variants in fresh JVMs, with the 144 MiB heap used by start-small.sh.

Compile AllocationProbe.java against the server test runtime first. Each variant must
contain its Chunk, ChunkSection, AthenaDeferredSectionData*, AthenaChunkPacketCache*
classes, ahead of the same runtime classpath. Store files must already exist.
"""
import argparse
import csv
import os
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
for arg in ['jdk', 'classpath', 'probe', 'baseline', 'fixed', 'map', 'output']:
    p.add_argument('--' + arg, type=Path, required=True)
p.add_argument("--modes", nargs="+", default=["maps", "compress", "save"])
a = p.parse_args()
runtime = a.classpath.read_text().strip()
output = a.output.resolve()
output.parent.mkdir(parents=True, exist_ok=True)
work = output.parent / 'work'
work.mkdir(exist_ok=True)
variants = [('baseline', a.baseline.resolve()), ('fixed', a.fixed.resolve())]
rows, logs = [], []
for mode in a.modes:
    for process in range(1, 4):
        for label, classes in (variants if process % 2 else list(reversed(variants))):
            cp = os.pathsep.join([str(classes), str(a.probe.resolve()), runtime])
            command = [str(a.jdk.resolve() / 'bin/java'), '-Xms144m', '-Xmx144m',
                       '-XX:+UseParallelGC', '-XX:+UseCompactObjectHeaders',
                       '-cp', cp, 'net.minecraft.server.AllocationProbe', mode,
                       str(a.map.resolve()), '1200']
            result = subprocess.run(command, cwd=work, text=True, capture_output=True,
                                    check=True, timeout=180)
            logs.append(f'{mode} {label} process={process}\n{result.stdout}{result.stderr}')
            print(f'{mode} {label} process={process}\n{result.stdout}', flush=True)
            for line in result.stdout.splitlines():
                if line.startswith('RESULT,'):
                    _, measured, run, ms, allocated = line.split(',')
                    rows.append([label, process, measured, run, ms, allocated])
            output.with_suffix('.log').write_text('\n'.join(logs))
            with output.open('w', newline='') as stream:
                writer = csv.writer(stream, lineterminator='\n')
                writer.writerow(['variant', 'process', 'mode', 'run', 'ms', 'allocated_bytes'])
                writer.writerows(rows)
