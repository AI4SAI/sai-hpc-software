#!/usr/bin/env python3
"""Select the accepted, immutable NVHPC/F90 HDF5 installation for CPU QE."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil

from devtools_install import checksum, plain, require, verify
from qe_dependencies import HDF5_SHA256

PREFIX = Path('/opt/apps/qe/dependencies/hdf5/1.14.6-nvhpc26.3-ompi5.0.10-f90/DSPRHBM')
RECORD = Path(__file__).with_name('qe_site_hdf5.json')
LIBRARIES = {'libhdf5.a', 'libhdf5_fortran.a', 'libhdf5_f90cstub.a'}


def verify_provider(record):
    package, toolchain = record['package'], record['toolchain']
    require(record['schema'] == 1 and package['prefix'] == str(PREFIX)
            and package['version'] == '1.14.6', 'Unexpected CPU QE HDF5 provider')
    require(toolchain['source_sha256'] == HDF5_SHA256
            and toolchain['mpi_interface'] == 'F90' and toolchain['mpi_f08'] is False,
            'HDF5 source or Fortran interface changed')
    require(os.environ.get('MPI_HOME') == toolchain['mpi_home'], 'HDF5 MPI provider differs')
    for name, tool in toolchain['tools'].items():
        selected = shutil.which(name)
        require(selected is not None and str(Path(selected).resolve()) == tool['path']
                and checksum(selected) == tool['sha256'], 'HDF5 compiler/MPI tool changed: ' + name)
    verify(PREFIX, package['files'])
    return PREFIX


def prepare(destination):
    record = json.loads(RECORD.read_text())
    provider = verify_provider(record)
    destination = plain(destination)
    metadata = destination / 'share/sai'
    metadata.mkdir(parents=True, exist_ok=True)
    receipt = dict(provider=str(provider), provider_sha256=checksum(RECORD),
                   source_sha256=HDF5_SHA256, manifest_sha256=record['provenance']['manifest_sha256'],
                   verified=True, linkage='static', mpi_interface='F90')
    (metadata / 'site-hdf5.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
    license_dir = destination / 'share/licenses/hdf5'
    license_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(provider / 'share/COPYING', license_dir / 'COPYING')
    (license_dir / 'COPYING').chmod(0o444)
    return provider


def verify_links(build):
    """Check generated link commands before compiling the application."""
    observed = set()
    commands = []
    for path in sorted(Path(build).rglob('link.txt')):
        words = shlex.split(path.read_text())
        require(not any(word.startswith('-lhdf5') for word in words),
                'QE link uses an unpinned HDF5 library search')
        libraries = [Path(word) for word in words
                     if Path(word).name.startswith('libhdf5')]
        if not libraries:
            continue
        for library in libraries:
            require(library.parent == PREFIX / 'lib' and library.suffix == '.a',
                    'QE link escaped the accepted static HDF5 provider: ' + str(library))
            observed.add(library.name)
        commands.append(str(path.relative_to(build)))
    require(LIBRARIES <= observed, 'QE link commands lack the accepted HDF5 C/Fortran libraries')
    return dict(provider=str(PREFIX), libraries=sorted(observed), link_commands=commands)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify-links'))
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    print(prepare(args.path) if args.action == 'prepare' else json.dumps(verify_links(args.path), sort_keys=True))


if __name__ == '__main__':
    main()
