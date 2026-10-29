"""Measure store opening in fresh JVMs after static initialization, with warm store pages.

Pass --variant original=/path/to/classes --variant pr26=/path/to/classes
--variant fixed=/path/to/classes. Variant directories must precede the runtime classpath.
StoreOpenProbe.class is the small probe supplied with REVIEW-PR26.md.
"""
import argparse
import csv
import os
from pathlib import Path
import re
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--jdk', type=Path, required=True)
parser.add_argument('--runtime-classpath-file', type=Path, required=True)
parser.add_argument('--probe-classes', type=Path, required=True)
parser.add_argument('--variant', action='append', required=True)
parser.add_argument('--maps', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
variants = [(label, str(Path(classes).resolve())) for label, classes in
            (item.split('=', 1) for item in args.variant)]
args.jdk = args.jdk.resolve()
args.maps = args.maps.resolve()
args.probe_classes = args.probe_classes.resolve()
args.output = args.output.resolve()
maps = ['map_bayside', 'map_construct', 'map_par_72_revamp']
classpath = args.runtime_classpath_file.read_text().strip()
work = args.output.parent / 'store-open-work'
work.mkdir(parents=True, exist_ok=True)
bootstrap = work / 'no-region-bootstrap'
# A path without region/ initializes AthenaSectionStore but cannot queue a store build.
assert not (bootstrap / 'region').exists()
for name in maps:
    for store in (args.maps / name).glob('athena-sections2-*.bin'):
        with store.open('rb') as stream:
            while stream.read(8 * 1024 * 1024):
                pass
rows, log = [], []
for run in range(1, 4):
    # Rotate variant order; all variants use a fresh JVM and the same maps and classpath.
    ordered = variants[run - 1:] + variants[:run - 1]
    for name in maps:
        for label, classes in ordered:
            cp = os.pathsep.join([classes, str(args.probe_classes), classpath])
            result = subprocess.run([
                str(args.jdk / 'bin/java'), '-cp', cp, 'net.minecraft.server.StoreOpenProbe',
                str(bootstrap), str(args.maps / name),
            ], cwd=work, text=True, capture_output=True, check=True, timeout=60)
            log.append(f'{label} {name} run={run}\n{result.stdout}{result.stderr}')
            match = re.search(rf'{name} open=([0-9.]+) ms store=(true|false)', result.stdout)
            if match is None or match[2] != 'true':
                raise RuntimeError(f'Expected an existing valid store: {result.stdout}\n{result.stderr}')
            rows.append([label, name, run, match[1]])
            print(f'{label} {name} run={run}: {match[1]} ms', flush=True)
with args.output.open('w') as stream:
    writer = csv.writer(stream)
    writer.writerow(['variant', 'map', 'run', 'open_ms'])
    writer.writerows(rows)
args.output.with_suffix('.log').write_text('\n'.join(log))
