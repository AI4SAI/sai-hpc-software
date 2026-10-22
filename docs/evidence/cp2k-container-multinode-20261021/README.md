# CP2K 2026.2: native versus SIF on two nodes and eight V100s

**Both workloads completed: all 24 calculations passed**, including four
excluded warmups and 20 measured samples. Independent local and cluster
revalidation agrees after checking **2,063 archived file hashes**, raw numerical
outputs, all rank placements and successful terminal accounting.

For **216 water molecules (648 atoms)**, external wall-time medians were
**223.874 s native and 224.633 s SIF (+0.34%)**. The paired difference median
was **-2.248 s**, with differences of both signs. These five pairs show no
clear SIF performance loss for this workload and placement; they do not
establish a universal overhead bound.

The retained 64-water input has a pronounced time trend in its cuSOLVER calls.
Its wall-time medians were **384.479 s native and 357.066 s SIF (-7.13%)**, but
the paired difference median was only **-3.679 s**. The last two pairs differed
by -3.679 s and +1.877 s. The observed negative median ratio is not evidence
that SIF accelerates CP2K; variability in the calculation dominates this case.

| Workload | Job | Nodes | Elapsed | Calculations | State |
| --- | --- | --- | --- | ---: | --- |
| 64-water OT/cuSOLVER | 1884803 | 16v100n45, 16v100n46 | 01:34:53 | 12 | COMPLETED 0:0 |
| 216-water OT/ELPA | 1884745 | 16v100n13, 16v100n14 | 00:47:03 | 12 | COMPLETED 0:0 |

Values below are seconds, excluding warmups. The ratio change is
`100 * (SIF median / native median - 1)`. The paired delta is the median of
the five individual SIF-minus-native differences, a different statistic.
No timing outliers were removed.

| Water | Metric | Native median (range) | SIF median (range) | Median ratio change | Paired delta median |
| ---: | --- | ---: | ---: | ---: | ---: |
| 64 | External wall | 384.479 (353.236–702.715) | 357.066 (354.107–600.848) | -7.13% | -3.679 |
| 64 | CP2K internal | 380.014 (348.427–695.687) | 352.344 (349.031–592.626) | -7.28% | -4.207 |
| 64 | Maximum rank lifetime | 382.790 (351.080–699.980) | 355.470 (352.270–597.360) | -7.14% | -3.660 |
| 216 | External wall | 223.874 (220.388–238.299) | 224.633 (212.858–226.329) | +0.34% | -2.248 |
| 216 | CP2K internal | 216.010 (208.355–232.032) | 216.614 (206.683–217.606) | +0.28% | -2.459 |
| 216 | Maximum rank lifetime | 221.130 (218.180–235.650) | 221.320 (210.080–223.610) | +0.09% | -2.480 |

All five external-wall pairs are listed in acquisition order below. A pair's
routes run sequentially on the same allocation; the two workloads have
separate allocations. N/S means native then SIF; S/N reverses that order.

| Pair | Order | 64 native | 64 SIF | 64 delta | 216 native | 216 SIF | 216 delta |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | N/S | 702.715 | 600.848 | -101.867 | 238.299 | 225.514 | -12.785 |
| 2 | S/N | 433.107 | 451.899 | +18.792 | 222.429 | 226.329 | +3.900 |
| 3 | N/S | 384.479 | 357.066 | -27.413 | 226.018 | 223.770 | -2.248 |
| 4 | S/N | 357.786 | 354.107 | -3.679 | 223.874 | 224.633 | +0.759 |
| 5 | N/S | 353.236 | 355.113 | +1.877 | 220.388 | 212.858 | -7.530 |

Every calculation used two outer SCF iterations and **107 total inner steps**;
the final inner convergence line alone reports seven steps. Energies and forces
agree with accepted references and the same-workload native warmup. The maximum
energy/force differences were **5.37e-11 Ha / 1.30e-11 Ha/Bohr** for 64 water
and **8.82e-11 Ha / 1.60e-11 Ha/Bohr** for 216 water.

The 64-water `cp_fm_diag_cusolver` maximum routine times fell from 660.250 to
314.339 s across native samples, and from 555.402 to 313.689 s across SIF
samples (112 calls per calculation). This accounts for most of the whole-run
time trend. Restart-writing timers also varied. In the 216-water samples,
`cp_fm_diag_elpa` ranged from 40.119 to 50.269 s, while restart-writing time
ranged from 3.088 to 18.139 s. Routine timers include the work/waits inside
those calls; they do not identify a unique cause of the variability. They are
retained without subtracting them to construct a corrected benchmark.

The experiment extends the [single-GPU measurements](../cp2k-container-overhead-20261020/README.md)
to **two 16V100 nodes, four allocated GPUs per node**. The accepted 64-water OT
input is retained and a 216-water OT input is added. Each workload has its own
two-node allocation: `1884803` on `16v100n45/46` for 64 water, and `1884745` on
`16v100n13/14` for 216 water. The allocations do not share nodes. Every
calculation uses eight MPI ranks, one GPU and four physical CPU cores/OpenMP
threads per rank; OpenBLAS and MKL each use one thread.

Both routes run as ordinary `stardust`, UID/EUID `1478400690`, and use the host
filesystem for inputs, outputs, restart files, MPI scratch and CUDA caches.
All scratch stays under `/home/stardust/sai-hpc-software`. The accepted release
is `v2026.2-g67b5da876dd6-raed7aa53ff43`; its identical absolute prefix inside
and outside the container is:

```
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100
```

`native` reads the public physical installation. `sif` uses ordinary
`apptainer exec --nv`, with executable, private libraries and original CP2K
data read from the immutable image. Host MPI, drivers and shared dependencies
are used in both routes. `/opt/apps` is not bind-mounted. Every rank proves its
nonzero user identity, CPU/GPU placement and the host working directory's
device/inode. CUDA driver probes require exactly one selected GPU per rank;
node inventories and UUID mapping prove four distinct GPUs per node and eight
overall. Each rank verifies the executable hash, and one rank per node verifies
all linked libraries and the scientific data. Both nodes must agree.

The workloads retain their previously accepted input bytes, including restart
I/O. Both are PBE/DZVP-MOLOPT-SR-GTH, 300 Ry, OT/DIIS with FULL_ALL
preconditioning and `EPS_SCF 1e-9`. The 64-water case uses its original cuSOLVER
selection; the 216-water case explicitly selects ELPA. Thus native/SIF results
are compared **within each workload**. Comparing the two sizes does not isolate
size scaling or establish an eigensolver preference. The 216-water case has
648 atoms, 1,728 electrons and 4,968 orbital basis functions. Complete outer
SCF iteration counts are checked as well as the last converged inner cycle.
Both native warmups used two outer iterations and 107 inner steps in total.
The 64-water native warmup spent 571.463 s in `cp_fm_diag_cusolver` out of a
606.667 s CP2K total; the 216-water native warmup spent 48.485 s in
`cp_fm_diag_elpa` out of 222.202 s. Each made 112 calls to the corresponding
routine. These excluded warmup diagnostics explain why the smaller case can
take longer here; they are not a controlled comparison of those backends.

Each workload has one excluded native/SIF warmup pair followed by five
measured pairs. Order alternates native/SIF, SIF/native, native/SIF,
SIF/native, native/SIF. No filesystem cache is flushed. External wall time
includes host MPI launch/teardown and the common instrumentation. GNU `time`
within each host MPI rank records its lifetime, including any Apptainer setup;
the maximum of the eight rank lifetimes is reported alongside CP2K's internal
total. Expensive provenance probes and full SIF hashing are outside these
timings. Numerical convergence, energy and forces must agree with accepted
references and the corresponding native warmup before a sample is accepted.

These are practical deployment measurements: physical `/opt/apps` is NFS,
whereas the SIF and work directories are on Lustre. Mounting, environment and
driver setup, image decompression and loading are included in the SIF route.
The difference is not a measurement of pure executable-read latency, and
negative differences do not by themselves establish container acceleration.
The two routes preserve the same restart I/O; routine timers are retained to
show variability without subtracting it from the measured totals.

Earlier attempts remain in the evidence. Jobs `1884418` and `1884477` stopped
before any scientific calculation, respectively on a GPU inventory count check
and because an unqualified MPI host list restricted available slots. Inventory
was corrected to enumerate the Slurm device cgroup without numeric CUDA index
filtering. The initial failing node's inventory was not saved before the
assertion; that observation cannot be reconstructed. Job `1884535` was deliberately
stopped after its native warmup: the initial 90-minute limit was insufficient, and an ordinary
user could not increase it. The workload jobs were resubmitted with four-hour
limits. Jobs `1884605` and `1884614` each finished both warmups but stopped on
an overly strict verification of a SIF mount device number, which can change
between Apptainer invocations. The corrected verifier checks stable resource
placement and that the executable comes from a filesystem distinct from the
physical installation. The original failed snapshots remain unchanged; their
completed warmups are independently reparsed but excluded from final timings.

The site's `/opt/sai_config/mps_mapping.d/_common.bash` removes its temporary
`.nccl_topo_JOB.xml` at shell exit. These frozen runners included that temporary
name in their final hash list as well as the permanent
`results/nccl-topology.xml` actually used by the calculations. The archive
collector restores only the missing temporary-name copy, only after its
preserved bytes match the original recorded SHA-256. The reconstruction is
explicitly listed in `raw/reconstructed-topology.json` and independently checked
by the revalidator; original run directories and frozen hash lists are not
modified. Future runner versions record only the permanent copy after verifying
the two copies agree. This change passed the targeted 10-test suite.

The [benchmark package](../../../deployment/cp2k-container-overhead/README.md)
documents submission. [`manifest.json`](manifest.json) identifies all main and
excluded jobs. [`collect.py`](collect.py) retains their inputs, frozen scripts,
probes, logs, forces and accounting. [`revalidate.py`](revalidate.py) hashes and
reparses the complete archive and checks that the main allocations are disjoint.
[`monitor.py`](monitor.py) takes optional read-only GPU/process snapshots during
excluded warmups only; instantaneous utilization is not a whole-run average.

Reproduce the offline check from the repository root:

```bash
python3 -B docs/evidence/cp2k-container-multinode-20261021/revalidate.py
```

[`local-validation.json`](local-validation.json) and
[`remote-validation.json`](remote-validation.json) match exactly. They contain
all three timing metrics, every measured value and paired difference, numerical
maxima, selected routine timers, topology reconstruction records and monitoring
checks. [`raw/SHA256.json`](raw/SHA256.json) pins all retained inputs, scripts,
probes, output logs, forces and accounting. The 5,034,109-byte archive is retained
under the cluster project's home diagnostics; its path and SHA-256 are recorded
in [`archive.json`](archive.json). The evidence includes four failed initial SSH
monitoring attempts (host-key verification) and eight successful snapshots;
only the successful snapshots establish CP2K GPU contexts. No public software,
modules or cluster policies were changed for this experiment.
