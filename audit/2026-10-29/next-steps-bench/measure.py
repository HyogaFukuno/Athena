"""Fresh-JVM bootstrap and warm extraction comparisons for N04/N02.

Variant directories contain the corresponding RegistryID, NibbleArray and
PacketPlayOutMapChunk classes (including inner classes), ahead of the same runtime
classpath. Compile NextStepsProbe.java against the built server test runtime.
"""
import argparse
import csv
import os
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--jdk', type=Path, required=True)
p.add_argument('--classpath', type=Path, required=True)
p.add_argument('--probe', type=Path, required=True)
p.add_argument('--baseline', type=Path, required=True)
p.add_argument('--fixed', type=Path, required=True)
p.add_argument('--map', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--mode', choices=['bootstrap', 'packet'], required=True)
a = p.parse_args()
runtime = a.classpath.read_text().strip()
variants = [('baseline', a.baseline.resolve()), ('fixed', a.fixed.resolve())]
output = a.output.resolve()
work = output.parent / 'work'
work.mkdir(parents=True, exist_ok=True)
rows, logs = [], []
for process in range(1, 4):
    for label, classes in (variants if process % 2 else list(reversed(variants))):
        cp = os.pathsep.join([str(classes), str(a.probe.resolve()), runtime])
        command = [str(a.jdk.resolve() / 'bin/java'), '-Xms512m', '-Xmx1g', '-cp', cp,
                   'net.minecraft.server.NextStepsProbe', a.mode]
        if a.mode == 'packet':
            command.append(str(a.map.resolve()))
        result = subprocess.run(command, cwd=work, text=True, capture_output=True,
                                check=True, timeout=180)
        logs.append(f'{label} process={process}\n{result.stdout}{result.stderr}')
        print(f'{label} process={process}\n{result.stdout}', flush=True)
        found = 0
        for line in result.stdout.splitlines():
            if line.startswith('RESULT,'):
                _, mode, run, ms, allocated, count = line.split(',')
                rows.append([label, process, mode, run, ms, allocated, count])
                found += 1
        if found != (1 if a.mode == 'bootstrap' else 3):
            raise RuntimeError('missing measurement output')
with output.open('w', newline='') as stream:
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(['variant', 'process', 'mode', 'run', 'ms', 'allocated_bytes', 'count'])
    writer.writerows(rows)
output.with_suffix('.log').write_text('\n'.join(logs))
