# CUDA IPC address-recycling diagnosis (2026-11-10)

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

One-node 16-GPU and two-node 32-GPU checks are queued as job `2344442` at report
creation. They are not included in the completed acceptance claims above.
No public `/opt` library, module, accepted SIF, or scheduled tester setting was
changed. The currently scheduled tester still uses `UCX_TLS=^cuda_ipc` until
the replacement library is separately accepted and deployed.
