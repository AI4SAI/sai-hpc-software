"""Use the installed runtime, oracle and LAMMPS for the six-atom SOC fixture."""
from pathlib import Path
import hashlib,json,os,re,shutil,subprocess,sys,time
import numpy as np
import spingnn2
from spingnn2.native import NativeModel
from spingnn2.reference import ReferenceModel
from spingnn2.jit import prepare_jit_artifact

case=Path.cwd();prefix=Path(os.environ['SPINGNN2_ROOT'])
fixture=json.loads((case/'fixture.json').read_text())
checks={name:dict(expected=digest,actual=hashlib.sha256((case/name).read_bytes()).hexdigest())
        for name,digest in fixture['hashes'].items()}
assert all(row['expected']==row['actual'] for row in checks.values()),checks
(case/'fixture-manifest-check.json').write_text(json.dumps(dict(passed=True,files=checks),indent=2)+'\n')
assert Path(spingnn2.__file__).resolve().is_relative_to(prefix/'venv')
assert 'torch' not in sys.modules
binary=Path(shutil.which('lmp_spingnn2'))
assert binary.resolve()==prefix/'bin/lmp_spingnn2'
backend=os.environ['SPINGNN2_BACKEND']
threads=int(os.environ['OMP_NUM_THREADS'])
model=case/'model.sp2.json'
options=dict(backend=backend,precision='float64',cache_dir=case/'jit',library=os.environ['SPINGNN2_LIBRARY'])
if backend=='cuda': options['gpu_arch']='sm_'+os.environ['SPINGNN2_ARCH'].removeprefix('sm')
else: options['cxx']=os.environ['CXX']
artifact=prepare_jit_artifact(model,**options)
shutil.copyfile(artifact,case/'artifact.json')
desc=json.loads(artifact.read_text())
shutil.copyfile(artifact.parent/desc['artifact'],case/desc['artifact'])
# Project default ACLs also reach newly generated cache files. Restrict only
# this job's private JIT files, preserving the upstream permission checks.
artifact.parent.chmod(0o700)
artifact.chmod(0o400)
(artifact.parent/desc['artifact']).chmod(0o400)
(case/'artifact.json').chmod(0o400)
(case/desc['artifact']).chmod(0o400)
(case/'host-isa.txt').write_text(subprocess.check_output([os.environ['CXX'],'-march=native','-Q','--help=target'],text=True))
(case/'host-isa.txt').write_text(subprocess.check_output(['g++','-march=native','-Q','--help=target'],text=True))
rows=[]
fields=['total_energy','atomic_energy','forces','spin_forces','spin_forces_vert','virial']
for variant in ('baseline','rotated'):
    suffix='' if variant=='baseline' else '-rotated'
    graph=dict(np.load(case/f'graph{suffix}.npz'))
    expected=dict(np.load(case/f'reference{suffix}.npz'))
    oracle=ReferenceModel.load(model).evaluate(graph)
    np.savez(case/f'oracle-installed{suffix}.npz',**{k:oracle[k] for k in fields})
    start=time.perf_counter()
    with NativeModel.load(model,backend=backend,precision='float64',execution_mode='direct',artifact=artifact) as native:
        actual=native.evaluate(graph)
        diagnostics=native.diagnostics
        assert native.backend==backend
        (case/f'loaded-libraries{suffix}.txt').write_text(Path('/proc/self/maps').read_text())
    elapsed=time.perf_counter()-start
    np.savez(case/f'native{suffix}.npz',**{k:actual[k] for k in fields})
    native_errors={k:float(np.max(np.abs(actual[k]-expected[k]))) for k in fields}
    oracle_errors={k:float(np.max(np.abs(oracle[k]-expected[k]))) for k in fields}
    assert max(native_errors.values())<1e-10,native_errors
    assert max(oracle_errors.values())<1e-10,oracle_errors
    creates=[]
    for i,(pos,spin,typ) in enumerate(zip(graph['pos'],graph['spin'],graph['atom_types'])):
        creates += [f'create_atoms {typ+1} single '+' '.join(map(str,pos))+' units box',
                    f'set atom {i+1} spin/atom 1 '+' '.join(map(str,spin))]
    inp='\n'.join(['units metal','atom_style spin','atom_modify map array','boundary p p p','newton on',
        'region box block 0 16 0 16 0 16 units box','create_box 2 box',*creates,'mass 1 55','mass 2 81',
        'pair_style spingnn2 precision float64 math ieee profile yes',
        f'pair_coeff * * "{model}" "{artifact}" 0 1','neighbor 0.7 bin',
        'neigh_modify every 1 delay 0 check yes','compute sf all property/atom fmx fmy fmz',
        'thermo_style custom step pe','thermo 1','thermo_modify norm no format float %.17g',
        'dump snapshot all custom 1 result.dump id fx fy fz c_sf[*]',
        'dump_modify snapshot sort id format float %.17g','run 0',''])
    work=case/f'lammps-{variant}';work.mkdir()
    (work/'in.spin').write_text(inp)
    cmd=['mpirun','-np','1','--map-by',f'ppr:1:node:pe={threads}','--bind-to','core',str(binary),
         '-log','log.lammps','-in','in.spin']
    if backend=='cuda': cmd += ['-k','on','g','1','t',str(threads),'-sf','kk','-pk','kokkos','gpu/aware','off','neigh','half']
    start=time.perf_counter()
    proc=subprocess.run(cmd,cwd=work,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    wall=time.perf_counter()-start
    (work/'stdout.log').write_text(proc.stdout)
    (work/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
    (work/'exitcode.txt').write_text(str(proc.returncode)+'\n')
    assert proc.returncode==0,proc.stdout
    energies=re.findall(r'^\s*0\s+([-+0-9.eE]+)\s*$',proc.stdout,re.M)
    assert len(energies)==1,energies
    energy=float(energies[0]);values=np.loadtxt(work/'result.dump',skiprows=9)
    assert values.shape==(6,7),values.shape
    forces=values[:,1:4]
    spin_forces=values[:,4:7]*(4.135667403e-3/(2*np.pi))
    errors=dict(energy_ev=abs(energy-float(expected['total_energy'].item())),
                force_ev_angstrom=float(np.max(np.abs(forces-expected['forces']))),
                spin_force=float(np.max(np.abs(spin_forces-expected['spin_forces']))))
    assert max(errors.values())<=1e-11,errors
    rows.append(dict(variant=variant,energy_ev=energy,native_energy_ev=float(actual['total_energy'].item()),
        reference_energy_ev=float(expected['total_energy'].item()),max_force=float(np.max(np.abs(forces))),
        max_spin_force=float(np.max(np.abs(spin_forces))),native_errors=native_errors,oracle_errors=oracle_errors,
        lammps_errors=errors,exitcode=proc.returncode,native_wall_s=elapsed,lammps_wall_s=wall,
        native_diagnostics=diagnostics))
assert 'torch' not in sys.modules
response=rows[1]['energy_ev']-rows[0]['energy_ev']
expected_response=rows[1]['reference_energy_ev']-rows[0]['reference_energy_ev']
assert abs(response)>1e-8 and abs(response-expected_response)<2e-11
result=dict(passed=True,backend=backend,partition=os.environ.get('SLURM_JOB_PARTITION'),job_id=os.environ.get('SLURM_JOB_ID'),host=os.uname().nodename,
    affinity=sorted(os.sched_getaffinity(0)),python=sys.executable,spingnn2_file=spingnn2.__file__,
    version=spingnn2.__version__,torch_imported=False,rows=rows,spin_rotation_energy_change_ev=response,
    tolerances=dict(native_fp64=1e-10,lammps_fp64=1e-11),
    environment={k:os.environ.get(k) for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS','MPI_HOME','SPINGNN2_LIBRARY')},
    artifact=str(artifact),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
    library_sha256=hashlib.sha256(Path(os.environ['SPINGNN2_LIBRARY']).read_bytes()).hexdigest())
(case/'acceptance.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
