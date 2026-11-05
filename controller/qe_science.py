"""QE installation inventory and independently reparsable SCF/PH/PP checks."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

from source_cache import checksum
from release_contract import validate_identity
from native_module import validate_native_entry
from export_native import write_manifests, read_installed_manifests, inventory
from sif_runtime import PROGRAMS

CONTROL = Path(__file__).resolve().parent
MODULES = ['openmpi/5.0.10-nvhpc26.3-pgi-cuda12-auto', 'fftw/3.3.10', 'hdf5/1.14.6-pgi-ompi508']
NUMBER = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?'
PP_INPUT = """&inputpp
 prefix='silicon', outdir='./tmp/', filplot='rho.dat', plot_num=0
/
&plot
 nfile=1, filepp(1)='rho.dat', weight(1)=1.0, iflag=3,
 output_format=6, fileout='rho.cube'
/
"""


def number(text):
    value = float(text.replace('D', 'E').replace('d', 'e'))
    if not math.isfinite(value):
        raise ValueError('nonfinite QE result')
    return value


def parse_scf(text):
    if 'JOB DONE.' not in text or 'convergence has been achieved' not in text:
        raise ValueError('QE did not finish a converged SCF')
    values = re.findall(r'!\s+total energy\s*=\s*(' + NUMBER + r')\s+Ry', text)
    forces = re.findall(r'atom\s+\d+\s+type\s+\d+\s+force\s*=\s*(' + NUMBER + r')\s+(' + NUMBER + r')\s+(' + NUMBER + r')', text)
    if len(values) != 1 or len(forces) != 2:
        raise ValueError('missing or ambiguous QE energy/force results')
    return dict(energy_ry=number(values[0]), forces_ry_bohr=[[number(v) for v in row] for row in forces])


def parse_phonon(text):
    values = re.findall(r'freq\s*\(\s*\d+\s*\)\s*=\s*' + NUMBER + r'\s*\[THz\]\s*=\s*(' + NUMBER + r')\s*\[cm-1\]', text)
    if 'JOB DONE.' not in text or len(values) != 6:
        raise ValueError('QE phonon calculation did not finish six modes')
    return sorted(number(v) for v in values)


def cube_electrons(text):
    lines = text.splitlines()
    atoms = int(lines[2].split()[0])
    vectors = [[number(v) for v in line.split()[1:]] for line in lines[3:6]]
    counts = [int(line.split()[0]) for line in lines[3:6]]
    if atoms != 2 or any(v <= 0 for v in counts) or any(len(v) != 3 for v in vectors):
        raise ValueError('unexpected QE cube grid')
    a, b, c = vectors
    volume = abs(a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0]))
    values = [number(v) for line in lines[6+atoms:] for v in line.split()]
    if len(values) != math.prod(counts) or volume <= 0:
        raise ValueError('incomplete QE density grid')
    return sum(values)*volume


def fixture():
    return json.loads((CONTROL / 'qe_fixture.json').read_text())


def prepare_case(directory):
    directory.mkdir(parents=True)
    data = fixture()
    for name, content in (('scf.in', data['scf']), ('ph.in', data['phonon']),
                          ('Si.pz-vbc.UPF', data['pseudo']), ('pp.in', PP_INPUT)):
        (directory / name).write_text(content)


def recheck(directory, *, gpu):
    data = fixture()
    rows = {}
    for ranks in (1, 2):
        case = directory / ('mpi' + str(ranks))
        for name, expected in (('scf.in', data['scf']), ('ph.in', data['phonon']),
                               ('Si.pz-vbc.UPF', data['pseudo']), ('pp.in', PP_INPUT)):
            if (case / name).read_text() != expected:
                raise ValueError('QE fixture changed: ' + name)
        text = (case / 'pw.out').read_text()
        row = parse_scf(text)
        if abs(row['energy_ry'] - data['energy_ry']) > data['energy_tolerance_ry']:
            raise ValueError('QE energy differs from the upstream silicon reference')
        if max(abs(v) for force in row['forces_ry_bohr'] for v in force) > 1e-5:
            raise ValueError('QE silicon reference forces are not zero')
        if gpu and (not re.search(r'GPU acceleration is ACTIVE', text, re.I) or
                    not re.search(r'launch CUDA kernel.*device=\d+', text)):
            raise ValueError('QE GPU result lacks actual kernel execution evidence')
        rows[str(ranks)] = row
    if abs(rows['1']['energy_ry'] - rows['2']['energy_ry']) > 2e-7:
        raise ValueError('QE one/two rank energies disagree')
    for left, right in zip(rows['1']['forces_ry_bohr'], rows['2']['forces_ry_bohr']):
        if max(abs(a-b) for a, b in zip(left, right)) > 1e-7:
            raise ValueError('QE one/two rank forces disagree')
    frequencies = parse_phonon((directory / 'mpi2/ph.out').read_text())
    if max(abs(a-b) for a, b in zip(frequencies, data['frequencies_cm1'])) > data['frequency_tolerance_cm1']:
        raise ValueError('QE phonon frequencies differ from the upstream reference')
    if 'JOB DONE.' not in (directory / 'mpi2/pp.out').read_text():
        raise ValueError('QE density postprocessing did not finish')
    electrons = cube_electrons((directory / 'mpi2/rho.cube').read_text())
    if abs(electrons - 8.0) > 5e-3:
        raise ValueError('QE postprocessed density does not integrate to eight electrons')
    return dict(scf=rows, frequencies_cm1=frequencies, density_electrons=electrons, gpu_executed=gpu)


def run(directory, launcher, gpu):
    directory = directory.resolve()
    env = dict(os.environ, OMP_NUM_THREADS='2', NVCOMPILER_ACC_NOTIFY='1')
    for ranks in (1, 2):
        case = directory / ('mpi' + str(ranks))
        prepare_case(case)
        programs = [('pw.x', 'scf.in', 'pw.out')]
        if ranks == 2:
            programs += [('ph.x', 'ph.in', 'ph.out'), ('pp.x', 'pp.in', 'pp.out')]
        trace = case / 'ranks'
        trace.mkdir()
        env['SAI_QE_TRACE_DIR'] = str(trace)
        for program, source, output in programs:
            with (case / output).open('x') as stream:
                subprocess.run(['mpirun', '-np', str(ranks), '--map-by', 'slot:PE=2', '--bind-to', 'core',
                                str(launcher), program, '-in', source], cwd=case, env=env,
                               stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=600)
    result = recheck(directory, gpu=gpu)
    (directory / 'science.json').write_text(json.dumps(result, sort_keys=True) + '\n')
    return result


def inspect(prefix, identity=None):
    prefix = Path(prefix)
    installed = {path.name for path in (prefix / 'bin').iterdir() if path.is_file() and os.access(path, os.X_OK)}
    if not set(PROGRAMS['qe']) <= installed:
        raise ValueError('missing site pwall tools: ' + str(sorted(set(PROGRAMS['qe']) - installed)))
    data = prefix / 'share/sai'
    if identity is None:
        identity = json.loads((data / 'release-identity.json').read_text())
        writing = False
    else:
        identity = validate_identity(identity)
        writing = True
    if identity['software'] != 'qe' or identity['install_prefix'] != str(prefix):
        raise ValueError('QE installation identity mismatch')
    cache = (data / 'CMakeCache.txt').read_text()
    for option in ('QE_ENABLE_MPI', 'QE_ENABLE_OPENMP', 'QE_ENABLE_HDF5', 'QE_ENABLE_SCALAPACK', 'QE_ENABLE_OSCDFT'):
        if not re.search('^' + option + ':BOOL=ON$', cache, re.M):
            raise ValueError('required QE build feature missing: ' + option)
    gpu = identity['target'] != 'dsprhbm'
    if gpu and ('QE_GPU_ARCHS:STRING=sm_70' not in cache or 'QE_ENABLE_MPI_GPU_AWARE:BOOL=ON' not in cache):
        raise ValueError('required QE V100/GPU-aware MPI build options missing')
    reports = {}
    for name in sorted(installed):
        binary = prefix / 'bin' / name
        ldd = subprocess.check_output(['ldd', binary], text=True)
        dynamic = subprocess.check_output(['readelf', '-d', binary], text=True)
        if 'not found' in ldd or re.search(r'\((?:RUNPATH|RPATH)\).*?/(?:workspace|input|control)/', dynamic):
            raise ValueError('QE has missing libraries or build-directory loader paths: ' + name)
        if name == 'pw.x':
            if 'libmpi.so.40' not in ldd or 'libscalapack' not in ldd:
                raise ValueError('QE MPI/ScaLAPACK runtime missing')
            if gpu and not all(lib in ldd for lib in ('libcublas', 'libcufft', 'libcusolver')):
                raise ValueError('QE CUDA runtime missing')
        reports[name] = dict(sha256=checksum(binary), ldd=ldd, dynamic=dynamic)
    if writing:
        paths = list(dict.fromkeys(v for v in os.environ['LD_LIBRARY_PATH'].split(':') if v))
        external = sorted(set('/opt/devtools/' + Path(v).parts[3] for v in paths if v.startswith('/opt/devtools/')))
        entry = validate_native_entry(dict(identity=identity, commands={n:'bin/'+n for n in sorted(installed)},
            external_roots=external, runtime=dict(modules=MODULES,
                prepend=dict(PATH=[str(prefix/'bin')], LD_LIBRARY_PATH=paths), set=dict(QE_ROOT=str(prefix)))))
        for name, value in (('release-identity.json', identity), ('native-entry.json', entry), ('binary-inspection.json', reports)):
            (data/name).write_text(json.dumps(value, sort_keys=True)+'\n')
        write_manifests([entry])
    else:
        entry = validate_native_entry(json.loads((data/'native-entry.json').read_text()))
        if read_installed_manifests([entry]) != inventory([entry]):
            raise ValueError('QE installed inventory changed')
    return dict(executables=sorted(installed), gpu=gpu)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('inspect', 'run'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--identity')
    parser.add_argument('--launcher', type=Path)
    parser.add_argument('--gpu', action='store_true')
    args = parser.parse_args()
    print(json.dumps(inspect(args.directory, json.loads(args.identity) if args.identity else None)
                     if args.operation == 'inspect' else run(args.directory, args.launcher, args.gpu)))
