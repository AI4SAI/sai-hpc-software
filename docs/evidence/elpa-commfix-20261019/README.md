# Shared ELPA communicator repair acceptance

Three ELPA 2026.02.001 variants were rebuilt inside Apptainer at their existing
final `/opt/devtools/elpa/` prefixes, using home-backed build and temporary storage.
The only additional library source change is upstream PR #85, merged 2026-10-15,
commit `20be982db53a671b9aac866a726aebe153a766e6`. GCC 13.3, MPI/BLAS, CUDA features,
ISA choices and the prior CUDA-aware collective compatibility patch are retained.

| Build | Job | Elapsed | State |
| --- | --- | --- | --- |
| AVX2 / NVIDIA | 1862858 | 4m 17s | COMPLETED 0:0 |
| AVX512 / NVIDIA | 1862859 | 2m 19s | COMPLETED 0:0 |
| AVX512 / CPU | 1862898 | 2m 03s | COMPLETED 0:0 |

| Independent partition check | Job | Node | State |
| --- | --- | --- | --- |
| 16V100 | 1862899 | 16v100n02 | COMPLETED 0:0 |
| 4V100 | 1862929 | 4v100n11 | COMPLETED 0:0 |
| 8V100V0 | 1862930 | 8v100v0n11 | COMPLETED 0:0 |
| DSPRHBM | 1862931 | d9470n02 | COMPLETED 0:0 |

Every final SIF and partition check covers real/complex, ELPA1/2, CPU/GPU as
applicable, one/two MPI ranks, repeated solves and nonblocking settings. A fresh
Fortran consumer, module selection/unload, CUDA dependency exclusion on CPU,
kernel inventories and actual per-rank loader traces are also checked.

The new lifecycle gate allocates/destroys 32 objects, solving twice per object,
on one and two ranks. Each GPU rank reports **32 created, 32 explicitly freed,
zero live** temporary communicators. CPU paths report 0/0/0. Missing tracing or
numerical results fail the gate. The 358 downloaded raw files match the source
hashes in `evidence-sha256.json`. Across the seven accepted jobs there are
1,832 successful solves, including eight extra module-loader solves. Maximum
spectrum/residual/orthogonality errors are `2.22e-15`, `6.46e-16`, `1.665e-15`.
These are finite, small single-node correctness/lifecycle checks, not a large
CP2K production run or performance benchmark.

The initial CPU job `1862860` built successfully and computed correct results,
but its verifier counted 63 of 64 lines because a site MPI advisory prefixed
one line with a NUL. The parser now trims that prefix without changing raw
logs; the replacement CPU job above completed the entire acceptance gate.

The [release manifest](../../../deployment/elpa-2026.02.001-commfix/release.json)
pins SIFs, new and previous payload manifests, the module and both patches.
`raw/` contains the independent original logs, frozen partition controls,
accounting, source-input hashes and loader records. The administrator payload
is at `/home/stardust/sai-hpc-software/deployment/elpa-2026.02.001-commfix`;
its three inventories total about 52.83 MiB. Preflight verifies the old physical
installs and new extracted payloads. **Physical replacement has not happened.**
The existing auto module and canonical prefixes are retained. The replacement
installer preserves old trees and is idempotent; no compiled system dependencies,
build objects or caches are shipped.

Deployment/lifecycle tests: 29 targeted tests passed on SAI with `umask 022` and
temporary storage under the project home. The first test invocation inherited
the site's `002` umask and correctly rejected writable fixture directories;
the installers' permission checks were retained. Full Linux CI at `a9f27e9`
passed 825 tests with two optional skips (827 total):
[run 39272571581](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39272571581).

## Scheduling incident and dependency recovery

While 8V100V0's independent validation was queued, an attempted diagnostic
Slurm step in our own CP2K dependency allocation requested four CPUs for one
GPU. Site policy requires six CPUs per GPU; its hook cancelled parent job
`1862503` at 22:44:15 CST, after 47m 27s. This was an agent resource-request
error, not an ELPA numerical or compiler failure. The final 8V100V0 acceptance
above ran successfully as its original independent batch job `1862930`.

The cancelled dependency run and overlay are retained. `recovery/` records
the recovery scripts: clone and SHA256-check the checkpoint, retain its exact
CP2K recipe identity/install prefix/Spack lock and installed packages, validate
the node/compiler/external graph, and continue the original install with
`--dont-restage`. The recovery allocation requests the original two GPUs and
12 CPUs under the normal batch policy. Its request and accounting are recorded
alongside these scripts; no previous evidence is relabelled as a new build.
The cloned recovery job `1863579` subsequently failed the filesystem check:
`e2fsck -pf` found deleted/unused inodes and an unconnected directory after the
interrupted write. It never reached dependency compilation and is **not accepted**.
Both original and clone are retained. Fresh dependency job `1863884` was then
submitted using the original frozen controller, identity and source-only seed,
without borrowing the inconsistent overlay. `recovery/recovery-status.json`
records that request and observed accounting; `recovery/failed-clone-fsck.log`
preserves the failed check. A detached monitor will validate and seal the fresh
checkpoint if it succeeds. Neither cancelled nor failed allocations are reported
as successful, and the original GitHub phase has not been relabelled.

CP2K still pins the old shared ELPA bytes. After administrator replacement,
native ELPA revalidation and a new CP2K dependency lock/identity are required.
