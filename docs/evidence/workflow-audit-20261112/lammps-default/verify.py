import hashlib,json,re,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/"control"))
import nepadapters_science as science
from source_cache import checksum
def require(condition,message):
 if not condition: raise ValueError(message)
def read(path): return json.loads(path.read_text())
job=(root/"job.id").read_text().strip()
accounting=subprocess.check_output(["sacct","-X","-n","-P","-j",job,"-o","JobIDRaw,State,ExitCode,Partition,Account,WorkDir,NodeList,Elapsed"],text=True)
record,=[line.split("|") for line in accounting.splitlines() if line.split("|")[0]==job]
require(record[1:6]==["COMPLETED","0:0","16V100","proj-sai-hpc-software",str(root)],"job incomplete or wrong allocation")
for name,sha in read(root/"request.json")["files"].items():
 require(checksum(root/name)==sha,"frozen input changed: "+name)
require((root/"module-unload.passed").is_file(),"unload failed")
script=(root/"job.sbatch").read_text()
require("--ignore_cache" not in script and "LMOD_IGNORE_CACHE" not in script,"cache bypass in job")
config=(root/"lmod-config.txt").read_text()
require(re.search(r"Ignore Cache \(LMOD_IGNORE_CACHE\)\s+no",config) is not None,"ignore cache enabled")
require(re.search(r"Cached loads \(LMOD_CACHED_LOADS\)\s+no",config) is not None,"cached loads altered")
lammps="lammps/30Sep2026_update1-deepmd320-auto"
nep="nepadapters/1.0.5-lammps30Sep2026_update1"
for name,expected in (("lammps-module-list.txt",[lammps]),("nepadapters-module-list.txt",[lammps,nep])):
 lines=(root/name).read_text().splitlines()
 require(all(n in lines for n in expected),"wrong default selection")
for name,expected in (("lammps-selection.txt",[lammps]),("nepadapters-selection.txt",[lammps,nep])):
 text=(root/name).read_text()
 require(all(str(root/"stage/opt/modules/modulefiles/apps"/n) in text for n in expected),"wrong preview module path")
before=read(root/"global-cache-before.json")
after={}
for path in Path("/opt/modules/lmod/cache").iterdir():
 if path.is_file():
  st=path.stat();after[str(path)]=dict(sha256=checksum(path),mtime_ns=st.st_mtime_ns,size=st.st_size,uid=st.st_uid)
require(before==after,"global cache changed during verification")
package=Path("/home/stardust/sai-hpc-software/deployment/lammps-update1-default")
manifest=read(package/"manifest.json")
lmp_release=read(Path("/home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1/release.json"))
row=next(r for r in lmp_release["deliveries"] if r["partition"]=="16V100")
expected_binary=next(r["sha256"] for r in row["files"] if r["path"]=="bin/lmp")
expected_plugins={Path(k).name:v["sha256"] for k,v in manifest["plugin"]["files"].items() if k.startswith("lib/")}
report=read(root/"acceptance.json")
require(report["passed"] is True and report["job"]==job and report["route"]=="normal-default-module-load","wrong native receipt")
require(report["lammps_prefix"]==row["prefix"] and report["binary_sha256"]==expected_binary and report["plugin_sha256"]==expected_plugins,"wrong native bytes")
out=root/"single-gpu";cases=root/"cases"
h=science.helper(cases);fixture=h.read_fixture(cases/"train.xyz")
screen=(out/"screen.out").read_text()
h.validate_startup_banner(screen,"nep/gpu");science.check_gpu_banner(screen)
observed=science.compare_raw(h,fixture,out)
require(observed==report["comparison"],"raw numerical output differs")
ranks=[read(p) for p in (out/"ranks").glob("*.json")]
require(ranks==report["ranks"] and len(ranks)==1 and ranks[0]["sm"]==70 and ranks[0]["binary_sha256"]==expected_binary,"wrong binary/GPU rank")
result=dict(passed=True,job=job,accounting=record,route="normal-default-module-load",
 defaults=dict(lammps=lammps,nepadapters=nep),global_cache_unchanged=True,ignore_cache=False,cached_loads=False,
 binary_sha256=expected_binary,plugin_sha256=expected_plugins,comparison=observed,
 scope="one GPU Pb/Te NEP4 numerical validation through default modules; prior full three-partition matrix retained",
 request_sha256=checksum(root/"request.json"),acceptance_sha256=checksum(root/"acceptance.json"),
 files={str(p.relative_to(root)):checksum(p) for p in [root/"lmod-config.txt",root/"lammps-module-list.txt",root/"nepadapters-module-list.txt",root/"lammps-selection.txt",root/"nepadapters-selection.txt",root/"global-cache-before.json",out/"screen.out",out/"log.lammps",out/"dump.out",*(out/"ranks").glob("*.json")]})
(root/"verification.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps(result,indent=2))
