# ABACUS beta12 CPU latency diagnosis

The slow DSPRHBM installation check contains seconds of filesystem access
latency, and other MPI ranks spend the corresponding time waiting in
collectives. The installed executable, rank placement and numerical results
remain correct. Moving MPI buffers/session files into RAM removes the
shared-memory warnings but does **not** resolve the complete delay.

A specific ABACUS beta12 source issue also amplifies shared-file traffic:
DeePKS writes `deepks_projdm.dat` from every rank, even with
`deepks_out_labels=0`. A traced remote rank spent 4.21 s opening this file while
rank 0 spent 4.21 s in the following `MPI_Allreduce`.

This is a diagnosis of the small native acceptance cases, not a CPU/GPU
benchmark, nor a claimed performance fix. No installed binary, public module,
production workflow setting or original acceptance evidence was changed.

## Original observation

The accepted installation job `2217411` ran on `d9470n03,d9470n04`: four ranks
per node, two OpenMP threads per rank. Its two-node HSE took 136.47 s externally
and 93.19 s in the ABACUS timer; DeePKS took 224.77 s externally and 157.93 s in
the ABACUS timer. In the same allocation, single-node DeePKS took 3.06 s
externally and 1.70 s internally. See the
[original native acceptance](../abacus-beta12-installed-20261105/README.md).

The 119.35 s `cal_e_delta_band` timer in the original DeePKS run is not evidence
of expensive dense matrix multiplication. At the pinned beta12 source
`6b24db05dc175d6e1d7894ac380f48118b43fbaa`, it contains a small local loop
(`nlocal=33` in this fixture) followed by a scalar
`MPI_Allreduce(MPI_IN_PLACE, ..., MPI_COMM_WORLD)`.

## Controlled observations

The same two nodes and immutable installed binary were used throughout. All
inputs and outputs stayed under project storage, with account
`proj-sai-hpc-software`. Only explicit communication buffers and, in the second
control, private MPI session metadata used `/dev/shm` (node RAM). Neither
physical `/tmp` nor a container was used.

`original` retains the acceptance script's project-backed `TMPDIR`.
`mmap-shm` adds `OMPI_MCA_shmem_mmap_backing_file_base_dir=/dev/shm` and
`OMPI_MCA_shmem_mmap_relocate_backing_file=1`.
`session-shm` also points `PRTE_MCA_prte_tmpdir_base` at a private directory
under `/dev/shm` on both nodes; application `TMPDIR` and the working directory
remain on the project filesystem.

The following runs have no syscall/PMPI instrumentation. External wall time
includes the rank wrapper's existing hash/loader checks, MPI startup and
finalization; it is not exclusively ABACUS compute time.

| Job | Order | Case | Configuration | External wall, s | ABACUS total, s |
| --- | ---: | --- | --- | ---: | ---: |
| 2218028 | 1 | DeePKS | original | 122.07 | 67.40 |
| 2218028 | 2 | DeePKS | mmap-shm | 102.53 | 14.96 |
| 2218028 | 3 | DeePKS | mmap-shm | 6.49 | 2.90 |
| 2218028 | 4 | DeePKS | original | 65.59 | 35.38 |
| 2218028 | 5 | HSE | mmap-shm | 118.26 | 107.81 |
| 2218192 | 1 | DeePKS | session-shm | 24.04 | 19.39 |
| 2218192 | 2 | DeePKS | original | 42.37 | 29.80 |
| 2218192 | 3 | DeePKS | session-shm | 27.32 | 22.86 |
| 2218192 | 4 | HSE | session-shm | 125.49 | 80.60 |

The remaining two runs in `2218192` add PMPI timing to all ranks and `strace`
to ranks 0 and 4. Their elapsed times (179.71 s and 68.26 s) are preserved in
[results.json](results.json), but are diagnostic samples, not performance
measurements. Tracing perturbs execution and its output also resides on the
project filesystem.

All eleven scientific samples returned zero and matched the pinned energy
reference: maximum absolute error `1.72e-12 eV`, tolerance `1e-5 eV`. Input
hashes matched the accepted fixtures in 106 checks. This work did not repeat
the complete installation matrix or certify a new restart/output-file
contract.

### Rank placement and MPI transport controls

In `2218028`, Slurm allocated CPUs `8-15` on `d9470n03` and `0-7` on
`d9470n04`; actual rank affinities covered the corresponding pairs. In
`2218192`, both nodes received `0-7` and ranks again stayed within those CPUs.
All 88 recorded rank launches had the expected binary SHA256, two allowed
CPUs and `OMP_NUM_THREADS=2`. Starting the numbering at CPU 0 was not evidence
of an allocation violation.

Two simple eight-rank MPI probes in `2218028` completed 1,000 out-of-place
8-byte allreduces in approximately 3.75 ms, and twenty 1 MiB allreduces in
approximately 28 ms, with either backing configuration. These tight-loop
probes do not cover every ABACUS communicator/collective. They do establish
that the tested communication operations themselves can run quickly on these
nodes, despite a much longer external invocation time.

### Direct file-operation and MPI-wait evidence

Representative syscall durations from the original profiled configuration:

| Rank | Operation | Duration |
| ---: | --- | ---: |
| 0 | `mkdir("OUT.autotest/")` | 4.27 s |
| 0 | open `OUT.autotest/running_scf.log` | 8.59 s |
| 0 | open `STRU`, carbon/hydrogen orbital files | 2.13–2.15 s each |
| 4 | open the frozen `native-rank.sh` on project storage | 12.84 s |
| 4 | `mkdir` an already-existing project parent during `mkdir -p` | 7.46 s |
| 4 | open `/opt/devtools/.../lib/ucx/libuct_cma.so.0` | 4.24 s |

The original profiled run recorded rank 0 in `MPI_Init_thread` for 82.95 s,
while rank 4 reached that call later and spent 15.48 s in it. Rank 4 spent
27.93 s in broadcasts, including one call of 19.33 s. MPI timers can therefore
include waiting for another rank's startup and file operations.

The `session-shm` trace provides two matched examples after communication
files were already moved into RAM:

- Rank 0 spent 4.261 s creating `OUT.autotest/`; rank 4 spent 4.262 s in an
  overlapping allreduce. The estimated overlap covers 99.98% of the MPI call.
- Rank 4 spent 4.211 s opening `deepks_projdm.dat`; rank 0 spent 4.214 s in an
  overlapping allreduce. The estimated overlap covers 99.84% of the MPI call.

[wait-correlations.json](wait-correlations.json) records the calls and clock
alignment method. PMPI monotonic origins are aligned with the final profile
file's open-completion time in strace; these epoch estimates are not hardware
synchronized measurements. The raw traces and profiles are preserved, so the
alignment and source of the waiting time can be reviewed independently.

### DeePKS writes the same file from every rank

The pinned source call is in
[lcao_deepks_iface.cpp:123](https://github.com/deepmodeling/abacus-develop/blob/6b24db05dc175d6e1d7894ac380f48118b43fbaa/source/source_lcao/module_deepks/lcao_deepks_iface.cpp#L123):
`deepks_scf=1` enters the block and invokes `check_pdm` without a rank guard.
[deepks_pdm.cpp:461](https://github.com/deepmodeling/abacus-develop/blob/6b24db05dc175d6e1d7894ac380f48118b43fbaa/source/source_lcao/module_deepks/deepks_pdm.cpp#L461)
opens `deepks_projdm.dat` with an ordinary truncating `ofstream` and writes the
projected density matrix. The function contains no MPI collective and no rank
filter. In the trace, ranks 0 and 4 open the same path about 0.9 ms apart;
rank 4's open takes 4.21 s. Setting `deepks_out_labels=0` does not suppress this
path when `deepks_scf=1`.

This is a concrete unnecessary source of concurrent shared-file access. The
next narrowly scoped upstream candidate is to execute that output function
on global rank 0 only, while retaining `cal_pdm` and its collective work on
all ranks. That source change has **not** been built, benchmarked or applied
to this release. It cannot by itself explain the unrelated slow input/library
opens or all of HSE's delay.

## Recommended follow-up and limits

1. Keep the accepted native release available. These results do not justify
   rebuilding it, changing compiler options, changing the MPI implementation,
   or weakening numerical/feature checks.
2. Keep calculation inputs, outputs and persistent scratch on project storage.
   Node-local RAM is suitable for MPI communication buffers/session metadata;
   it is not a reason to move application work to physical `/tmp`. The two
   RAM controls here validate that distinction, but do not establish a full
   speedup or change any production defaults.
3. Review the DeePKS rank-0 output candidate separately, then validate energies
   and projected-density/restart outputs before proposing an upstream fix.
   Do not silently remove output from the established acceptance fixture.
4. Investigate the multi-second metadata/open latency with site filesystem
   diagnostics using the timestamps and paths here. It appears on project
   input/output paths, a frozen control script and a shared `/opt` library.
   Storage-server state, network retries, client behavior and contention were
   not measured; the underlying cause of those delays remains open.

The reported `Space Available: 0 B` is an MPI backing-file warning. It does
not establish the physical storage quota, and was not used to stop work. The
RAM controls remove that warning while retaining slow cases, so suppressing
or eliminating it alone would not close the performance issue.

## Accounting and evidence

| Job | Result | Allocated CPUs | Elapsed | Purpose |
| --- | --- | ---: | ---: | --- |
| 2218028 | COMPLETED 0:0 | 16 | 468 s | mmap ABBA controls, probes, HSE |
| 2218148 | FAILED 1:0 | 16 | 2 s | diagnostic profiler compile error |
| 2218192 | COMPLETED 0:0 | 16 | 483 s | session controls and diagnostic tracing |

The failed profiler attempt was a local C macro variable-name conflict; no
ABACUS calculation ran in that attempt. Its source and compiler log remain
in the archive. Across all three jobs, allocated time is **4.236 CPU-core
hours**; Slurm `TotalCPU` is **0.877 CPU hours**. These are resource quantities,
not monetary charges or GPU hours. No further jobs were submitted after the
cause investigation had sufficient evidence.

The complete remote run paths are in [runs.json](runs.json); the collection
operation is
`/org/org-open-sai/proj-sai-hpc-software/operations/abacus-cpu-diagnosis-20261105`.

- [raw.tar.gz](raw.tar.gz): 789 archived controls, logs, rank traces and results;
  2,718,441 bytes, SHA256
  `1d1e650ca32265276535c680b9c68889b01ce837559359beb5c4992d2a2c986d`.
- [manifest.json](manifest.json): digest and size of every archived file.
- [accounting.txt](accounting.txt): complete job/step accounting, including the
  failed diagnostic attempt.
- [results.json](results.json), [input-digests.json](input-digests.json): all
  samples and fixture hashes.
- [slow-syscalls.json](slow-syscalls.json), [mpi-profiles.json](mpi-profiles.json):
  extracted diagnostic observations, with complete originals in the archive.
- [source-references.json](source-references.json): pinned source URLs and
  hashes used to interpret the timers and output behavior.
- [verification.json](verification.json): archive, input, binary, affinity and
  numerical checks. Recheck from the repository with:

  ```bash
  python3 docs/evidence/abacus-cpu-io-diagnosis-20261105/verify.py
  ```
