"""Package a container-built SpinGNN prefix for the existing native exporter."""
import base64
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from export_native import write_manifests
from spingnn_config import site, target_policy
from spingnn_release import module_text, qualified_identity, require, save


def entry(identity):
    identity = qualified_identity(identity)
    prefix = identity['install_prefix']
    settings, policy = site(), target_policy(identity['target'])
    return dict(identity=identity, commands={'lmp': 'bin/lmp', 'lmp_spingnn2': 'bin/lmp_spingnn2'},
        external_roots=['/usr', '/lib', '/lib64', settings['gcc'], policy['mpi_prefix'], settings['mkl'],
                        '/opt/devtools/nvidia/cuda-12.9.1', '/opt/modules/modulefiles/devtools'],
        runtime=dict(modules=settings['modules'][policy['backend']][:2],
            prepend={'PATH': [prefix + '/venv/bin'], 'MODULEPATH': ['/opt/modules/modulefiles/devtools']},
            set=dict(SPINGNN2_ROOT=prefix, SPINGNN2_LIBRARY=prefix + '/lib/libspingnn2.so.1',
                     SPINGNN2_TARGET=identity['partition'], SPINGNN2_BACKEND=policy['backend'],
                     SPINGNN2_ARCH='sm' + identity['cuda_arch'] if identity['gpus'] else '',
                     PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')))


def version_module(identity):
    """One version selector, identical for all four independently built targets."""
    identity = qualified_identity(identity)
    base = str(Path(identity['install_prefix']).parent)
    return '\n'.join([
        'whatis("SpinGNN2 native inference and LAMMPS; CPU/OpenMP or V100 CUDA")',
        'help("Load this version inside a supported Slurm allocation. Native programs run directly from /opt.")',
        'if mode() ~= "load" and mode() ~= "unload" then return end',
        'local targets = {DSPRHBM=true, ["4V100"]=true, ["16V100"]=true, ["8V100V0"]=true}',
        'local partition',
        'if mode() == "load" then',
        '  local job = os.getenv("SLURM_JOB_ID")',
        '  if not job or not job:match("^[1-9]%d*$") then LmodError("load SpinGNN2 inside a Slurm allocation") end',
        '  partition = os.getenv("SLURM_JOB_PARTITION")',
        'else', '  partition = os.getenv("SAI_SPINGNN_LOADED_PARTITION")', 'end',
        'if not targets[partition] then LmodError("unsupported SpinGNN2 partition: " .. tostring(partition)) end',
        'local prefix = ' + json.dumps(base) + ' .. "/" .. partition',
        'if mode() == "unload" and os.getenv("SAI_SPINGNN_LOADED_PREFIX") ~= prefix then LmodError("saved SpinGNN2 version differs") end',
        'local path = prefix .. "/share/sai/module.lua"',
        'if not isFile(path) then LmodError("this SpinGNN2 partition is not installed") end',
        'local fragment, detail = loadfile(path)',
        'if not fragment then LmodError(tostring(detail)) end', 'fragment()', ''])


def repair_records(prefix):
    venv = prefix / 'venv'
    for record in venv.glob('lib/python*/site-packages/*.dist-info/RECORD'):
        rows = []
        for name, _, _ in csv.reader(record.open()):
            path = (record.parent.parent / name).resolve()
            require(path.is_relative_to(venv), 'wheel RECORD escaped its venv')
            if path == record:
                rows.append([name, '', ''])
            elif path.is_file():
                data = path.read_bytes()
                digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
                rows.append([name, 'sha256=' + digest, str(len(data))])
        with record.open('w', newline='') as stream:
            csv.writer(stream).writerows(rows)


def lammps_link(prefix):
    """Expose the standard command as one relative link to the same binary."""
    binary, link = prefix / 'bin/lmp_spingnn2', prefix / 'bin/lmp'
    require(binary.is_file() and not binary.is_symlink(), 'missing regular SpinGNN LAMMPS binary')
    if not link.exists() and not link.is_symlink():
        link.symlink_to('lmp_spingnn2')
    require(link.is_symlink() and os.readlink(link) == 'lmp_spingnn2'
            and link.resolve(strict=True) == binary, 'lmp must link relatively to lmp_spingnn2')


def relocatable(prefix, run, identity):
    """Use the prior administrator delivery's self-locating entry convention."""
    for path in (prefix / 'venv/bin').iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        first, _, body = data.partition(b'\n')
        if first.startswith(b'#!') and first.endswith(b'/venv/bin/python'):
            path.write_bytes(b'#!/bin/sh\n\'\'\'exec\' "$(dirname -- "$(readlink -f -- "$0")")/python" "$0" "$@"\n\' \'\'\'\n' + body)
    # pip's local build URL is provenance, not a dependency on its temporary tree.
    for path in (prefix / 'venv').glob('lib/python*/site-packages/spingnn2-*.dist-info/direct_url.json'):
        path.write_text(json.dumps(dict(url='https://github.com/Hyyu-Lab/SpinGNN.git',
            vcs_info=dict(vcs='git', commit_id=identity['source_sha'], requested_revision=identity['source_ref']))) + '\n')
    wrapper = run / 'sources/kokkos-4.7.02/bin/nvcc_wrapper'
    if identity['gpus']:
        shutil.copyfile(wrapper, prefix / 'bin/nvcc_wrapper')
        (prefix / 'bin/nvcc_wrapper').chmod(0o755)
    for path in (prefix / 'lib').rglob('*.cmake'):
        text = path.read_text()
        path.write_text(text.replace(str(wrapper), str(prefix / 'bin/nvcc_wrapper')))
    repair_records(prefix)
    dynamic = {}
    for name in ('bin/lmp_spingnn2', 'lib/libspingnn2.so.1'):
        text = subprocess.check_output(['readelf', '-d', str(prefix / name)], text=True)
        paths = re.findall(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]', text)
        require(paths and '$ORIGIN' in paths[0], 'missing self-relative ELF library path')
        require(not any(s in paths[0] for s in ('/workspace', '/home/', '/org/', '/tmp/')), 'temporary ELF runtime path')
        dynamic[name] = paths
    return dynamic


def package(run, identity):
    identity = qualified_identity(identity)
    require(os.environ.get('APPTAINER_CONTAINER') and os.environ.get('SAI_SPINGNN_CONTEXT') == 'build',
            'SpinGNN packaging must follow an actual container build')
    prefix = Path(identity['install_prefix'])
    lammps_link(prefix)
    dynamic = relocatable(prefix, run, identity)
    metadata = prefix / 'share/sai'
    metadata.mkdir(parents=True, exist_ok=True)
    fixture = metadata / 'fixture'
    fixture.mkdir()
    for path in (run / 'acceptance').iterdir():
        if path.is_file() and (path.suffix in ('.npz', '.json', '.bin')):
            shutil.copyfile(path, fixture / path.name)
    (metadata / 'module.lua').write_text(module_text(identity, prefix))
    (metadata / 'version-module.lua').write_text(version_module(identity))
    save(metadata / 'release-identity.json', identity)
    save(metadata / 'build-provenance.json', dict(identity=identity,
        base_sif=site()['base_sif'], base_sha256=site()['base_sha256'],
        job_id=os.environ['SLURM_JOB_ID'], container=os.environ['APPTAINER_CONTAINER'],
        context='build', elf_paths=dynamic, dependencies=dict(mpi=os.environ['MPI_HOME'],
        cxx=os.environ['CXX'], cuda=os.environ.get('CUDA_HOME'), mkl=site()['mkl'])))
    for path in [prefix, *prefix.rglob('*')]:
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
    write_manifests([entry(identity)])


if __name__ == '__main__':
    package(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())['identity'])
