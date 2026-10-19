# Simplified ELPA module and repaired physical libraries

The candidate auto module is 36 lines, down from 64. One partition table selects
the same three canonical prefixes. It loads the same MPI/BLAS modules and exposes
the same compilation/runtime search paths. Unload uses `ELPA_ROOT` directly and
clears the three identities left by earlier modules. Repeated `lscpu` probing
is removed from the ELPA module; dependency consistency is checked on each
actual partition. The public module has not yet been replaced.

CP2K's 36-line public selector delegates its environment setup to the installed
`share/sai/native-module.tcl`; ELPA's simplified file contains its complete setup.
No library, algorithm, CUDA option or installation path changed for this module update.

| Partition | Job | Node | Elapsed | Result |
| --- | --- | --- | --- | --- |
| 16V100 | 1864706 | 16v100n19 | 14 s | COMPLETED 0:0 |
| 4V100 | 1864707 | 4v100n09 | 5 s | COMPLETED 0:0 |
| 8V100V0 | 1864708 | 8v100v0n11 | 15 s | COMPLETED 0:0 |
| DSPRHBM | 1864709 | d9470n02 | 5 s | COMPLETED 0:0 |

All jobs run directly against the administrator-installed, communicator-repaired
physical `/opt` libraries, with a private candidate module under the project home.
Each job verifies all three installation inventories before running. The four
checks cover C/Fortran consumer compilation, two-rank complex ELPA2 with the
nonblocking option, actual per-rank ELPA/MPI/BLAS/HCOLL loader paths, CPU execution
without CUDA dependencies, and 32 successive ELPA objects per rank. GPU ranks
report 32 communicators created, 32 freed, zero remaining; CPU paths report 0/0/0.

There are **456 successful solves**. Maximum spectrum/residual/orthogonality
errors are `1.332e-15`, `5.198e-16`, and `1.665e-15`. These are small single-node
correctness/lifecycle tests, not CP2K application or performance qualification.
Ordinary unload succeeds after changing the partition variable. Unload after
replacing both earlier module variants succeeds with the partition unset,
removing the original paths and internal variables. Missing/unsupported
partitions still reject loading.

Results:
`/home/stardust/sai-hpc-software/runtime-tests/elpa-simple-20261020-003329-3250659`.
All **113 raw files** in `raw/evidence-sha256.json` were hashed on SAI and verified
after download. Compiled test programs and runtime caches are excluded.
`validation.json` is independently derived from accounting, frozen controls,
loader traces, numerical logs and resource traces. Reproduce from the repo root:

```bash
python3 deployment/elpa-2026.02.001-commfix/verify-module-results.py \
  docs/evidence/elpa-simple-module-20261020/raw
```

`previous-release.json` preserves the earlier library handoff's manifest.
The current release keeps its original library/SIF identities and records
separate module hashes for the October 19 container checks and October 20 native
checks. The updated installer skips already-repaired libraries, backs up the
known old public module and replaces it atomically. Its replacement/retry tests
and the existing ELPA suite passed: **32 tests**.
