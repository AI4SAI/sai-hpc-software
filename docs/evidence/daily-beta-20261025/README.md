# Daily beta closure — 2026-10-25

The scheduled workflow did run on October 23–25. Runs `39918941225`,
`40073814974`, and `40173502941` ended cancelled/failed, and the workflow did not
previously contain a beta-module promotion stage. Source dates shown by modules
are commit dates; they are distinct from the date a build was accepted.
The schedule remains `23 2 * * *` UTC (10:23 Asia/Shanghai).

The corrected pipeline revalidates each software's complete development matrix
with the original frozen controllers, runs numerical checks through its actual
Lmod/SIF launchers, and then selects an immutable home-maintained generation.
The static `/opt` gateways remain the entry points. All computation and evidence
stay under `/org/org-open-sai/proj-sai-hpc-software`; no application is extracted
from SIF for normal use.

## Completed publications

### ABACUS

Workflow [40189122773](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40189122773)
successfully built and accepted source `89595dd29a26aec34d623518cadf736c17345f69`
(commit date October 23) on all four targets. Its original frozen controller
checks multinode execution, GPU features, and paired PW/HSE/DeePKS science and
performance before project publication. The [workflow jobs](abacus-workflow-jobs.json)
retain the original build/acceptance results.

The first public-route verification exposed an internal acceptance-script
environment error: `--export=NIL` removed HOME, and the AVX2 host MPI failed at
initialization on 16V100/8V100V0. 4V100/DSPRHBM passed. The
[failed route receipt](abacus-first-route-failure.json) retains the errors and
proves that the previous public alias was preserved. The runner now restores
the actual account HOME/USER/LOGNAME before module/MPI startup; storage and
runtime scratch policies are unchanged.

Publisher `ca1bcd1` rechecked the same immutable SIFs and all four routes passed:
`1934997` (16V100), `1934999` (4V100), `1935001` (8V100V0), `1935003` (DSPRHBM).
`abacus/develop-beta-later` now selects
`develop-beta-20261025-89595dd29a26-r75ef5179f1e8-b34568c7aa827`.
See the [publication record](abacus-publication.json) and [live help](abacus-public-help.txt).
The original workflow began before the beta job was wired in, so its accepted
artifacts were passed to the same publisher separately using the original
resolved matrix, without resolving a newer upstream commit.

### DeePMD-kit

DeePMD's original daily source was
`2ba617bc3db67adee2b8ba00c71e9fa1c2d2bb8c` (commit date October 23). The three
October 25 SIFs passed build verification. Two passed their original science
allocations; `8v100v0n09` reported an uncorrectable CUDA ECC fault. Independent
job `1933856` accepted the identical 8V100V0 SIF on `8v100v0n06`, preserving the
original failed allocation and recording a separate recovery pointer.

The automatic publication route then passed jobs `1934205` (16V100), `1934207`
(4V100), and `1934212` (8V100V0). The first 8V100V0 route job, `1934209`, again
encountered ECC; the publisher automatically excluded that node and submitted
its single allowed retry. Each accepted route checks GPU inference plus actual
two-GPU se_e2_a and DPA2 training/restart, learning curves, finite gradients,
changed and synchronized parameters, saved checkpoints, the SIF identity, and
private runtime memory.

`deepmd-kit/master-beta-later` now selects
`master-beta-20261025-2ba617bc3db6-r75ef5179f1e8-b57696ab2c62a`.
The [publication record](deepmd-publication.json) includes all jobs, checks, and
the previous generation. [Live module help](deepmd-public-help.txt) reports the
source commit date and accepted snapshot date separately. A first preparation
attempt failed before job submission because frozen fixture permissions were
copied; its retained status proves the prior alias was preserved. Fixture
copies now remain writable inside the private verification directory.

A separate invocation using publisher `70851b8` revalidated the same complete
matrix and returned `unchanged`, without submitting any runtime allocations or
altering the selected generation. Its [repeat-publication receipt](deepmd-repeat-publication.json)
records the original request and preserved fixed module.

### GPUMD

Workflow [40190615647](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40190615647)
completed successfully on controller commit `440de2c`, including the reusable
beta publication job. Source `30a6db02e36494b8bba71c9fa1d1a68627604961` has
commit date October 24. Build jobs `1934314`–`1934316` and complete science jobs
`1934369`, `1934376`, `1934385` passed on all three V100 partitions. The science
checks cover the pinned prediction gold values, dynamics, training, JIT, GNEP,
PLUMED, installed DeePMD integration and the existing throughput gate.

The automatic publisher then passed its actual SIF/module route in jobs
`1934704`, `1934706`, `1934708` and selected
`gpumd/master-beta-20261025-30a6db02e364-r75ef5179f1e8-b5ed2841f9ae7` through
`master-beta-later`. The [publication record](gpumd-publication.json),
[workflow jobs](gpumd-workflow-jobs.json) and [live help](gpumd-public-help.txt)
record the evidence and public result. This validates the Actions path from
source resolution through build, science and public beta rotation.

### LAMMPS

Source `049f269111f42ac408f706e0cc94b37f50346baf` (commit date October 24)
passed all three candidate builds and all three scientific acceptance jobs in
workflow [40190614608](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40190614608).
Science jobs `1935412` (16V100), `1935406` (4V100), and `1935408` (8V100V0)
passed all 14 cases, including GPU execution, single/two-node calculations,
and TF/PT/JAX models through the installed native DeePMD 3.2.0 dependency.
Each candidate provides 71 packages and 2,500 styles. See the
[science records](lammps-science.json) and [workflow jobs](lammps-workflow-jobs.json).

The workflow started on controller `440de2c`, before the HOME repair. Its beta
job reproduced the MPI initialization failure on 16V100/8V100V0 and retained
the old public version. The [failed route receipt](lammps-first-route-failure.json)
and original failed workflow remain unchanged. Publisher `9a3039b` revalidated
the same SIFs with the corrected account environment; public-route jobs
`1935854`, `1935856`, and `1935858` all passed. No application rebuild was needed.
`lammps/develop-beta-later` now selects
`develop-beta-20261025-049f269111f4-r75ef5179f1e8-bb0120cf26221`.
The [publication record](lammps-publication.json) and [live help](lammps-public-help.txt)
bind that selection to its accepted artifacts.

The actual [source transport](lammps-source-transport.json) sent a 329,504-byte
Git delta from the cached accepted ancestor, replacing an approximately 909 MB
full-history transfer. The pinned DeePMD dependency source was also a cache hit.

## Repairs exercised by this campaign

- ABACUS removed its old two-Si cuSOLVERMp test directory. The same reviewed
  input is now pinned with content hashes in the controller; current upstream
  pseudopotentials and orbitals are still used.
- GPUMD's installed baseline is 5.7 despite its `GPUMD-5.8` directory name.
  Its MD reader rejects new NEP files, while current `nep` rejects legacy files.
  MD/throughput use the same fixed compatible potential for both programs;
  current NEP prediction uses upstream's converter in the build container.
  Both model representations must reproduce the same pinned energy, force,
  and virial arrays. Current-source training, JIT, GNEP, PLUMED and DeePMD checks
  remain required.
- LAMMPS's new project cache was empty although the old home cache existed.
  It is now seeded locally without moving the old cache or using alternates;
  later source transport sends a verified Git delta from a cached ancestor.
- The image driver retries only Apptainer's explicit pre-payload fuse2fs mount
  timeout. A compiler or scientific failure is never classified as this retry.
- Beta publication requires a complete matrix. Partial/release-only requests
  leave beta unchanged; a failing release track does not block an independently
  accepted development matrix. Durable Slurm receipts, bounded infrastructure
  retry, immutable fixed modules, atomic symlinks, and generation comparisons
  preserve prior and newer selections during failure/reconnection.
- Infrastructure classification reads the application's actual `case/stdout.txt`
  as well as Slurm/training logs. The rendered launchers redirect both streams
  there, so ignoring that file would miss ECC and explicit pre-payload mount
  failures. Targeted tests cover both output locations and confirm that NaN
  results and the observed HOME/MPI environment failure are not auto-retried.
- ABACUS's small DSPRHBM paired cases requested 100 GiB per node and a
  one-hour allocation on 120 GiB nodes. Historical step MaxRSS was below 3 GiB.
  The current pending jobs were adjusted to 32 GiB per node and 20 minutes,
  retaining the same two nodes, eight ranks, two threads/rank, inputs and
  numerical/performance gates. The PW job started immediately after the memory
  adjustment. PW/HSE/DeePKS passed in 2m23s/3m17s/1m34s. The new recipe uses
  these measured requests for packaged CPU cases; custom cases retain 100 GiB.
  See the [PW](abacus-backfill-pw.json), [HSE](abacus-backfill-hse.json),
  [DeePKS](abacus-backfill-deepks.json) adjustments and
  [successful results with unchanged frozen inputs](abacus-backfill-results.json).
- Beta controller/fixture transport is now gzip-compressed. The measured
  payload shrinks from 3,450,880 to 1,096,440 bytes; every archive still receives
  a full hash check before immutable extraction.

## Verification

GitHub's full regression suite passes on commit `ca1bcd1`: 1,106 tests, with two
skips, in [run 40194858365](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40194858365).
[All six PR validation workflows](ci-ca1bcd1.json) pass. Coverage includes interrupted image
copying, complete/mixed partition matrices, changed images, retaining the old
alias after failure, held-job reattachment, same-artifact repeated publication,
and bounded infrastructure recovery. The local full-suite run began before the
workflow-test fixtures were updated; its affected tests subsequently passed,
including the two CMake checks after selecting the already-installed CMake.
