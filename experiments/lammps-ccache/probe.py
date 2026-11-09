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
from md_ccache import compile_command, digest, include_layout


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
        flags += (['-Xcompiler=-march=native,-mtune=native'] if label == 'cuda'
                  else ['-march=native', '-mtune=native'])
        folder = root / label
        folder.mkdir()
        inputs = folder / 'inputs'; inputs.mkdir()
        (inputs / 'early').mkdir(); (inputs / 'late').mkdir()
        source = inputs / 'probe.cpp'
        header = inputs / 'late/value.h'
        header.write_text('#define VALUE 11\n')
        source.write_text('#include "value.h"\n#ifndef OFFSET\n#define OFFSET 0\n#endif\n'
                          'int main() {return VALUE + OFFSET;}\n')
        config = dict(original, store=str(folder / 'cache'), stats_log=str(folder / 'stats.log'),
                      namespace=include_layout([inputs])['sha256'], ready=True)
        args = [*flags, '-O2', '-I' + str(inputs / 'early'), '-I' + str(inputs / 'late'),
                '-MD', '-MF', str(folder / 'probe.d'), '-c', str(source), '-o', str(folder / 'probe.o')]
        def attempt(name, expect_hit, expected, extra=()):
            log = Path(config['stats_log']); log.unlink(missing_ok=True)
            obj = folder / 'probe.o'; obj.unlink(missing_ok=True)
            start = time.monotonic()
            code = compile_command(config, compiler, [*args, *extra])
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
        attempt('equivalent-source-comment', True, 22)
        attempt('changed-effective-flag', False, 23, ['-DOFFSET=1'])
        (inputs / 'early/value.h').write_text('#define VALUE 44\n')
        config['namespace'] = include_layout([inputs])['sha256']
        time.sleep(1.05)
        attempt('new-shadowing-header', False, 44)
        attempt('warm-after-layout-change', True, 44)
    Path('/runtime/ccache-probes.json').write_text(json.dumps(records, indent=2) + '\n')
    print('LAMMPS_CCACHE_INVALIDATION_PROBES_PASSED', len(records), flush=True)


if __name__ == '__main__':
    main()
