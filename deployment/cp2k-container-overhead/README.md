# CP2K native versus SIF runtime overhead

This experiment uses the accepted shared-ELPA CP2K 2026.2 release, one ordinary
Slurm user, one MPI rank and one V100 per GPU partition (two CPU cores on
DSPRHBM). The host Open MPI launcher, physical CPU affinity, OpenMP settings,
executable, linked library bytes and original CP2K data are checked for equality.
The GPU experiments use four physical cores/threads on 4V100 and 16V100, three
on 8V100V0. OpenBLAS and MKL use one thread in both routes.

`native` executes the public physical installation. `sif` uses ordinary
`apptainer exec --nv` (without `--nv` on CPU), with the application and its
private libraries read from the immutable SIF. Input/output directories and MPI
scratch are bind-mounted at their original physical home paths. No sudo,
fakeroot, public `/opt` edits, writable image, or node `/tmp` storage is used.
Probes record the actual nonzero UID, mount table, file device/inode, affinity,
library hashes and host work-directory identity.

The same original small PBE, 64-water OT and 64-water ELPA inputs are reused.
Each route has one excluded warmup, followed by alternating native/SIF order:
five measured pairs for small PBE, three for each 64-water case. Every result
must converge and agree in energy/forces before timings are summarized. The
report preserves individual samples, paired wall-time differences, CP2K's
internal total and the time outside that timer. SCF iteration counts are
compared; timing variation is retained, not attributed automatically to SIF.

Startup additionally compares `native`, direct `sif` and the existing checked
repository launcher with `--version`: one first invocation plus seven warmed
repetitions each. The checked launcher's first invocation can include its image
checksum; subsequent invocations use its existing per-job/per-host cache.
The direct route's full SIF integrity check happens on the login node before
submission, outside timing. Image identity is checked again before and after
execution. No cache is flushed, so first observed startup is not a controlled
cold-cache measurement. Container overhead includes mounting, environment/driver
setup and loading as well as reading executable bytes.

Run in the cluster home checkout:

```bash
python3 -B deployment/cp2k-container-overhead/run.py submit \
  --partition 16V100 --name UNIQUE_RUN_NAME
```

The submitter freezes its inputs, scientific parser, launchers and metadata
under `~/sai-hpc-software/runtime-tests/`. After the job ends, save
`sacct -X -j JOB -n -P -o JobID,Partition,State,ExitCode,Elapsed,NodeList`
as `results/accounting.txt` and use the frozen runner's `verify RUN` command.
Verification reparses all raw calculations and requires terminal `COMPLETED 0:0`.

For a finer startup measurement, the supplementary `startup.py` experiment uses
20 alternating native/SIF pairs after one excluded invocation of each route.
GNU `time` runs **inside** the host MPI rank, surrounding the common rank wrapper.
It records rank wall time, user/system CPU time and peak RSS alongside external
wall time. This removes the outer MPI launch/teardown portion from the rank
measurement; CP2K's own initialization and any container setup remain included.
The current submit helper targets a GPU partition and excludes the main
experiment's node so the two measurements do not compete on that node:

```bash
python3 -B deployment/cp2k-container-overhead/startup.py submit \
  /home/stardust/sai-hpc-software/runtime-tests/MAIN_RUN \
  --name UNIQUE_STARTUP_NAME
```

Save accounting in the same format, then use the startup run's own frozen
`startup.py verify RUN`. Neither experiment isolates pure storage read latency:
the practical SIF route also creates mounts and a process environment, exposes
host drivers, and decompresses image pages when needed. All measured routes
use a warm filesystem cache to an uncontrolled extent; no cold-start claim is
made. One rank/node does not establish overhead at many-rank or many-node scale.
