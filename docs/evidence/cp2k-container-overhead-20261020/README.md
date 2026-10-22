# CP2K 2026.2: ordinary-user native versus SIF execution

The experiment compares the accepted shared-ELPA CP2K 2026.2 executable in
the physical `/opt/apps` installation with the same executable and private
libraries read from its immutable SIF. Both routes use physical home directories
for input, output, CUDA cache and MPI scratch. The account is `stardust`, UID/EUID
`1478400690`; no sudo, fakeroot, public installation changes or node `/tmp` are
used. The [benchmark scripts](../../../deployment/cp2k-container-overhead/README.md)
are reusable.

**Completed on all four partitions: 112 scientific calculations passed.**
Independent remote and local revalidation produced identical results after
checking 2,251 archived file hashes and reparsing the original outputs. The
20-pair startup experiment measured a **0.400 s median additional rank lifetime**
for direct SIF execution. The 64-water OT wall-time medians increased by
**0.82% on 16V100** and **0.43% on 8V100V0**. Other cases experienced substantial
storage variability; the complete observations below do not establish a
universal container overhead percentage.

The accepted CP2K identity is
`v2026.2-g67b5da876dd6-raed7aa53ff43`. Both routes use the same absolute prefix,
`/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/<PARTITION>`.
Host MPI and external dependencies are shared; `/opt/apps` is not bound into
the container, so its CP2K executable, private libraries and CP2K data really
are read from SIF.

| Partition | Job | Node | Elapsed | Scientific calculations | State |
| --- | --- | --- | --- | ---: | --- |
| 4V100 | 1882248 | 4v100n33 | 00:46:08 | 28 | COMPLETED 0:0 |
| 16V100 | 1881686 | 16v100n02 | 00:34:37 | 28 | COMPLETED 0:0 |
| 8V100V0 | 1882053 | 8v100v0n03 | 00:51:38 | 28 | COMPLETED 0:0 |
| DSPRHBM | 1882051 | d9470n02 | 01:46:10 | 28 | COMPLETED 0:0 |

One MPI rank is used on each node. GPU cases use one V100, four physical
cores/OpenMP threads on 4V100 and 16V100, and three on 8V100V0. DSPRHBM uses
two physical cores/threads. Each route has one excluded warmup; small PBE has
five measured alternating pairs, and 64-water OT and 64-water ELPA each have
three. Input bytes, SCF convergence/iteration counts, energies and forces are
checked against accepted references. Executable and all linked-library hashes,
CPU affinity and relevant environment must match within each native/SIF pair.
Mount records prove that application/private libraries come from SIF and the
container working directory has the same device/inode as the host directory.
The largest energy and force differences from the corresponding native warmup
are **5.18e-11 Ha** and **1.60e-11 Ha/Bohr**, respectively; all comparisons with
the accepted scientific references also pass. The 112 calculations include 24
excluded warmups and 88 measured samples. Six additional diagnostic calculations
on another 4V100 node are archived separately.

The following startup measurements include host MPI launch/teardown. Warm
medians use seven repetitions per route. The last column is the median of
within-repetition SIF-minus-native differences, which need not equal the
difference between the two medians. All values are seconds.

| Partition | First native | First SIF | Warm native median | Warm SIF median | Warm checked-launcher median | Paired SIF delta median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 4V100 | 0.865 | 4.369 | 0.715 | 0.915 | 1.015 | +0.200 |
| 16V100 | 2.620 | 4.072 | 2.670 | 3.221 | 3.171 | +0.300 |
| 8V100V0 | 2.021 | 4.125 | 2.019 | 2.472 | 2.670 | +0.451 |
| DSPRHBM | 1.268 | 5.377 | 0.969 | 1.472 | 1.670 | +0.503 |

The main startup experiment runs `--version` once, followed by seven rotating
repetitions of native, direct SIF and the checked repository launcher. A separate
16V100 job, `1882107` on `16v100n08`, surrounds the common rank wrapper with GNU
`time` inside host MPI: 20 alternating measured pairs plus one excluded pair.
Its independently revalidated rank lifetime medians are **1.575 s native** and
**1.985 s SIF**. The paired difference median is **0.400 s**, mean **0.406 s**,
range **-0.010 to +0.860 s**. External wall-time paired difference is **0.451 s**.
The rank measurement excludes outer MPI launch/teardown, while retaining CP2K
initialization and container setup. It does not isolate pure executable reads.

The first observed SIF invocation adds about 1.5–4.1 seconds across the four
jobs. No cache is flushed, so this is **not a controlled cold-cache result**.
Full direct-route image integrity verification occurs before submission,
outside timing; image stat identity is checked before and after execution. The
checked repository launcher has an existing per-job/per-host checksum cache;
it does not hash the whole image on every call.

The actual storage layout also limits causal interpretation: physical
`/opt/apps` is NFS, while SIFs and both routes' work directories are on Lustre.
These are measurements of the site's practical deployment routes, not a
packaging-only experiment with identical backing storage. One-rank/node results
do not establish multi-rank startup cost or multi-node scaling.

All measured scientific wall times are listed below, excluding warmups. Values
are **median (minimum–maximum), in seconds**, including host MPI startup and
teardown. Small PBE uses five pairs; the 64-water cases use three pairs each.
The percentage is `100 * (SIF median / native median - 1)`, an observed ratio,
not an isolated container penalty. The last column independently reports the
median paired SIF-minus-native difference, in seconds. In particular, the
16V100 and DSPRHBM OT paired differences and ratios of medians have opposite
signs; they are different statistics over variable samples.

| Partition | Case | Native median (range) | SIF median (range) | Median ratio change | Paired delta median |
| --- | --- | ---: | ---: | ---: | ---: |
| 4V100 | Small PBE | 9.430 (9.428–9.529) | 9.730 (9.729–9.930) | +3.19% | +0.301 |
| 4V100 | 64-water OT | 376.715 (346.904–403.683) | 261.515 (220.605–286.921) | -30.58% | -116.761 |
| 4V100 | 64-water ELPA | 21.830 (21.542–53.324) | 23.344 (21.943–46.716) | +6.93% | +0.401 |
| 16V100 | Small PBE | 20.424 (20.125–20.522) | 20.926 (20.570–22.223) | +2.46% | +0.697 |
| 16V100 | 64-water OT | 171.528 (169.567–176.635) | 172.930 (164.708–174.175) | +0.82% | -2.460 |
| 16V100 | 64-water ELPA | 39.398 (37.894–39.444) | 39.796 (38.292–40.097) | +1.01% | +0.398 |
| 8V100V0 | Small PBE | 23.271 (23.172–23.522) | 23.973 (23.972–24.220) | +3.02% | +0.701 |
| 8V100V0 | 64-water OT | 173.320 (173.114–173.371) | 174.066 (173.675–174.167) | +0.43% | +0.848 |
| 8V100V0 | 64-water ELPA | 132.954 (85.217–198.832) | 128.369 (127.424–143.751) | -3.45% | -4.586 |
| DSPRHBM | Small PBE | 35.500 (34.039–44.061) | 36.348 (35.100–45.365) | +2.39% | +1.059 |
| DSPRHBM | 64-water OT | 521.768 (508.957–621.748) | 563.586 (473.482–564.844) | +8.01% | -48.287 |
| DSPRHBM | 64-water ELPA | 138.764 (112.653–237.276) | 171.138 (146.038–181.609) | +23.33% | +32.374 |

Several later scientific samples encountered substantial storage waits.
On 8V100V0, the 64-water ELPA native warmup took 44.485 s internally, while
the SIF warmup took 94.280 s; ELPA itself took **7.551/7.554 s**. SIF's
`write_mo_set_to_restart` timer was **22.717 s**, with **27.307 s** of CP2K
self time. In measured pair 0, native/SIF ELPA times were **7.518/7.476 s**,
while restart-writing times were **38.690/27.755 s**. An evidence collector
also waited on a source-file read in Lustre's `cl_sync_io_wait`. The raw routine
timers are retained alongside whole-program times. We do not subtract these
timers to manufacture a corrected benchmark, or interpret negative whole-run
differences as container acceleration.

The original 4V100 job `1882052` on `4v100n06` passed version and provenance
checks but segfaulted in its first **native** science calculation, before SIF
science began. DBCSR's caller is `libsmm_acc.cpp:397`, whose CUDA macro expands
to `nvrtcCompileProgram`. All six diagnostic calculations in job `1882162`
on `4v100n31` passed: native/SIF with four threads and default stack, four
threads and `OMP_STACKSIZE=256M`, and one thread/default stack. The complete
benchmark retry `1882248` on `4v100n33` passed without a stack workaround.

The failed node's probes recorded different `libnvrtc.so.12` and
`libcufft.so.11` bytes at the same shared CUDA paths. The login node and
`16v100n02`, `16v100n08`, `8v100v0n03`, `4v100n33` agree with each other;
native and SIF agree within every tested node. The cause of the `4v100n06`
difference is unresolved. A follow-up direct/cached read was refused by
`pam_slurm_adopt` because this account no longer had an active allocation
there. [`audit_cuda.py`](audit_cuda.py) is a read-only check for an authorized
allocation or administrator; it performs no library writes or explicit cache
eviction. No public library, module, or cluster policy was changed.

The original job controllers and failed outputs are preserved. Their science
verifier initially indexed the native warmup before inserting it into its
sample map. The corrected verifier is archived separately under
`raw/verifier/`, with its hash in [`manifest.json`](manifest.json); scientific
parsers are hash-matched to each frozen job. The correction changes verification
only. [`revalidate.py`](revalidate.py) hashes the complete archive and reparses
all numerical samples, timing records, rank placement and terminal accounting.
It also retains diagnostic routine timers without changing the measured totals.

Reproduce the offline check from the repository root:

```bash
python3 -B docs/evidence/cp2k-container-overhead-20261020/revalidate.py
```

The complete results are in [`local-validation.json`](local-validation.json)
and [`remote-validation.json`](remote-validation.json), including every measured
timing, per-partition numerical maxima and selected CP2K routine timers.
[`raw/SHA256.json`](raw/SHA256.json) pins the frozen inputs, probes, outputs,
controllers and accounting. [`archive.json`](archive.json) records the complete
5,424,678-byte archive retained under the cluster project's home diagnostics.
The failed original 4V100 attempt and follow-up read denial are retained alongside
the successful retry, rather than omitted from the evidence.
