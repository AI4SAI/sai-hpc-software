#!/usr/bin/env python3
"""Apply only the diagnosed external MPI environment repair before relocking."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

from cp2k_spack import write_config
from cp2k_spack_environment import environment, HPCX_ROOT


def check_repair(old, new):
    normalized = deepcopy(new)
    external = normalized['spack']['packages']['openmpi']['externals'][0]
    external.pop('extra_attributes')
    if normalized != old:
        raise ValueError('MPI repair would change more than the external runtime environment')


def prepare(partition, target, native_os):
    config = Path('/workspace/env/spack.yaml')
    old = json.loads(config.read_text())
    prefix = '/opt/software/cp2k/development/spack-native-probe/' + partition
    new = environment(partition, prefix, target, native_os)
    check_repair(old, new)
    for relative in ('hcoll/lib/libhcoll.so.1', 'sharp/lib/libsharp_coll.so'):
        if not (Path(HPCX_ROOT) / relative).is_file():
            raise ValueError('required standalone MPI runtime missing: ' + relative)
    for name in ('spack.yaml', 'spack.lock'):
        destination = Path('/results') / (name + '.before-mpi-repair')
        if destination.exists():
            raise ValueError('repair evidence already exists')
        shutil.copy2(Path('/workspace/env') / name, destination)
    backup = config.with_name('spack.yaml.before-mpi-repair')
    if backup.exists():
        raise ValueError('refusing repeated environment repair')
    config.rename(backup)
    write_config(config, new)


if __name__ == '__main__':
    prepare(*sys.argv[1:])
