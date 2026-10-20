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

The recorded mount tables show another site-specific distinction: the physical
`/opt/apps` installation is on NFS, while the SIF and bound home working
directories are on Lustre. This measures the practical deployment routes at
this site, not a packaging-only comparison on identical backing storage. Warm
repetitions reduce loading effects but do not remove that distinction. Inputs
and outputs use the same Lustre filesystem in both routes.

The [completed four-partition report](../../docs/evidence/cp2k-container-overhead-20261020/README.md)
contains all 112 scientific calculations, the separate 20-pair startup check,
raw evidence and an offline revalidator. It also records storage variability
and the failed original 4V100 native attempt before the successful retry.

`multinode.py` extends the comparison to two 16V100 nodes, four MPI ranks and
four allocated V100s per node, with four physical cores/OpenMP threads per rank.
It reuses the accepted 64-water OT input and the accepted 216-water OT/ELPA
input, including their original restart I/O. Each size has one excluded warmup
per route and five alternating measured pairs. This compares deployment routes;
it does not compare eigensolver choices or change CP2K defaults.

```bash
python3 -B deployment/cp2k-container-overhead/multinode.py submit \
  /home/stardust/sai-hpc-software/runtime-tests/cp2k-container-overhead-20261020-16V100 \
  --ot-parent /home/stardust/sai-hpc-software/runtime-tests/cp2k-direct-39365139034-16v100-avx2-ot-water216 \
  --name UNIQUE_RUN_NAME
```

An ordinary Slurm step inventories each node's allocated devices through the
CUDA driver and device cgroup, requiring exactly four visible devices before
proceeding. Numeric `CUDA_VISIBLE_DEVICES` is omitted only for this inventory;
its original value is archived. Every rank selects its own node's GPU UUID; it does not reuse the
first node's numeric GPU indices. All eight ranks prove physical-core binding,
ordinary UID, host working-directory identity and unchanged placement between
samples. Native/SIF probes check executable bytes on every rank, library/data
bytes on each node, actual SIF mounts, and exactly one visible GPU per rank.
The same instrumentation runs in both routes; individual rank lifetimes and
external MPI wall time are recorded separately. All temporary files remain in
the project home. Full SIF hashing occurs before submission, outside timing.

After completion, save accounting as described above and run the frozen
`multinode.py verify RUN`. It reparses all selected calculations, convergence, energies,
forces, SCF iteration counts and rank/resource evidence before reporting timings.
Both sizes together produce 24 calculations. To run them concurrently in separate
allocations, add `--case water64-gpw` or `--case water216-ot` (12 calculations
each), use different names, and pass the first allocation's hosts to the second
submission's `--exclude NODE1,NODE2`. Each job allows up to four hours within the
ordinary `flood-gpu` QoS; faster completion releases its allocation immediately.

The [completed two-node report](../../docs/evidence/cp2k-container-multinode-20261021/README.md)
contains all 24 calculations for 64-water OT/cuSOLVER and 216-water OT/ELPA,
with independent remote/local raw revalidation. For 216 water, external wall
medians were 223.874 s native and 224.633 s SIF (+0.34%); the paired delta
median was -2.248 s. The 64-water case had a strong time trend in cuSOLVER
calls, so its -7.13% median ratio is not evidence of container acceleration.
All individual pairs, failed initial attempts and topology-copy provenance
are preserved.
