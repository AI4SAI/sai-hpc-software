import json,os,shutil,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/"control"))
import nepadapters_science as science
from source_cache import checksum
root=Path.cwd()
cases=root/"cases"
h=science.helper(cases)
fixture=h.read_fixture(cases/"train.xyz")
for atom in fixture["atoms"]:
    atom["position"]=[x+5.0 for x in atom["position"]]
elements=h.read_type_map(cases/"nep.txt")
out=root/"single-gpu";out.mkdir()
h.write_data(out/"data.baseline",fixture,elements)
h.write_input(out/"in.baseline","",cases/"nep.txt",elements,"nep/gpu","environment","off")
binary=Path(os.environ["SAI_LAMMPS_PREFIX"])/"bin/lmp"
plugins=Path(os.environ["SAI_NEPADAPTERS_PREFIX"])/"lib"
command=[shutil.which("mpirun"),"-np","1","--map-by","ppr:1:node:PE=1","--bind-to","core",
    "/usr/bin/python3","-B",str(root/"control/nepadapters_rank.py"),"1",str(binary),
    "-k","on","g","1","-pk","kokkos","neigh","full","comm","device","gpu/aware","on",
    "-in","in.baseline","-log","log.lammps"]
with (out/"screen.out").open("x") as stream:
    subprocess.run(command,cwd=out,stdout=stream,stderr=subprocess.STDOUT,check=True)
screen=(out/"screen.out").read_text()
h.validate_startup_banner(screen,"nep/gpu")
science.check_gpu_banner(screen)
comparison=science.compare_raw(h,fixture,out)
ranks=[json.loads(p.read_text()) for p in (out/"ranks").glob("*.json")]
assert len(ranks)==1 and ranks[0]["sm"]==70 and ranks[0]["binary"]==str(binary)
report=dict(passed=True,job=os.environ["SLURM_JOB_ID"],route="normal-default-module-load",
    lammps_prefix=str(binary.parent.parent),plugin_prefix=str(plugins.parent),
    comparison=comparison,command=command,ranks=ranks,binary_sha256=checksum(binary),
    plugin_sha256={p.name:checksum(p) for p in plugins.glob("*plugin.so")},
    case_sha256={name:checksum(cases/name) for name in science.CASE_SHA256})
(root/"acceptance.json").write_text(json.dumps(report,indent=2)+"\n")
print("NORMAL_DEFAULT_LOAD_AND_NEP_GPU_PASSED")
