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

The [qualification snapshot](raw/qualification.json), observed at 03:49 UTC,
records the source/recipe identities, SIF hashes, build and science receipts,
numerical comparisons, dependency timings, and scheduler accounting:

| Partition | Build job | Build elapsed | Science job | Science at snapshot |
| --- | --- | --- | --- | --- |
| DSPRHBM | 2274454 | 26:17 | 2274775 | Running |
| 16V100 | 2274466 | 28:30 | 2274820 | Verified, 14 calculations |
| 4V100 | 2274504 | 33:42 | 2274952 | Verified, 14 calculations |
| 8V100V0 | 2274465 | 38:34 | 2274966 | Running |

The 16V100 two-node water64 case made 112 `cp_fm_diag_cusolver` calls.
Its single/two-node energy and maximum force differences were `7.96e-12 Ha`
and `1.10e-11 Ha/Bohr`, below the unchanged `1e-6` and `1e-5` limits.
All seven case comparisons and the OT/diagonalization/ELPA comparisons passed
on both accepted GPU targets. These are correctness checks, not performance
measurements. Full daily completion and beta rotation still require the
remaining targets' science and beta gates.

All new build and runtime files stay below
`/org/org-open-sai/proj-sai-hpc-software`. Container installation prefixes do not
write the physical `/opt/apps` installation. The hosted SSH workflow remains in
use; the unmerged VPS runner experiment is unrelated to these failures.
