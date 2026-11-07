# CP2K November 7 development build recovery

CP2K source: `0761fd1b052d62503997faf4d66c0f8b9cec03b7`.
Repair controller: `65ed8ef8870fe8688b635fdcf07c25cf71f7621c`.
PR: https://github.com/AI4SAI/sai-hpc-software/pull/73

## Diagnosis

The [daily workflow 42313526953](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42313526953)
used controller `e70641b64329a96693972dc0cff22af4aeb6523a`. All four CP2K
development builds stopped before compilation with
`unrecognized CP2K cuSOLVER source; review MPI progress repair`.
The source guard recognized only the v2026.2 layout and its repaired bytes.

Upstream [CP2K #6465](https://github.com/cp2k/cp2k/pull/6465), merged as
`ca8d286b8450cd59e3d8e31029a498985a473c46`, moved three separate NCCL
initialization sites into one shared BLACS context. The new initialization
still needs the MPI progress barrier before blocking NCCL bootstrap. Its
communicator is `ctx->comm`; already initialized contexts return without
repeating the barrier. Both original and repaired file hashes are reviewed.
The v2026.2 repair remains byte-identical; unknown sources still stop for review.

The [first recovery 42328707931](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42328707931)
passed that guard on every partition, then stopped with
`unsupported or missing CP2K LibXS version/checksum: cc9188a`.
Upstream [CP2K #6476](https://github.com/cp2k/cp2k/pull/6476), merged as
`f9c1cf8a92affeff309ea39f7b32a1c183ef4629`, updated LibXS and its HFX packing
call. The workflow now stages `libxs-cc9188a.tar.gz` from the exact upstream URL
and checks SHA-256
`df7ce1ea8087663cff31e0ac9d018b8406d4489bd86f2923bf27147de62f33e5`.
The actual 9,104,999-byte download matches that declaration. Release LibXS
selection remains unchanged. Existing dependency caches are correctly missed
when the LibXS source contract changes; physical Spack providers are reused.

## Validation

- Reproduced the original source-guard failure using the actual upstream file.
- Six source-repair tests passed, including legacy/current layouts, exact hashes,
  idempotence, and rejection of an altered communicator lifecycle.
- Twelve dependency tests passed, including new/release archive selection,
  changed checksum rejection, and damaged download/archive handling.
- Hosted validation at the repair controller passed 1,270 tests with two
  optional skips and all shell syntax checks.
- [Four-partition recovery 42331323249](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42331323249)
  uses the same CP2K source as the failed daily run. All four builds passed
  executable/version/loader/feature checks and produced SIFs. All twelve
  managed-overlay receipts record accepted natural teardown.

The [completed qualification](raw/completed-qualification.json), observed at
04:24 UTC, records the source/recipe identities, SIF hashes, build and science
receipts, numerical comparisons, dependency timings, and scheduler accounting.
The earlier [03:49 UTC snapshot](raw/qualification.json) is retained separately.

| Partition | Build job | Build elapsed | Science job | Science elapsed | Science result |
| --- | --- | --- | --- | --- | --- |
| DSPRHBM | 2274454 | 26:17 | 2274775 | 38:48 | Verified, 14 calculations |
| 16V100 | 2274466 | 28:30 | 2274820 | 7:26 | Verified, 14 calculations |
| 4V100 | 2274504 | 33:42 | 2274952 | 6:02 | Verified, 14 calculations |
| 8V100V0 | 2274465 | 38:34 | 2274966 | 10:53 | Verified, 14 calculations |

The 16V100 two-node water64 case made 112 `cp_fm_diag_cusolver` calls.
Its single/two-node energy and maximum force differences were `7.96e-12 Ha`
and `1.10e-11 Ha/Bohr`, below the unchanged `1e-6` and `1e-5` limits.
All four targets passed all seven single/two-node case comparisons and the
OT/diagonalization/ELPA comparisons, for 56 completed calculations. The largest
single/two-node energy and force differences were `3.07e-11 Ha` and
`1.50e-11 Ha/Bohr`; the largest solver-comparison force difference was
`1.543e-9 Ha/Bohr`. These are correctness checks, not performance measurements.

## Completed beta publication

The [workflow result](raw/workflow-completed.json) is `completed/success` at
04:55:43 UTC on attempt 2. This reran the failed publication stage and reused
all four accepted SIFs and their scientific evidence; no rebuild was submitted.
The [publication snapshot](raw/publication.json), observed at 04:56 UTC,
records passing numerical checks through the host-MPI SIF launch path:

| Partition | Launch job | Node | Elapsed | Result |
| --- | --- | --- | --- | --- |
| DSPRHBM | 2275859 | d9470n01 | 0:55 | Verified |
| 16V100 | 2275853 | 16v100n42 | 0:30 | Verified |
| 4V100 | 2275855 | 4v100n31 | 0:16 | Verified |
| 8V100V0 | 2275857 | 8v100v0n01 | 0:56 | Verified |

`cp2k/master-beta-later` now selects
`master-beta-20261107-0761fd1b052d-r4d85319d4295-b5186e4e26126`, generation
`daily-20261107-42331323249-2-cp2k`. The active module hash matches its generation
manifest, the public catalog hash matches the publication receipt, and all
four image hashes match the original scientifically accepted SIFs. Login-node
`module --ignore_cache help cp2k/master-beta-later` reports the exact source
commit and accepted snapshot `20261107`. Its source-version date is November 6
because that is the source commit date, while acceptance and publication took
place on November 7.

The [first publication attempt](raw/cpu-bootstrap-stall.json) is retained as a
failure. DSPRHBM job `2275533` on `d9470n02` stopped making progress inside
Apptainer's memory-bootstrap startup before MPI or CP2K executed. After 23:09
wall time and 5.616 seconds of CPU time, a diagnostic `SIGQUIT` to the confirmed
bootstrap child captured a Go stack waiting in Apptainer's RPC server and ended
the job with `2:0`. The controller retained the previous beta. The unchanged
runtime and SIF passed on `d9470n01` during the retry. This is an operational
recovery; the evidence does not establish the root cause or a permanent fix for
that startup stall.

All new build and runtime files stay below
`/org/org-open-sai/proj-sai-hpc-software`. Container installation prefixes do not
write the physical `/opt/apps` installation. The hosted SSH workflow remains in
use; the unmerged VPS runner experiment is unrelated to these failures.
