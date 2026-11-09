#!/usr/bin/env python3
"""Require real GCC, nvcc_wrapper and native CUDA cache invalidation on SAI."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, '/control')
from md_ccache import compile_command, digest


def main():
    original = json.loads(Path('/runtime/ccache-launcher.json').read_text())
    root = Path('/workspace/ccache-probes')
    root.mkdir()
    records = []
    for label, compiler, flags in (
            ('gcc', os.environ['CXX'], []),
            ('kokkos-host', '/workspace/lammps/lib/kokkos/bin/nvcc_wrapper', ['--host-only']),
            ('kokkos-cuda', '/workspace/lammps/lib/kokkos/bin/nvcc_wrapper', ['-arch=sm_70']),
            ('cuda', shutil.which('nvcc'), ['-x', 'cu', '-arch=sm_70', '-ccbin', os.environ['CXX']])):
        folder = root / label
        folder.mkdir()
        source = folder / 'probe.cpp'
        header = folder / 'value.h'
        header.write_text('#define VALUE 11\n')
        source.write_text('#include "value.h"\nint main() {return VALUE;}\n')
        config = dict(original, store=str(folder / 'cache'), stats_log=str(folder / 'stats.log'))
        args = [*flags, '-O2', '-MD', '-MF', str(folder / 'probe.d'), '-c', str(source), '-o', str(folder / 'probe.o')]
        def attempt(name, expect_hit, expected):
            log = Path(config['stats_log']); log.unlink(missing_ok=True)
            obj = folder / 'probe.o'; obj.unlink(missing_ok=True)
            start = time.monotonic()
            code = compile_command(config, compiler, args)
            stats = log.read_text() if log.exists() else ''
            hit = 'direct_cache_hit' in stats or 'preprocessed_cache_hit' in stats
            if code or hit != expect_hit:
                raise ValueError(f'{label}/{name}: exit={code}, hit={hit}, stats={stats}')
            exe = folder / 'probe'
            # -x cu selects a source language; an ELF object must not inherit
            # it during the independent link/execution check.
            link_flags = flags[2:] if label == 'cuda' else flags
            subprocess.run([compiler, *link_flags, str(obj), '-o', str(exe)], check=True)
            if subprocess.run([str(exe)]).returncode != expected:
                raise ValueError('cached executable returned stale data')
            records.append(dict(compiler=label, case=name, hit=hit, expected_value=expected,
                                seconds=time.monotonic() - start, object_sha256=digest(obj), stats=stats))
            Path('/runtime/ccache-probes.json').write_text(json.dumps(records, indent=2) + '\n')
        time.sleep(1.05)
        attempt('cold', False, 11)
        attempt('warm', True, 11)
        previous = header.stat()
        header.write_text('#define VALUE 22\n')
        os.utime(header, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        time.sleep(1.05)
        attempt('changed-header-same-mtime', False, 22)
        attempt('warm-after-header-change', True, 22)
        source.write_text(source.read_text() + '// changed comment\n')
        time.sleep(1.05)
        attempt('changed-source-comment', False, 22)
        attempt('warm-after-source-change', True, 22)
    Path('/runtime/ccache-probes.json').write_text(json.dumps(records, indent=2) + '\n')
    print('LAMMPS_CCACHE_INVALIDATION_PROBES_PASSED', len(records), flush=True)


if __name__ == '__main__':
    main()
