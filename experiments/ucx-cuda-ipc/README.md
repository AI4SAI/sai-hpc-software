# CUDA IPC address-recycling diagnosis (2026-11-10)

## UCT-first prerequisite check and review fixes (2026-11-13)

Commit [`2bb888a`](https://github.com/Stardust0831/ucx/commit/2bb888a9779820fa1d26469212896ab2b677a95d) attempts the actual UCT import before using a legacy IPC diagnostic. Successful UCT imports never run the direct legacy probe; only an initial unreachable result triggers it, because UCT also reports unexpected driver import failures as unreachable. Persistent unsupported IPC can skip, while unexpected initial failures and all later import failures remain fatal. Expected CI skips now use ordinary output, and both MPI tests use the shared `test_cuda_errors_and_warnings_counter` name. The production CUDA IPC runtime source is unchanged from `ffe50ff`.

Fresh baseline and candidate release-mt builds completed in job **2418498**, which finished **0:0 in 7m31s** on `16v100n26` (V100-SXM2-16GB, driver 550.163.01, CUDA 12.9.1, Open MPI 5.0.10). All **27 core scenarios**, **nine actual CI launcher cases**, and **14 shell guard/exit-status cases** reached their expected outcomes. The direct UCT regression and independent MPI reproducer each exercised 30 lower-start overlaps per variant: baseline had 30 recovered CUDA error-208 imports per program, candidate had zero, and all normal-run byte comparisons passed. The existing MPI/CUDA matrix recorded **450 passes and 36 Fabric-related skips per variant**. All **34 completed-process core library audits** and **14 CI library audits** matched the intended variant.

A control disabling only direct legacy exports makes the previous `ffe50ff` test exit 77, while the revised test passes all 30 overlaps. This validates the test gate on V100; the successful UCT imports still use real legacy handles, so it does not establish Fabric/POSIX-FD hardware coverage. The later import-error controls and error-208 recovery control verify a successful real stale-mapping close before the injected failure. The latter reaches full-cache purge/retry with an empty cache after proactive invalidation; purging unrelated live mappings remains unvalidated.

Seven source hashes match the tested commit. The [validation record](review-probe-20261113.json) includes commands, source/library hashes, driver results, injection source and scheduler accounting. Raw evidence is `review-probe-evidence.tar.gz` in `/org/org-open-sai/proj-sai-hpc-software/ucx-review-probe-20261113` and the corresponding local `.work` directory (SHA256 `838ea318ef5497708b341ebc8e7009d9de6ff18a6e68672cf50377aa9703e84b`).

The overlap trigger remains allocator dependent, and the external injection controls do not provide deterministic in-tree coverage. Full upstream CI completion, exhaustive Fabric/concurrency coverage, full static-build/GPU acceptance, and application/performance acceptance remain outside this validation.

## Shared test helpers and CI guards (2026-11-13)

Commit [`ffe50ff`](https://github.com/Stardust0831/ucx/commit/ffe50ff8575a55437920238abdf620dda9e05eec) links the overlap regression with `libtest_cuda_common.la`, reuses its ERROR/WARN counter and full-buffer comparison, and checks the overlap executable independently in the MPI CI launcher. The CUDA IPC runtime source is unchanged from `cf3a1d0`. Collective prerequisite checks and the primary-context lifecycle remain specific to the overlap regression.

Fresh baseline and candidate release-mt builds completed in job **2417162**, which finished **0:0 in 8m58s** on `16v100n10`. All **21 core scenarios**, **nine actual CI launcher cases**, and **14 shell guard/exit-status cases** reached their expected outcomes. The direct UCT regression and independent MPI reproducer each exercised 30 lower-start overlaps per variant: baseline had 30 recovered CUDA error-208 imports per program, candidate had zero, and all normal-run byte comparisons passed. A new negative control corrupts the last byte after a successful host copy and is rejected by the shared comparison helper.

The existing MPI/CUDA matrix recorded **450 passes and 36 Fabric-related skips per variant**. All **28 completed-process core library audits** and **14 CI library audits** matched the intended variant. Seven source hashes match the tested commit. The [validation record](review-reuse-20261113.json) includes commands, source/library hashes, driver results, the injection source and scheduler accounting. Raw evidence is `review-reuse-evidence.tar.gz` in `/org/org-open-sai/proj-sai-hpc-software/ucx-review-reuse-20261113` and the corresponding local `.work` directory (SHA256 `b1dbcfc1c3ca1f9cf5cf7fa2e2ad082e88c3b581a305697baecdf90eaacbda1d`).

The overlap trigger still depends on allocator address reuse. The existing LRU gtest fixture uses a standalone synthetic cache and does not drive the production remote-cache import path; deterministic coverage remains open. This validation does not establish full upstream CI completion, exhaustive Fabric/concurrency coverage, full static-build/GPU acceptance, or application/performance acceptance.

## Follow-up review: insertion, diagnostic scope and launcher (2026-11-12)

Commit [`cf3a1d00c46a82c8ca9fae696f75342d71dd2ce8`](https://github.com/Stardust0831/ucx/commit/cf3a1d00c46a82c8ca9fae696f75342d71dd2ce8) removes the remaining page-table insertion retry: the prior range invalidation covers the aligned insertion span while the same write lock remains held. Other insertion failures still propagate. The regression's ERROR/WARN handler is now active only around receiver-side `uct_rkey_unpack()` and `uct_rkey_ptr()`, so host synchronization warnings outside the mapping import do not affect the gate. Import and stale-mapping-close diagnostics still fail the test without relying on message wording.

All **20 core scenarios** reached their expected outcomes in job `2402389`, including the new unrelated-warning pass and injected stale-close-error failure. Both the native MPI reproducer and direct UCT regression exercised 30 lower-start overlaps per variant: baseline had 30 recovered CUDA 208 imports per program, candidate had zero, and all byte checks passed. The existing MPI/CUDA matrix remained at **450 passes and 36 Fabric-related skips per variant**. The close-error test performs a real successful close before injecting an error return; it verifies diagnostic handling and is not an observed driver close failure.

The reviewer's fully simplified launcher was tested, but its wrapper run loaded the host UCX 1.20 `libucp` together with current build libraries; a plain ELF loaded the host UCX throughout and failed with exit 244. Job `2402622` compared alternatives: explicitly exporting `LD_LIBRARY_PATH` through mpirun retained this problem, while applying just `prefix/lib` through `env` inside each rank avoided foreign UCX libraries. The final launcher uses the ordinary executable path and removes `.libs` selection and `UCX_MODULE_DIR`; the shared build therefore uses its standard libtool wrapper.

Final CI command verification in job **2402687** completed **0:0 in 21 seconds**, with all **nine expected-outcome cases** passing across shared-wrapper and plain executable layouts. All **28 core-process library audits** and **14 final CI-process library audits** matched the intended variant. The first job's overall failure came from the intentionally evaluated launcher issue; its failed CI receipts remain preserved, and only the corrected launcher results are accepted. All five source hashes match `cf3a1d0`; the compiled C sources were unchanged between the core tests and final launcher verification.

The [follow-up validation record](review-followup-20261112.json) contains exact commands, source/library hashes, driver results, library maps, scheduler accounting and launcher comparisons. Raw evidence is `followup-evidence.tar.gz` under `/org/org-open-sai/proj-sai-hpc-software/ucx-review-followup-20261112` and the corresponding local `.work` directory (SHA256 `e10aa461eebc6ff5f6a4ef97cad1c1534514a4d6268fd5a74febd2c255e9e6a6`). These tests do not establish full upstream CI completion, Fabric IPC coverage, concurrency safety, or application/performance acceptance of the generalized patch; the allocator-dependent overlap trigger and prior full-static-build limitation remain.

## Release-build review fixes (2026-11-11)

Commit [`09ebff09a61d0e2e2219acc7892efa2fb1017d45`](https://github.com/Stardust0831/ucx/commit/09ebff09a61d0e2e2219acc7892efa2fb1017d45) restores the original `release-mt --with-mpi --enable-assertions` CI build flags. The test now accesses GPU memory through UCT `cuda_ipc` MD/rkey APIs while MPI exchanges host metadata, so MPI transport fallback cannot bypass the tested cache. It counts ERROR/WARN severity without matching internal message strings; trace logging is compiled out in the validated release build. The launcher selects the shared-build `.libs` executable when present and otherwise uses the normal test executable.

Final Slurm job **2355446** completed with **0:0 in 76 seconds** on `16v100n15`. All **18 expected-outcome scenarios**, **nine executions of the actual added CI block**, and **24 completed-process UCX library-map audits** passed their checks. Both the direct UCT regression and the independent MPI reproducer exercised 30 lower-start overlaps per variant: baseline had 30 recovered error-208 imports per program, the candidate had zero, and all byte checks passed. The existing MPI/CUDA matrix recorded **450 passes and 36 Fabric-related skips per variant**. Error injection verified that unexpected imports fail, unsupported prerequisites skip, recovered import errors still fail the regression, and a warning with unrelated wording is counted.

Shared and plain non-libtool executables passed the GPU regression. The static regression target also built and launched without a missing-path error; it explicitly skipped because that build had no registered `cuda_ipc` component. The full static build stopped earlier in `ucx_read_profile` with an unresolved `ucs_load_modules` symbol, so these results do not establish full static-build or static-GPU support. The allocator trigger remains hardware dependent; Fabric IPC, concurrent allocation/free, and application/performance acceptance of the generalized change are outside this validation.

The [release-build validation record](release-review-validation-20261111.json) includes the exact tested source hashes, driver results, library hashes/maps, command outputs, scheduler accounting and all prior failed-attempt explanations. Every source hash matches `09ebff0`. Raw evidence is retained as `release-review-evidence.tar.gz` under `/org/org-open-sai/proj-sai-hpc-software/ucx-review-release-20261110` and the corresponding local `.work` directory (SHA256 `598708c0a4ed2db2dcc6c5f8251c4b203f5298afd235d3a4c15f8492561cf188`). This verifies the added command block and focused suites; it does not claim completion of the full upstream CI pipeline.

## Prior review revision: generalized cleanup and CI

Upstream [PR #12292](https://github.com/openucx/ucx/pull/12292) now has review follow-up commit [`6d92f85ea2432592c7c1e780fdeb5dd95a743306`](https://github.com/Stardust0831/ucx/commit/6d92f85ea2432592c7c1e780fdeb5dd95a743306), based on `74b413ab19d63c277ffa3fb553ed83e42bc50f6f`. Cache hits retain the fast point lookup; misses use the existing range helper to remove stale overlaps for all handle types. The redundant range-invalidation retry is removed, while the full-cache purge/retry fallback is retained. A direct IPC capability probe distinguishes unavailable prerequisites from real import errors, and the regression is now invoked by the upstream MPI CI script.

Both the native reproducer and the revised in-tree regression exercised 30 lower-start overlaps on the same two V100 GPUs for both variants. Baseline tracing found 30 recovered CUDA error-208 imports per program; the candidate had none. All byte checks passed, and the regression correctly returned 1 for baseline and 0 for the candidate. The existing MPI/CUDA matrix produced **450 passes and 36 skips per variant**, with no failed cases; the skips concern unavailable Fabric combinations. All 16 expected-outcome scenarios passed the corrected verifier, including collective GPU skips, error injection, and retained recovery. The regression's 61 successful driver imports include its one direct capability probe; the native reproducer has 60.

The added CI command block was exercised in job **2350615**, which completed with **0:0 in 10 seconds**. Normal execution passed; no GPU produced an explicit skip; an injected recovered import error still propagated failure through the CI gate while byte checks passed. Library paths are applied inside the MPI process, and the new CI invocation bypasses libtool's build-tree path injection. This avoids the host/build/install library mixing exposed by the prior launcher attempt. This is a test of the added command block, not a claim that the full upstream CI pipeline has completed.

The [review validation record](review-validation-20261110.json) includes source hashes, all 16 scenario outcomes, 20 completed-process library-map audits, the three CI gate cases, scheduler receipts, and explanations of the preserved failed attempts. The two ranks intentionally terminated by MPI abort did not write destructor-time library maps. The five PR source files match the final revision; after the 16-scenario run only the CI shell launcher changed, so the compiled C sources are identical. This revision was rebuilt in its own prefix; unlike the earlier copied-prefix trial below, its core libraries are not claimed to be byte-identical to baseline. Raw logs and test harnesses are retained in `review-evidence.tar.gz` under `/org/org-open-sai/proj-sai-hpc-software/ucx-review-20261110` and in the local `.work/ucx-review-20261110` copy, with a recorded SHA256.

The allocator-dependent overlap trigger still skips if address reuse does not exercise the case. Fabric IPC and concurrent allocation/free behavior are not exhaustively validated. Earlier UCX 1.20 application and timing results below apply to the original legacy-only backport, not full application/performance acceptance of this generalized change.

## Initial upstream master follow-up (legacy-only revision)

The patch has also been ported to upstream master
`74b413ab19d63c277ffa3fb553ed83e42bc50f6f` (reports UCX 1.24.0), as fork commit
[`0cd50f47523d07ca1113b9a3938bdecd0ecba9e9`](https://github.com/Stardust0831/ucx/commit/0cd50f47523d07ca1113b9a3938bdecd0ecba9e9).
It adds a two-rank MPI/CUDA regression under `test/mpi` alongside the 10-line
runtime change. The three PR source files match the cluster build byte for byte.

Slurm job `2347716` completed with `0:0` in 15 seconds on `16v100n54`, using the
same two distinct V100s for both variants. The original native reproducer and
the new in-tree regression each exercised 30 lower-start overlapping
reallocations. In **each** program, baseline tracing recorded 60 successful
imports and 30 recovered error-208 imports; the patch recorded 60 successful
imports and no failed imports. Both variants passed all byte comparisons.
The in-tree regression correctly exits 1 on baseline and 0 on the patch. It
requires trace logging and checks that CUDA IPC activity and address recycling
actually occurred; an unexercised path is skipped rather than accepted.

All eight rank traces were audited: every mapped UCX library came from the
selected variant's prefix. Core libraries are byte-identical between variants.
Full provenance, source hashes, and scheduler receipts are in
[`current-master-20261110.json`](current-master-20261110.json); the raw archive
is `master-evidence.tar.gz` under `.work/ucx-master-native-20261110` locally.
The remote experiment directory is
`/org/org-open-sai/proj-sai-hpc-software/ucx-master-native-20261110`.

The current source includes the `gpunetio` submodule, which must be populated.
When using an MPI already linked against another UCX, linking the MPI tests
also needs the in-tree UCX libraries in the linker search path. At runtime,
select **both** `prefix/lib` and `prefix/lib/ucx` in `LD_LIBRARY_PATH`, as well
as `UCX_MODULE_DIR=prefix/lib/ucx`. An initial copied-prefix run loaded both
baseline and candidate CUDA modules through GDA's embedded RUNPATH and aborted;
that mixed-library run is preserved as job `2347639`, not accepted as a patch
test. The corrected run verifies library isolation explicitly. All failed
build/setup receipts are retained and described in the JSON.

The application and performance measurements below concern the UCX 1.20
backport, not an application/performance acceptance of current master.

## UCX 1.20 diagnosis

The observed `cuIpcOpenMemHandle() failed: resource already mapped` comes from
a stale remote mapping whose address range overlaps a new CUDA allocation.
UCX 1.20 checks the new allocation's **starting address** in its cache. When the
new allocation starts below an old cached region but extends into it, that
point lookup misses the old region. CUDA rejects the import with error 208;
UCX then invalidates the overlapping region and successfully retries.

This is a recovered import failure in the measured cases, not evidence of
incorrect simulation results. The earlier policy that rejected all such logs
was conservative; previous large-run acceptance is not automatically restored
by this diagnosis.

## Evidence

We traced the driver calls without changing their arguments or return values,
recorded exporter allocation ranges and buffer IDs, and audited loaded library
paths in each process. The tracer forwards the same CUDA symbol version that
the application calls. Both traced and untraced controls reproduce the error.

An observed transition on the exporting rank was:

| Allocation | Start | Size | Buffer ID |
|---|---|---:|---:|
| Old cached allocation | `0x7faa7bfa1600` | 219,392 B | 1379 |
| Replacement allocation | `0x7faa7bf9d800` | 140,992 B | 2387 |

The replacement starts 15,872 bytes below the old allocation and overlaps it.
The handles and buffer IDs are **different**. Each failure is followed by a
successful close of the old mapping and successful import of the new one.
This is not an identical handle being imported twice. The application stacks
include Kokkos border communication and PPPM reverse grid communication during
Verlet setup after allocation changes.

The native reproducer uses only MPI and CUDA, outside any container, and checks
every received byte. In 30 iterations it frees adjacent 32 KiB allocations and
replaces them with a 64 KiB allocation starting below the old mapped buffer.
This reproduces the same error independently of LAMMPS and the SIF launcher.

| Implementation | Small LAMMPS test: failed imports | Native reproducer: failed imports | Numerical/data checks |
|---|---:|---:|---|
| Installed UCX 1.20.0 | 4 | — | pass |
| Same-source rebuild | 4 | 30 | pass; all failed imports recover |
| Upstream PR #11216 | 4 | — | pass; errors become debug messages |
| Local overlap invalidation patch | **0** | **0** | pass |

[PR #11216](https://github.com/openucx/ucx/pull/11216), tested at
`8d1543454b835e2b4104be01bf678a8efc208945`, reuses identical handles and lowers
the `ALREADY_MAPPED` log level. In this reproducer it does not prevent the
failed imports, because the old and replacement handles differ. Counting only
`UCX ERROR` lines would incorrectly describe that patch as fixing this case.

## Candidate patch

[`patches/0001-invalidate-overlap-before-import.patch`](patches/0001-invalidate-overlap-before-import.patch)
applies to UCX commit `4b7a6ca8410f9cea0e15857233ecfeefdd863dde`, the revision
reported by the cluster's UCX 1.20.0 installation. After a cache miss and before
opening a legacy CUDA IPC handle, it calls the existing range-invalidation
routine. The fast cache-hit path remains unchanged. The existing error
reporting and retry paths are retained. No opaque CUDA handle bytes are decoded.

This is a source rebuild, not runtime interposition as a repair. The
interposition library is observational and used only in diagnostic trials.
The candidate and baseline have identical build options and core libraries;
only `libuct_cuda.so.0.0.0` differs in the copied candidate installation. The
build enables UCX logging to support the trace investigation. Timed trials
use the default log level and do not load the tracer.

The patch is an experimental UCX 1.20 backport candidate. It reuses the same
stale-region lifetime policy as the existing recovery path. It has not been
reviewed as a general solution for concurrent CUDA allocation/free activity,
all CUDA memory types, or other UCX releases.

## Application verification and performance

The immutable LAMMPS image uses upstream commit
`d291d572cf3c73a8a720774b78482a7c931304ca`, with the existing site integration;
it is not a pristine upstream build. SIF SHA256:
`6630f4ae4ec0481adb8025127186f49fa4ab22083bc7a8639c11ae25afb3e2f3`.

Small SPC/E and SNAP checks compare initial/final energy, temperature, stress,
and every atomic force against a separate CPU reference starting from the same
written seed. The gate is `abs(delta) <= 2e-6 + 2e-7 * abs(reference)` for each
value. One-node four-GPU and two-node eight-GPU checks passed, including all 24
GPU-rank driver traces with zero failed imports. Each rank uses a distinct GPU.
These are short deterministic numerical checks, not long-trajectory validation.

The timing comparison runs 972,000 SPC/E atoms on the same four V100 GPUs of
`16v100n81`, one MPI rank per GPU, two bound CPU cores per rank, and one OpenMP
thread. Each invocation performs 100 warmup steps followed by three measured
250-step segments. Invocation order is baseline, candidate, no-IPC, no-IPC,
candidate, baseline; there are six timed segments per setting. No tracing is
enabled during these measurements.

| UCX setting | Median seconds / 250 steps | Mean invocation wall time | Import errors in two invocations |
|---|---:|---:|---:|
| Baseline, IPC enabled | 5.9781 | 31.30 s | 7 + 7, recovered |
| Candidate, IPC enabled | **5.9622** | **31.33 s** | **0 + 0** |
| Candidate, `UCX_TLS=^cuda_ipc` | 8.3493 | 39.27 s | 0 + 0 |

The candidate is within 0.3% of the baseline loop time and gives a 1.40x loop
speedup over disabling IPC in this case. Including startup and warmup, total
invocation time is about 20% lower. This is a bounded four-GPU comparison;
the result does not establish the cost of disabling IPC on every topology.
Large-run finite-state checks accompany the separate force/thermo gates.

## Reproduce the native case

Run inside a two-GPU Slurm allocation, with the corresponding host Open MPI
and CUDA modules loaded, and a work directory under the project filesystem.
Build the reproducer and optional tracer:

```bash
mpicc -O2 -g -I/opt/devtools/nvidia/cuda-12.9.1/include \
  reproducer/mpi_repro.c -L/opt/devtools/nvidia/cuda-12.9.1/lib64 -lcudart \
  -o mpi_repro
gcc -shared -fPIC -O2 -g -I/opt/devtools/nvidia/cuda-12.9.1/include \
  reproducer/ipc_trace.c -ldl -pthread -o ipc_trace.so
mkdir -p trace
```

Select an isolated baseline or patched UCX prefix. `UCX_TLS` deliberately
includes `cuda_ipc` and `OMPI_MCA_pml=ucx` forces the intended MPI backend.
Without the latter, a successful native run can use another MPI backend and
exercise no CUDA IPC calls; our initial unforced attempt is retained as such.

```bash
ucx_trial_prefix=/absolute/path/to/isolated/ucx
mpirun -np 2 --map-by ppr:2:node:pe=2 --bind-to core \
  env OMPI_MCA_pml=ucx UCX_TLS=sm,self,cuda_copy,cuda_ipc \
      UCX_RNDV_THRESH=1 UCX_RNDV_SCHEME=get_zcopy \
      UCX_MODULE_DIR="$ucx_trial_prefix/lib/ucx" \
      LD_LIBRARY_PATH="$ucx_trial_prefix/lib:$LD_LIBRARY_PATH" \
      LD_PRELOAD="$PWD/ipc_trace.so" SAI_UCX_TRACE_PATH="$PWD/trace" \
  bash -c '
    IFS=, read -ra devices <<< "$CUDA_VISIBLE_DEVICES"
    export CUDA_VISIBLE_DEVICES="${devices[$OMPI_COMM_WORLD_LOCAL_RANK]}"
    exec ./mpi_repro
  ' > screen.txt 2>&1
python3 reproducer/analyze_trace.py trace > trace-analysis.json
```

Check that `lower_overlap_count=30`, data checks pass, and the expected UCX
library paths appear in the traces. No failed imports is the candidate gate;
the baseline is expected to recover from 30 failed imports. Preserve separate
output directories for each variant. The receiver's importer trace, rather
than the process exit status alone, demonstrates whether the defect was hit.

## Artifacts and scope

The complete investigation, exact batch scripts, frozen benchmark controls,
library builds, traces, and scheduler logs remain in:

`/org/org-open-sai/proj-sai-hpc-software/ucx-ipc-20261110-a`

Isolated candidate prefix: `install/preinvalidate` under that directory.
Raw evidence archives and their SHA256 values are listed in
[`results-20261110.json`](results-20261110.json). Local copies are under
`.work/ucx-ipc-20261110` in the operator workspace. Archives are investigation
evidence and are not installation payloads.

Completed Slurm jobs: `2344056` (same-source/PR controls), `2344228` (candidate
and timings), `2344419` (native reproduction), and `2344492` (4/8 GPU numerical
checks). The initial build job `2343954` finished compilation but exited 127
when `ucx_info` picked up the host `libucs`; the follow-up explicitly selects
the rebuilt library path. That failed receipt and the unforced native attempt
`2344321` are preserved.

Job `2344442` subsequently completed the one-node 16-GPU and two-node 32-GPU
SNAP numerical/trace checks: all 48 ranks used distinct GPUs within each run,
with 128 and 320 successful imports respectively and no failed imports.
The one-node 16-GPU SPC/E process also completed and its saved force/thermo
outputs passed the same numerical gate on post-run audit. Its traces contain
832 successful imports, no error 208, and 59 error 711
(`CUDA_ERROR_TOO_MANY_PEERS`) results during reachability checks. CUDA defines
711 as exhausted peer-access resources. The strict assertion that rejected
**any** failed import terminated the batch script (Slurm `FAILED`, `1:0`, 52 s),
so the two-node 32-GPU SPC/E case was not reached. This is a separate limitation;
the whole 16/32-GPU suite is not accepted. Raw evidence is retained in
`validate32-evidence.tar.gz`.
No public `/opt` library, module, accepted SIF, or scheduled tester setting was
changed. The currently scheduled tester still uses `UCX_TLS=^cuda_ipc` until
the replacement library is separately accepted and deployed.
