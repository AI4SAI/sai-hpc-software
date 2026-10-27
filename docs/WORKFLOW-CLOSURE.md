# Workflow closure — 2026-10-27

## October 27 recovery and deployment measurements

CP2K's October 26 source `d43cbde630af` is now publicly accepted on all four
targets. The same-image science retry 1958543 completed successfully on
`8v100v0n[02,08]`, but the original failed Actions run did not restart beta
promotion after that external recovery. The alias therefore really retained
October 25; this was not stale Lmod cache. Explicitly resuming beta validation
with the original source matrix in
[workflow 40434962493](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40434962493)
passed public checks 1967174/1967176/1967178/1967180 and atomically selected
`master-beta-20261027-d43cbde630af-r75ef5179f1e8-b1b8da72091d1`.
Normal login-node `module help cp2k/master-beta-later` shows that fixed version,
source version `master-2026-10-26` and accepted snapshot `20261027`.
Those dates describe source and acceptance separately. Source version labels
use the upstream commit's UTC date. At the October 27 02:41 UTC inspection,
upstream HEAD had advanced to `e90df42b4bd8` (October 26 19:39 UTC, October 27
03:39 in Beijing); it would still have an October 26 source label. That newer
commit is not part of this recovered acceptance. The active daily schedule is
02:23 UTC, but GitHub had not yet created an October 27 scheduled run at that
inspection; the previous three scheduled runs were created after 07:00 UTC.

LAMMPS's previously queued 4V100 build 1955376 and 8V100V0 science 1958570
also completed successfully. Workflow 40316850352 succeeded and automatically
selected `develop-beta-20261026-049f269111f4-r75ef5179f1e8-b6733dc9a2745`.
The ABACUS DSPRHBM performance failure described below is still unresolved.

The [release/SIF experiment](evidence/release-sif-efficiency-20261027/README.md)
completed all 12 combinations of CP2K, ABACUS, LAMMPS and DeePMD on the three
V100 partitions, including 144 measured scientific calculations. Exact native
administrator-installed releases were compared with their original SIFs using
the production launcher, with byte identity, placement, numerical outputs and
terminal accounting independently revalidated. On 16V100, 64-water CP2K OT
internal time changed by +0.04% and whole-process time by +0.45%; LAMMPS engine
time changed by -0.48% and DeePMD inference by -1.11%. The report keeps startup,
short-workload limitations, storage variability and all unsuccessful preparation
attempts explicit. GPUMD's installed binaries differ from both retained 5.8.1
and 5.9.1 SIFs, so they cannot form a same-build packaging comparison.

## October 26 MD follow-up

The scheduled daily run triggered at 07:51 UTC. GPUMD's complete development
matrix was accepted and its beta rotated. DeePMD's three builds and LAMMPS's
16V100 build instead hit fuse2fs teardown errors after compilation. The MD
builders now explicitly synchronize both writable phases before teardown and
check each unmounted overlay with `e2fsck -fn`. Original failed runs remain
recorded. See [repair and acceptance evidence](evidence/md-overlay-sync-20261026/README.md).

The fresh [DeePMD workflow](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40316174805)
has accepted final SIFs for source `4e1479d8a23f` on 16V100 (1955178),
4V100 (1955181) and 8V100V0 (1955183). All three prove the installed DDP patch with
actual two-GPU `se_e2_a`/DPA2 training, checkpoint restart, finite gradients
and synchronized model updates. Public module checks 1956382/1956384/1956386
repeated inference and both DDP cases successfully. The workflow succeeded and
`deepmd-kit/master-beta-later` now selects
`master-beta-20261026-4e1479d8a23f-r75ef5179f1e8-b9fb80a6e4fee`.
This closes the October 26 DeePMD SIF publication; the evidence establishes
single-node two-GPU PyTorch training and restart.

The corrected [LAMMPS workflow](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40316850352)
completed builds on 16V100/8V100V0 and remains queued on 4V100 (1955376).
The completed builds were advanced to science using their original frozen
controllers and Actions handoffs. 16V100 job 1958567 passed all 14 scientific
cases, with 71 packages and 2,500 styles; 8V100V0 science job 1958570 is queued.
ABACUS DSPRHBM passed
numerical checks but its permitted performance retest exceeded the 1.10
threshold (PW 1.10909, HSE 1.20), so its public beta also remains unchanged.
CP2K's 8V100V0 build completed, but science job 1956309 failed during
`--version` with `cudaErrorECCUncorrectable` on rank 1 / node `8v100v0n09`,
before any scientific case. Retry 1958543 uses the identical SIF and frozen
controller in a separate evidence directory, excluding that node. The other
three CP2K targets passed acceptance; its public beta retains the prior
generation until the remaining science and public launch checks pass.
See the [dated status snapshot](evidence/md-overlay-sync-20261026/gap-status.json).
The completed October 25 matrix
below is retained as a historical acceptance record, not relabelled as today's
result. Linux validation of the MD repair passes 1,112 tests with two optional
skips.

## Daily development beta closure

The scheduled workflows did run on October 23–25, but failed/cancelled builds
and the missing beta promotion stage left the public modules unchanged. Source
dates in module help identify upstream commit dates, not the last build date.
The daily workflow now connects each software's complete development matrix to
scientific acceptance, real Lmod/SIF launch verification and atomic beta rotation.
The schedule remains 02:23 UTC / 10:23 Asia/Shanghai. Failed or partial matrices
retain the previous accepted generation, and the five applications publish
independently. See [daily closure evidence](evidence/daily-beta-20261025/README.md).

The completed campaign resolves each source once and retains that identity
through recovery. All 17 application/target entries are scientifically accepted
and publicly selected as of October 25, 17:36 UTC (October 26, 01:36 Asia/Shanghai):

| Program / public entry | Selected source | Current campaign |
| --- | --- | --- |
| `abacus/develop-beta-later` | `89595dd29a26aec34d623518cadf736c17345f69` | Four targets built, scientifically accepted and beta-published on October 25. |
| `cp2k/master-beta-later` | `1db19ea511d10fbf2eb75cec7c0d88b6635563e2` | All four targets built, scientifically accepted and beta-published on October 25. DSPRHBM fresh recovery passed dependencies 1937947, application 1938812 and science 1939349; all four public module checks passed in 1939536–1939542. The original corrupt dependency checkpoint and failed workflow remain recorded. |
| `deepmd-kit/master-beta-later` | `2ba617bc3db67adee2b8ba00c71e9fa1c2d2bb8c` | Three targets accepted and beta-published on October 25; actual two-GPU se_e2_a/DPA2 training and restart passed. |
| `lammps/develop-beta-later` | `049f269111f42ac408f706e0cc94b37f50346baf` | Three targets built, scientifically accepted and beta-published on October 25; 14 scientific cases, 71 packages and 2,500 styles per target. |
| `gpumd/master-beta-later` | `30a6db02e36494b8bba71c9fa1d1a68627604961` | Three targets accepted and beta-published automatically by successful Actions run 40190615647. |

The [final public preflight](evidence/daily-beta-20261025/public-preflight.json)
verified all selected SIF checksums, immutable payload permissions, runtime files
and launchers. [Module checks](evidence/daily-beta-20261025/public-module-checks.json)
confirm that ordinary cached help sees the new fixed versions; login-node loads
print version/help and fail without leaving partially loaded modules or SIF
environment variables. Source dates remain October 23/24/25, while every selected
generation records acceptance on October 25 UTC.

All computation, build scratch and raw evidence use
`/org/org-open-sai/proj-sai-hpc-software` with account `proj-sai-hpc-software`.
Static `/opt` beta gateways read stardust's home-maintained immutable modules and
SIFs. Normal execution reads directly from SIF; no application extraction or new
user sbatch template is required. Native `/opt` software installation is a
separate administrator action.

## Earlier SIF and runtime acceptance

The [20261024 SIF module snapshot](../deployment/sif-development-20261024/README.md)
exposed all five accepted development sources through independent `*-sif`
Lmod modules, selecting the image by Slurm partition. All 17 application/target
entries passed fresh numerical checks of this launch route in jobs
`1919703`–`1919706`, including two-node/four-rank host MPI on 16V100. Apptainer
shared user namespaces remove the observed UCX permission failures; MPI shared
memory pools use RAM while disk scratch stays in the project/home directory.
The fixed runtime requires neither a writable artifact tree nor per-rank SIF
rehashing. An administrator-readable handoff is prepared separately from the
private project tree; public `/opt` installation is still administrator-driven.
These SIF entries do not relabel the original acceptance records or install
the native staging exports.

The [branch-beta entries](../deployment/sif-beta/README.md) use static `/opt`
gateways and home-maintained `*-beta-later` symlinks. Runtime changes receive a
new fixed name `<branch>-beta-20261024-<source SHA>-r<runtime SHA>`, preserving
previous fixed modules and loaded environments through rotation. Login-node
loads print the actual version, commit, supported partitions, ordinary launch
command and `/opt/sbatch_examples`, then fail without partially loading modules.
There are no new user sbatch templates.

The private-memory runtime `a4c6a24c229f` passed all 17 application/partition
combinations on the unchanged accepted SIFs. Host MPI starts through thin
wrappers that establish private mount namespaces; scratch uses tmpfs and is
invisible to another same-user job's epilog. Restricted nodes use the installed
Apptainer profile and an independent 36 KiB mountpoint-only bootstrap SIF.
No application tree or whole image is extracted/copied into RAM. The maintained
home runtime is 112,992 bytes; existing application SIFs are reused in place.

Acceptance includes two-node 16V100 host MPI, one-rank-per-GPU site mapping,
actual concurrent same-user jobs on 16V100 and DSPRHBM, namespace teardown, and
another two-step single-GPU DeePMD training run. The evidence records startup
costs and tmpfs/swap limitations; it does not claim a new performance speedup.
See [memory runtime acceptance](evidence/sif-memory-runtime-20261025/README.md).
Generation `20261025-memory-r1` passed the final module checks and was
promoted in job `1924922`. The administrator handoff remains
`/home/stardust/sai-hpc-software/deployment/sif-beta/install.sh`; its preflight
checks the selected immutable payload and acceptance before writing the five
static gateways. `/opt` installation is still administrator-operated.
Historical generations, templates and acceptance records remain unchanged.

## Previous development deliveries

The preceding development campaign used the funded project directory
`/org/org-open-sai/proj-sai-hpc-software` and explicit Slurm account
`proj-sai-hpc-software`. Frozen historical controllers and existing home
artifacts remain unchanged. Build, science, publication and native `/opt`
installation are separate acceptance states.

| Program | Selected development commit | Evidence from the preceding campaign |
| --- | --- | --- |
| ABACUS `develop` | `370ba685e6707b3625c5580ff9d02ef0afb248e8` | All four targets built, scientifically accepted and published with reverified native staging exports; workflow [39971072923](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39971072923) succeeded. Physical `/opt` installation pending. |
| CP2K `master` | `cfe5e5d87aaf302953f14eb43587bef2411a8fb2` | All four targets built, scientifically accepted and project-published, with independent SIF, raw science, native export and module audits. Source-pinned LibXC 7.1.2 and LibXS `930a2c4` are verified. 4V100/8V100V0 completed through documented recoveries; the original [workflow 39983165813](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39983165813) retains its failed status. Physical `/opt` installation pending. |
| DeePMD-kit `master` | `558f56962775098b8b554e341c3b37d2f86e6aeb` | New SIFs with the compression-table DDP patch pass TF/PT/JAX inference/API, measured CUDA execution, and single-node two-GPU `se_e2_a`/DPA2 training and checkpoint restart on all three V100 targets. Single-GPU regression, old-checkpoint restart, model compression and public module launch also pass. Published as beta generation `20261025-deepmd-ddp-r2`; TensorFlow/JAX remain CPU-only. Native installation of this development snapshot remains pending. |
| LAMMPS `develop` | `a0e2bd6efb3d67a29c0c4e4de3b19556ffdb9d5b` | All three V100 candidates build-verified and scientifically accepted: 14 cases, 71 packages, 2,497 styles per target. Available through the 20261024 SIF modules; legacy catalog publication and native installation remain pending. |
| GPUMD `master` | `2c93346fce4461de1c42c77e7fe74cdb6b0e3df6` | All three V100 targets scientifically accepted, published with native staging exports; physical `/opt` installation pending. |

All five preceding development snapshots completed their required build
and scientific acceptance gates. The table records those historical source
snapshots. A later
[read-only upstream check](evidence/development-project-20261023/upstream-recheck-20261024.json)
found newer ABACUS and CP2K commits while these jobs were running; the other
three branch heads were unchanged. New commits are resolved in subsequent
daily runs and do not replace the identities of running or accepted artifacts.

The [20261025 DDP repair](evidence/deepmd-ddp-20261025/README.md) now accepts
single-node two-GPU PyTorch training for `se_e2_a` and DPA2 on all three V100
partitions. The workflow freezes inference-only compression ParameterLists after
model assembly, preserving checkpoint layout and retaining the single-task
`find_unused_parameters=False` optimization. New SIFs use recipe `7d8ce7396215`;
the original application images remain available for rollback. The original
build jobs retain their failed acceptance-runner status; independent jobs
`1930666`, `1930668` and `1930702` verify those exact immutable new images.

Runtime jobs `1930729`–`1930731` additionally verify single-GPU training/restart,
restart from pre-fix checkpoints, compressed-model consistency and GPU inference.
Generation `20261025-deepmd-ddp-r2` initially selected these SIFs through the existing static
`/opt` gateway and the home-maintained `deepmd-kit/master-beta-later` alias.
Public job `1930769` passes ordinary `deepmd-python -m torch.distributed.run`
DPA2 training with two GPUs. The earlier
[ldconfig/JIT runtime fix](evidence/deepmd-ldconfig-20261025/README.md) remains in
runtime `75ef5179f1e8`. This acceptance does not establish multi-node or
more-than-two-GPU scaling, and TensorFlow/JAX remain CPU-only.

The [20261025 multi-GPU audit](evidence/deepmd-multigpu-20261025/README.md)
confirms that the **installed native 3.2.0 release** and the site's existing
3.2.0 Conda environment both pass two-GPU `se_e2_a` and DPA2 training on all
three V100 partitions, including checkpoint save and restart from step 8 to 12.
Both ranks perform CUDA backpropagation with finite gradients and identical
final parameters. That audit used the earlier `a4c6a24c229f` SIF runtime. The
subsequent ldconfig fix removed the Triton discovery failure; the new DDP repair
and freshly built SIFs described above now also pass the two-GPU training gate.
The framework probe also confirms CPU-only TensorFlow 2.21.0 and JAX 0.11.1,
with no Horovod. TensorFlow multi-GPU needs both a CUDA-enabled framework and
Horovod; native PyTorch success does not establish those capabilities.

[ABACUS development evidence](evidence/abacus-development-20261023/README.md)
records 23 successful build/acceptance jobs and independent checks of all four
SIFs, raw scientific results, modules and 8,713 native export entries per target.
The three GPU targets passed actual two-node cuSOLVERMp/NCCL execution. All
declared PW/HSE/DeePKS SCF iteration-time gates pass; total wall times for these
small cases are recorded separately and do not establish a general speedup.

[LAMMPS development evidence](evidence/lammps-project-20261023/README.md)
records recovery of the already compiled 8V100V0 overlay in export job
`1912932` and science job `1912959`; the old build `1896876` remains failed.
4V100 and 16V100 reused their unchanged-source SIFs after independent rereading
of all raw acceptance evidence. No new DeePMD build was needed for LAMMPS.

[DeePMD development evidence](evidence/deepmd-project-20261023/README.md)
records separate scientific jobs `1913313` and `1913315` on the unchanged
4V100/16V100 SIFs. The 8V100V0 export/science recovery `1913472` completed in
3m31s using an independently copied overlay; its original full 32 GiB hash
was checked again after recovery. All three original build allocations keep
their failed status and original recipe. Every accepted target passes 48
numerical records, the six backend/API groups and actual PyTorch CUDA execution.

Daily planning now selects all five applications independently. The standalone
DeePMD workflow requires its own numerical, measured GPU execution and two-GPU
PyTorch training/restart checks;
LAMMPS reuses the pinned installed DeePMD dependency and requires its own 14-case
single/two-node GPU acceptance. A new DeePMD failure does not prevent LAMMPS from
building, and a successful build without scientific acceptance is not reported
as accepted. Slurm accounting visibility delays remain pending; a terminated
job missing its required acceptance markers fails immediately.
New DeePMD jobs seal the verified SIF before science and then bind the numerical
results, model inputs and GPU trace to that exact image and allocation. LAMMPS
build and science now have separate Actions time budgets; the science job uses
the original frozen handoff and resumes an existing acceptance allocation
without submitting it again.

The project archive cache avoids repeating slow GitHub-to-cluster uploads for
identical dependency inputs. Its six initial archives total 25.14 MB; a real
checksum-and-copy cache-hit probe took 0.751 seconds with zero archive upload.
Each run gets independent file contents. See the
[cache probe](evidence/development-project-20261023/cache-hit-probe.json) and
[CP2K project input prerequisites](evidence/development-project-20261023/cp2k-project-inputs.md).
The [first CP2K attempt](evidence/development-project-20261023/cp2k-first-attempt/summary.json)
retains all four failed allocations and original logs.
The next attempt passed the corrected CP2K environment selection and began
dependency compilation. A read-only source preflight then found that current
master had upgraded LibXS and removed its old JIT repair patch. The previous
recipe would silently build unpatched 1.0.0. That Actions run was stopped
before any application submission; its four dependency allocations completed
successfully and were sealed by their original controller. Their original
prefixes and receipts are preserved in the
[checkpoint record](evidence/development-project-20261023/cp2k-39976482528-dependency-checkpoints.json);
they are not application acceptance or inputs relabeled for the corrected recipe.
The [LibXS preflight](evidence/development-project-20261023/cp2k-libxs-preflight.md)
records the source evidence, verified archive, cancellation and binary-cache
assessment. The corrected workflow follows each source's declared dependency
version without changing the installation identity contract.
The retry resolved the newer master commit `cfe5e5d87aaf` once for all four
targets. It directly follows `04b255137f7b` and changes Fortran reference
counting without changing the dependency or toolchain declarations.
All four dependency allocations completed successfully. The first 4V100
application allocation `1914241` then failed at Apptainer 1.4.4's fixed
10-second fuse2fs mount deadline, before any application compilation. Its
dependency clone had passed both filesystem checks. A later read-only probe
on the same node completed both attempts, but does not identify the cause of
the original writable mount timeout. Fresh application `1914645` passed the
mount, but an agent's `srun` diagnostic requested one CPU per task, violating
4V100's eight-logical-CPU-per-GPU rule; the site hook cancelled that allocation.
This is an operational mistake, not a CP2K or container failure. Its command,
site error and accounting remain in the [incident evidence](evidence/cp2k-development-20261023/README.md#4v100-mount-failure-and-fresh-application-attempt).
Job `1914931`, under a new run ID ending in `-mount-retry2`, used the same
source/recipe, original frozen controller and original sealed dependency input.
It encountered a second genuine pre-entry mount timeout on `4v100n01`.
The next submission, `1915039`, tried to exclude that node using an environment
variable unsupported by the installed Slurm. The submission postcheck caught
the missing exclusion; only that new allocation was deliberately cancelled.
Job `1915163` was then cancelled while held because a check incorrectly expected
the scheduler-derived CPU field before scheduling; it consumed no compute time.
A subsequent attempt with explicit CPU resources was rejected before allocation:
SAI requires GPU-only requests and derives the CPU count itself. These operational
errors and their original records remain separate from the real mount failures.

Fresh 4V100 job `1915249` (`-node-retry4`) completed on `4v100n04` in 55m25s,
with the explicit `4v100n01` exclusion and eight scheduler-assigned logical CPUs.
Its final SIF passed seven-case single/two-node science in job `1915730`
(7m49s), and project publication completed at 19:42:38 UTC on October 23.
The [independent delivery audit](evidence/cp2k-development-20261023/4V100-validation.json)
revalidated the SIF, raw science, native export, module, successful allocations
and all preserved recovery attempts. Physical `/opt` installation is separate.
8V100V0 job `1914617` also failed at the same pre-entry mount deadline after
finishing its clone checks. Fresh job `1915260` (`-node-retry1`) completed in
1h54m52s on `8v100v0n03`, excluding `8v100v0n02`, with six scheduler-assigned
logical CPUs. Its mount took 2.125 seconds after the original clone checks.
Science job `1916019` passed all seven single/two-node cases in 13m29s on
`8v100v0n03` and `8v100v0n04`; project publication completed at 20:48:40 UTC.
The [8V100V0 delivery audit](evidence/cp2k-development-20261023/8V100V0-validation.json)
independently verified the final SIF, raw science, native export, module and
the original failed allocation plus the successful recovery.
Both new jobs passed held-allocation and immutable-input checks before release;
a separate read-only revalidation checked their running allocations and recovery
provenance. They retain the original controller, recipe and sealed dependency
checkpoint. Both recoveries completed their application, science and project
publication gates; the original workflow failures are preserved. Changing
nodes succeeded in this campaign, but does not establish the mount timeout's
root cause or remove Apptainer's fixed 10-second startup deadline.

The [16V100 delivery audit](evidence/cp2k-development-20261023/16V100-validation.json)
independently revalidated the final SIF, seven single/two-node scientific cases,
native export, module and successful dependency/build/science allocations
`1913830`, `1914677` and `1915010`. This is project publication; physical `/opt`
installation remains separate. The [DSPRHBM audit](evidence/cp2k-development-20261023/DSPRHBM-validation.json) likewise passed after science job `1915020` and project publication.

The [LibXC supplement](evidence/cp2k-development-20261023/README.md) ran on the
four final SIFs with thresholds fixed before execution. Its
built-in-PBE versus LibXC HOST energy comparison failed the original `1e-6` Ha
gate on all four targets (difference approximately `4.16e-6` Ha); the failure records
and original thresholds remain intact. Source inspection shows different default
PBE/PW92 constants in these implementations, including an explicit CP2K comment
recommending LibXC for higher-precision program comparisons. The 16V100 LibXC
HOST/DEVICE energy difference was `2.34e-13` Ha, with 94 measured LibXC CUDA
kernel invocations and the actual private 7.1.2 loader path. The independent
pre-existing DFT-reference protocol subsequently passed all 258 scalar
comparisons on each final LibXC HOST provider. The 4V100 HOST/DEVICE difference
was `9.95e-14` Ha with identical forces and 94 LibXC CUDA kernel invocations.
The 8V100V0 difference was `3.27e-13` Ha and at most `1.01e-12` Ha Bohr⁻¹
in force, also with 94 LibXC CUDA kernel invocations. All three GPU targets
pass the original HOST/DEVICE thresholds. The source-default mismatch in the
built-in/LibXC comparison remains a separately documented failed comparison.
The [read-only combined summaries](evidence/cp2k-development-20261023/libxc-reference/summaries/)
recompute the raw mathematical and CP2K comparisons and preserve the original
built-in comparison failures unchanged.

[Branch integration audit](evidence/development-project-20261023/branch-audit.md)
identifies two old branches already merged through equivalent patches. The
remaining CP2K MPI experiment tools/evidence were independently verified and
merged in [PR #24](https://github.com/AI4SAI/sai-hpc-software/pull/24). Current
CP2K master already contains the upstream repair in cp2k/cp2k#6338; its
2026.2-only experiment patch is not applied to master.

The older release and development matrices below are historical checkpoints;
they do not override the current source-specific results above.


PRs [#8](https://github.com/AI4SAI/sai-hpc-software/pull/8) and
[#10](https://github.com/AI4SAI/sai-hpc-software/pull/10) are merged into `main`.
Daily delivery now uses reusable workflows from its own revision and waits
for their results. The old feature-branch dispatch failures described below
are historical. ABACUS's latest GitHub release, `v3.11.0-beta11`
(`b0411118d360`), now has corrected SIFs on all four partitions from
[run 39808766467](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39808766467).
All passed version, library, feature and packaged-file checks. 4V100,
16V100 and 8V100V0 passed automatic two-node SCF, distributed cuSOLVERMp/NCCL,
PW, HSE and DeePKS acceptance and were published with reverified native exports.
DSPRHBM also passed and was published after a diagnosed CPU reference-library
recovery using the same SIF. The old reference loaded unified NVIDIA ELPA;
selecting the installed AVX512 CPU ELPA fixed its HSE failure. Future CPU
benchmarks select and verify that library without changing ranks, scientific
tolerances or timing thresholds. The final 8V100V0 acceptance jobs
`1901416`, `1901616`, `1901623`, `1901688` and `1901712` completed with `0:0`;
independent revalidation of raw science, SIF checksum, native file bytes and
module metadata passed. All four partitions are now accepted and published
in the home tree; the administrator has also installed their physical runtime
files and public module under `/opt`.
The original CPU acceptance job remains failed in GitHub's audit history;
this run is not claimed as an entirely successful automatic workflow.
PR #17 repaired acceptance after skipped CP2K dependencies, selected
partition-specific ELPA inside the build container, enabled concurrent
partition builds and corrected GPU affinity and upstream energy references.
See the
[release build and acceptance record](evidence/abacus-beta11-20261022/README.md).
The [beta11 administrator deployment package](../deployment/abacus-3.11.0-beta11/README.md)
passed four-partition source/reference preflight, a real copy/readback
rehearsal under home storage and `saiuser01` source/target access checks.
The physical installation now passes `--verify` and independent readback of
all 76 regular files, four symlinks and the public module. Both ordinary and
cache-bypassing module queries list `abacus/3.11.0-beta11-auto`.
The install contains about 1.83 GiB of standard runtime files and a standalone public
partition selector. Workflow metadata, test inputs and private dependency
development files stay outside `/opt`. All 30 single/two-node native scientific
calculations now pass in jobs `1905277`, `1905278`, `1905279` and `1905309`,
each `COMPLETED / 0:0`. Raw-result revalidation, exact loader/feature/placement
checks, real cuSOLVERMp/NCCL execution, module unload and post-run installation
readback also pass; maximum reference energy error is `2.046e-8 eV`.
The new work and scratch directory is `/org/org-open-sai/proj-sai-hpc-software`,
and Slurm accounting confirms `proj-sai-hpc-software` on every accepted job.
Existing build artifacts and reference data remain under home storage.
The first CPU job `1905280` failed before application execution because the
explicit MPI host list provided one slot per node; specifying four CPU slots
per node fixed it without changing resources or scientific criteria.
The superseded private-account jobs `1903636`–`1903639` were cancelled while
pending; their files and the failed CPU attempt are preserved.
[Physical native acceptance and project-account evidence](evidence/abacus-beta11-20261022/physical-native-acceptance.json).
This controller revision routes all new automatic builds and acceptance to
the funded project directory/account. Frozen home-based continuations retain
their original controller revision and root policy.
Optional public deployment of the container image driver is tracked in
[issue #12](https://github.com/AI4SAI/sai-hpc-software/issues/12).

The installed `fuse2fs/1.47.4-sai` module has passed all 13 public-install
checks. New ABACUS and GPUMD batch scripts now load it with Apptainer, covering
their writable build/export and read-only overlay extraction phases. CP2K,
DeePMD and LAMMPS already explicitly select the same optimized driver bytes.
Loading only `apptainer/1.4.4` still uses its bundled helper; see the
[single-command opt-in and workflow behavior](../deployment/fuse2fs-1.47.4-sai1/README.md#use).
This enables the measured I/O optimization for future builds; scientific
acceptance of new ABACUS/GPUMD deliveries remains part of their own workflows.

October 23: GPUMD `v5.9.1` (`11ba6fbdb4d7`) now has verified SIFs on all three
V100 partitions from [run 39955137406](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39955137406).
Builds `1911768` (4V100), `1911773` (16V100) and `1911767` (8V100V0) completed
in 80, 119 and 176 seconds. All three passed GPU science/performance,
host launcher execution, native export readback and publication. Independent
raw-result and export revalidation passed on every target; NEP throughput differs
from the site baseline by less than 0.5%. Science jobs `1912292`, `1911814`
and `1911819` completed in 94, 129 and 128 seconds respectively.
All new work uses `/org/org-open-sai/proj-sai-hpc-software` and account
`proj-sai-hpc-software`. Project-inherited ACLs initially prevented writable
overlay startup; clearing ACLs only on private scratch fixed it. New GPUMD
jobs use partition-authorized one-GPU QoS (rush on 4V100/8V100V0, flood
on 16V100) and shorter time requests for backfill. Original attempts, scheduling adjustments and frozen controllers
are preserved. Physical `/opt` installation and its native execution are
still pending.
Current GPUMD `master` (`2c93346fce44`, October 21) also passes all three
partitions in [run 39963334471](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39963334471).
Builds `1912478`/`1912477`/`1912479` completed in 73/129/194 seconds, and
science jobs `1912533`/`1912536`/`1912539` in 94/122/125 seconds. All are
`COMPLETED / 0:0`. Raw numerical outputs, all 457 native export files per target
and published modules passed independent revalidation; benchmark differences
are below 0.3%. The first attempt exposed a source-packaging exclusion that
removed upstream's new `main_nep/nep/` runtime directory, plus an unsupported
16V100 QoS. Both are fixed without relaxing JIT, scientific or performance
checks. This closes GPUMD release/master container delivery on three V100
partitions; it does not assert closure of other applications' development tracks.
[GPUMD project delivery record](evidence/gpumd-project-20261023/README.md).

October 22: [independent LAMMPS candidates](LAMMPS-INDEPENDENT.md) reuse the
accepted physical DeePMD 3.2.0 dependency. The first 16V100 develop build
(`a0e2bd6efb3d`, job `1896356`) completed in 55m57s and produced a verified
252 MiB SIF. Its 14 single/two-node GPU scientific cases passed in job
`1896820`, independently reverified with the pinned installed DeePMD dependency.
All release packages/styles remain present (71 packages, 2,497 styles).
The 4V100 build `1896871` completed in 41m08s; science job `1897165` passed
the same 14 single/two-node cases in 57s, with independent raw revalidation.
Two earlier attempts failed in MPI core binding before application execution;
the new acceptance launch keeps Slurm's CPU affinity without extra PRTE binding.
The SIF and scientific inputs are unchanged. Native publication, 8V100V0's
development acceptance and automatic daily integration are still pending.

LAMMPS release work is now tracked in [LAMMPS-RELEASE.md](LAMMPS-RELEASE.md).
The October 20 release restart now has published, reverified `stable_30Sep2026`
SIFs paired with DeePMD 3.2.0 on **4V100, 16V100 and 8V100V0**. All three passed
two-node TF/PT/JAX numerical acceptance, explicit GPU/Kokkos LJ/PPPM checks,
PyTorch CUDA performance, full native export readback and actual execution
through the public container module. The new 71 packages/2,473 styles preserve
the old 70 packages/2,197 styles. At the user's request, performance acceptance
records GPU scope explicitly; CPU timing is excluded. The
[delivery record](evidence/lammps-release-20261020/final-delivery.json) pins the
accepted images and evidence. The [physical installation package](../deployment/lammps-30Sep2026/README.md)
passed its full read-only preflight and now includes post-install native
Slurm checks plus raw-result revalidation: 17 scientific cases per partition
cover single/two-node GPU/Kokkos, TF/PT/JAX and PLUMED execution. The physical
installer now copies only standard runtime/API/data files (about 1.09 GiB per
partition); workflow metadata, custom checks and reference material stay in
the home tree. Its public module is standalone. The cross-account read/traverse
gap reported by `saiuser01` is repaired with targeted ACLs; effective source
and target access is audited in the
[runtime deployment evidence](evidence/lammps-release-20261020/runtime-deployment-preflight.json).
The administrator's physical `/opt` installation is now accepted on all three
partitions. Native jobs `1885341`/`1885342`/`1885343` passed 17 scientific
cases each, full feature/loader/placement checks and module unload, with
`COMPLETED / 0:0`. Independent raw-result revalidation and post-run installed
file readback also passed; [physical native evidence](evidence/lammps-release-20261020/physical-native-acceptance.json)
pins the frozen inputs, results and accounting. The native batch script now
lets site policy derive CPU allocation from GPU count. Before the October 22
integration, the daily dispatcher ran the old MD feature branch; its release history
check rejects newer recovery identities, while its development build fails
CUDA configuration. The stable restart uses the integrated controller,
explicit GCC 14.3.0 and the qualified CP2K overlay driver, preserving all
existing evidence. 8V100V0 extraction recovery `1882907` completed successfully.
An export wrapper permission issue was repaired and all failed attempts retained.

The code now connects build, scientific/performance acceptance, native export
readback and publication. The current shared-ELPA CP2K 2026.2 is physically
installed on all four partitions and selected by `cp2k/2026.2-auto`. Jobs
`1879431`–`1879434`, executed as `stardust`, passed all 56 native single/two-node
calculations and ended `COMPLETED 0:0`. Independent remote and local raw-log
revalidation confirmed version/features, actual shared ELPA/HPCX HCOLL loading,
MPI placement and numerical agreement with the accepted SIFs. Maximum energy
and force differences were `2.87e-11 Ha` and `1.30e-11 Ha/Bohr`. Original data
remain complete; runtime uses the physical installation without Apptainer.
[Current physical acceptance and raw evidence](evidence/cp2k-shared-elpa-native-20261020/README.md).

The large-water CPU/GPU benchmark is being repeated after confirming upstream
CP2K issue #6074: the original CPU baseline unintentionally diagonalized on
half its MPI ranks. The CPU-only repair changes two lifecycle blocks in one
source file; container relinks preserve the original control bytes and reuse
all other 1,389 objects. Three controlled A/B jobs passed, with real MPI traces
confirming all intended solving ranks through 64 ranks and numerical agreement
across repeated solves. The 216-water median total-time reductions are 5.79%
(4V100/16 cores), 38.30% (16V100/16 cores) and 16.24% (16V100/64 cores).
The original five larger CPU/GPU points are being repeated; public installs
are unchanged. [Repair and actual MPI/timing evidence](evidence/cp2k-scalapack-mpi-repair-20261022/README.md).

The original private-ELPA CP2K 2026.2 on all four partitions (4V100, 16V100,
8V100V0 and DSPRHBM) has passed candidate science, native export readback,
publication, physical `/opt` installation and native single/two-node science.
Development is not yet accepted. GPUMD's latest release status is recorded
above; its additional physical native execution check remains pending.
Production acceptance is incomplete across the full site matrix. Original dirty
worktrees and recovery checkpoints remain preserved. At the user's request,
the ready-to-run [CP2K physical deployment package](../deployment/cp2k-2026.2/README.md)
now includes the four-partition module, selective installer and post-install
native science scripts. The user installed all four accepted trees and the public
module. The `stardust` account verified the physical bytes, links and permissions,
then jobs `1844993`–`1844996` passed all 56 native scientific calculations, each
ending `COMPLETED 0:0`. Independent raw-log revalidation confirmed energies,
forces, exact versions/features, real HPCX HCOLL/private ELPA loading and MPI
placement. Maximum differences from the accepted SIF results were `2.12e-11 Ha`
and `1.60e-11 Ha/Bohr`. Original data remain complete (280 files and two links
per partition); no application container is used for native execution.
[Physical installation acceptance and raw evidence hashes](evidence/cp2k-install-layout-20261018/physical-native-acceptance.json).

The new shared-ELPA recipe `aed7aa53ff43` has now also completed all four
application builds, 56 single/two-node scientific calculations, native export
readback, publication, administrator installation and physical acceptance.
The 216-water diagonalization experiment completed on all three GPU partitions:
direct cuSOLVERMp reduced median total time by 9.84%, 22.22% and 14.19% on
4V100, 16V100 and 8V100V0. A separate controlled OT backend experiment passed
all 24 calculations; shared ELPA reduced OT total time by 13.10%, 7.06% and
5.57% for that input. Defaults remain unchanged.
[Accepted release](evidence/cp2k-shared-elpa-release-20261020/README.md),
[diagonalization comparison](evidence/cp2k-water216-tuning-20261020/README.md),
[OT comparison](evidence/cp2k-ot-backends-20261020/README.md).

An ordinary-user native-versus-SIF benchmark now passes all 112 scientific
calculations across four partitions, with physical home input/output in both
routes and matching executable/library bytes. Twenty alternating startup pairs
on 16V100 measured a 0.400 s median additional rank lifetime for direct SIF
execution. The 64-water OT wall-time medians increased by 0.82% on 16V100 and
0.43% on 8V100V0; substantial storage variability limits interpretation of other
cases. Independent remote/local revalidation agrees on all 2,251 archived file
hashes and raw results. The failed first 4V100 native attempt and differing
shared CUDA library hashes on that node remain documented; another node passed
the full retry without a workaround.
[Container overhead measurements and complete evidence](evidence/cp2k-container-overhead-20261020/README.md).

The two-node follow-up on 16V100 also passes: four V100s per node, eight MPI
ranks and ordinary-user host-filesystem I/O. Jobs `1884803` and `1884745`
completed all 24 calculations for 64-water OT/cuSOLVER and 216-water OT/ELPA.
Independent remote/local revalidation agrees on 2,063 archived file hashes
and raw results. The 216-water wall-time median ratio was +0.34% for SIF,
with a -2.248 s paired delta median; no clear SIF performance loss appears
in these five pairs. The 64-water -7.13% median ratio coincides with a strong
time trend inside cuSOLVER and is not evidence of container acceleration.
[Two-node measurements, all pairs and retained attempts](evidence/cp2k-container-multinode-20261021/README.md).

Cross-account deployment now grants `saiuser01` read/traverse ACLs only on the
blocked release/reference paths, with no added write access. The 2,415 required
paths passed effective POSIX ACL checks; native checks write results, caches and
MPI scratch to the executing account's own home work tree. Frozen manifests,
scientific references and inputs still match their pinned hashes.
[Cross-account access evidence](evidence/cp2k-install-layout-20261018/cross-account-access.json).

## Implemented and locally verified

- October 19 shared-ELPA follow-up: the accepted standalone dependency now has
  a [unified reproduction guide](ELPA.md). A new CP2K recipe pins its three
  physical ELPA variants by file hashes and uses the matching partition paths.
  All four GCC 14.3 Fortran compatibility probes (`1862298`–`1862301`) passed
  against the site's GCC 13.3 ELPA modules and library. This establishes a
  cross-compiler consumer check, not acceptance of a new CP2K application.
  The previously installed four-partition CP2K remains the accepted release
  until new candidate science completes. [Preflight evidence](evidence/cp2k-shared-elpa-20261019/preflight.json)
  and [issue #9 tuning protocol](CP2K-V100-TUNING.md#shared-elpa-rebuild-and-issue-9).
  Release workflow [39262701906](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39262701906)
  passed 818 Linux tests (816 passed, two skipped), resolved v2026.2 and started
  fresh dependencies on 16V100 `1862478`, 4V100 `1862509`, 8V100V0 `1862503`
  and DSPRHBM `1862508`. Recipe `46ac9460660f` installs at a new canonical
  prefix. [Frozen submissions and observed progress](evidence/cp2k-shared-elpa-20261019/build-start.json).
  A separate GPU object-lifecycle audit `1862456` found the accepted shared
  ELPA lacks upstream PR #85's temporary-communicator free: eight objects
  left eight tracked communicators, despite passing all numerical checks.
  The integration build continues; long-running GPU qualification requires
  the [archived repair and fresh dependency validation](ELPA.md#gpu-求解器生命周期限制).
  The repair is now in the shared build recipe: GPU jobs `1862858`/`1862859` and
  CPU job `1862898` passed, as did four-partition candidate checks
  `1862899`/`1862929`/`1862930`/`1862931`. Tracked GPU communicators now return to
  zero after 32 object lifetimes per rank. The administrator replacement package
  passed preflight. On October 20 the physical files matched all three repaired
  inventories. A simplified 36-line selector passed four native checks
  `1864706`–`1864709`, including 456 solves and zero remaining tracked GPU
  communicators. The administrator then replaced the public module; jobs
  `1865327`–`1865330` verified it on all four partitions, and GCC 14.3/CMake
  probes `1865334`–`1865337` passed with the repaired lock. Fresh release
  workflow [39293908513](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39293908513)
  completed all four dependency and application compilations with recipe
  `38dfba7e3ed7`. Application jobs `1865877` (16V100), `1865642` (4V100),
  `1866021` (8V100V0) and `1865863` (DSPRHBM) installed the binaries and native
  metadata successfully, but MPI stdin forwarding consumed the shell's pending
  export commands. No SIF was produced and GitHub correctly rejected the
  incomplete deliveries. The shell wrapper now reads its entire command source
  before starting children and isolates their stdin. Recovery preserves the
  frozen recipe and original overlays, clones their compiled contents, and runs
  metadata/export/verification followed by science. All four recovered SIFs now
  pass the artifact checks. Shared-ELPA GPU science exposed large-matrix ELPA2
  nonconvergence and a small distributed-matrix CUDA host-registration failure;
  the same inputs converge with cuSOLVERMp, ELPA CPU kernels and ELPA1 GPU.
  DSPRHBM passed all 14 scientific calculations and numerical acceptance.
  Isolated ELPA builds using upstream host-staged back-transformations preserve
  GPU computation and passed standalone numerical, Fortran and lifecycle checks.
  All 42 unchanged CP2K calculations then passed on 4V100 (`1868636`), 16V100
  (`1868645`) and 8V100V0 (`1868647`), with independent numerical revalidation.
  The 39.8 MiB [NVIDIA-only replacement package](../deployment/elpa-2026.02.001-backtransform/README.md)
  was installed by the administrator. Public-module native jobs `1869223`–`1869226`
  then passed on all four partitions: 460 numerical solves, zero remaining GPU
  communicators and successful GCC 14.3 Fortran probes. The CP2K dependency lock
  now pins the new GPU libraries and back-transformation patch. The old GPU
  deliveries remain failed; the fresh identity has now passed its own science.
  [Physical ELPA acceptance](evidence/elpa-backtransform-native-20261020/README.md).
  Fresh [workflow 39365139034](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39365139034)
  passed 840 tests (838 passed, two skipped) and submitted recipe `aed7aa53ff43`:
  dependency jobs `1869397` (16V100), `1869396` (4V100), `1869360` (8V100V0)
  and `1869407` (DSPRHBM) all completed, followed by application jobs `1870431`,
  `1869890`, `1870216` and `1870183`. Science jobs `1870898`, `1870420`,
  `1871559` and `1870992` passed all 56 calculations. All four deliveries have
  verified native exports. Its frozen watcher then submitted the 216-water
  comparisons; all three GPU partitions completed and independently passed.
  [New release acceptance and first tuning results](evidence/cp2k-shared-elpa-release-20261020/README.md).
  The [shared-ELPA administrator deployment package](../deployment/cp2k-2026.2-shared-elpa/README.md)
  is now prepared in the cluster home: 1.85 GiB of selected runtime files, all
  four source preflights passed, with `saiuser01` read/traverse access verified.
  It preserves exact build prefixes and switches the public module only after
  all four copied trees pass verification. The old installation/module are retained.
  The administrator subsequently installed it; `stardust` verified all physical
  files and completed 56/56 native calculations across the four partitions.
  [Handoff evidence](evidence/cp2k-shared-elpa-deployment-20261020/README.md) and
  [completed physical acceptance](evidence/cp2k-shared-elpa-native-20261020/README.md).
  The final integration
  archive contains 2,156 independently hash-verified files. Linux CI at
  `0e0ed1c` passed 840 tests (838 passed, two optional skips).
  [Diagnosis and export recovery](evidence/cp2k-export-recovery-20261020/README.md).
  [Lock migration and rebuild](evidence/cp2k-commfix-20261020/README.md).
  [Repair and deployment evidence](evidence/elpa-commfix-20261019/README.md),
  [simplified module and repaired physical-library acceptance](evidence/elpa-simple-module-20261020/README.md).
  The old development job `1861248`, frozen at controller `a7dd890`, failed
  CMake because that recipe omitted wignernj. The current recipe contains
  the pinned wignernj build; development still has no newly accepted artifact.

- Daily and child workflows await reusable jobs and reject unexpected skipped,
  failed or cancelled deliveries. A recorded unchanged release is an intentional
  skip; development must produce a fresh run on every trigger. Explicit retry
  inputs propagate through reusable workflows. Schema-1 history is retained.
- Long jobs are separated into build and acceptance phases. CP2K additionally
  has a dependency phase. CPU and GPU compilation can run independently. CP2K now
  validates each candidate independently on one and two nodes; ABACUS retains
  its existing reference policy. Handoffs validate workflow/source/track/target and the original
  immutable controller identity, without uploading or claiming a second build.
- Re-entering CP2K application or any software's acceptance stage validates and
  resumes the existing Slurm handle. Completed MD evidence is reverified without
  rewriting receipts pinned by a performance repeat. An ambiguous submission
  with no recorded job handle fails closed. This does not automatically recover
  the initial dependency/build phase across new workflow attempts; unchanged
  release attempts still require explicit retry authorization.
- ABACUS supports the upstream JSON option change and renamed HSE fixture,
  validates packaged scientific inputs before compiling, and includes all
  required native library paths.
- CP2K dependencies are built on the target partition, sealed with exact
  identity, job/script, graph, installation receipt and overlay hashes, then
  cloned and checked for application compilation. The original overlay remains
  intact. GPU dependency wall time is capped at the observed 240-minute QOS;
  historical 158–167 minute successes are references, not proof for this recipe.
  The required wignernj archive is pinned and checked. All four partition source
  caches passed read-only verification on SAI.
- MD connects two-node source-bound TF/PT/JAX science, explicitly scoped raw performance checks,
  one repeat after a slowdown greater than 10%, paired export readback and
  immutable publication/module generation. PT requires actual Nsight CUDA
  kernel evidence. New GPU deliveries benchmark PT only; TF/JAX CPU timing
  remains available in the historical all-backend scope, with all-backend
  numerical correctness retained in either scope. Published manifests name
  the accepted performance scope and backends.
  Runtime checks bind actual SIF bytes to the paired build identity.
- GPUMD adds frozen-model GNEP numerical forces, stale-output rejection,
  calibrated engine intervals and alternating baseline/candidate trials.
  Scientific and performance verdicts are recomputed from retained raw files.
- Publication requires all scientific gates. Software with a historical
  comparison policy blocks a repeated performance regression greater than 10%;
  CP2K now uses candidate-only correctness checks and descriptive timings. Repeat records bind original evidence, artifact,
  baseline, inputs and resources; original logs remain available.
- Every accepted single or paired SIF must export its exact prefixes and pass
  independent readback of all bytes, permissions, links, identities and native
  modules before publication. Cache validation rechecks the exported payload.
  Schema-1 `/opt/software` exports are never relabelled as schema-2 `/opt/apps`.
- Per the user's October 17 instruction, shared disk-growth reservations and
  submission/allocation free-space gates are removed. Filesystem limits are
  enforced by actual writes, with failures retained. Builds, caches, overlays
  and temporary files stay in home; host `/tmp` is not used for compilation.
  CP2K keeps target/recipe-scoped Slurm singletons and exclusive ownership of each
  run; other software retains its shared singleton.
  Repacking copies and checksum-checks a checkpoint instead
  of moving the original. Monitors preserve jobs when observation expires.
- Controller staging batches the trusted files into one SFTP connection before
  fingerprinting/submission. Each run still has its own immutable controller;
  application/acceptance handoffs do not reupload it.

## Verified on SAI

Evidence: [workflow-closure-20261015.json](evidence/workflow-closure-20261015.json) and
[GPUMD 16V100 proof](evidence/gpumd-16v100-closure-20261015.json).

- Native export tool fixture: real SIF roundtrip passed for 17 software/partition
  combinations and 102 retained files. This is explicitly a synthetic fixture,
  not scientific software acceptance; no container code or system `/opt` write
  was involved.
- Real Lmod probe job `1790564`: `COMPLETED`, exit `0:0`, 12 seconds. Module load,
  unload, partition selection and subsequent image/launcher switch all passed.
- Initial read-only capacity/reference audit: about 16.05 GiB actually available. Of 17
  proposed old failed routine overlays, one was found referenced by CP2K recovery
  records and protected. The remaining 16 could reclaim 141.92 GiB, subject to
  explicit user approval and immediate file/job revalidation. The latest complete
  audit at 23:25 Asia/Shanghai scanned 17,782 metadata/script files without read
  errors and revalidated all 16 unchanged failed overlays with no new references.
  Exact review list: `/tmp/sai-overlay-cleanup-ready-20261015.md`. Nothing deleted.
- GPUMD 16V100 build `1790933` completed `0:0` in 20:07; acceptance `1792098`
  completed `0:0` in 2:33. All scientific/feature checks and the actual launcher
  passed. Median MD throughput was 2,396,010 versus baseline 2,408,080 atom-steps/s
  (about 0.50% slower), within the 10% gate; no repeat was needed. The exact SIF
  was exported, read back and published. A subsequent read-only verification of
  the published artifact, raw proofs, export bytes and module records passed.
- Additional module job `1803862` ran the published SIF module successfully:
  energy and forces matched its gold fixture exactly. The next exported-native
  step failed because unloading the SIF module also unloaded Apptainer. This is
  a probe sequencing defect; the corrected local probe reloads Apptainer and
  retains the failed attempt. Exported-native execution/JIT is still unproven.
- At 23:02 Asia/Shanghai, project capacity was **0 available bytes**. The normal
  13 GiB GPUMD submission check rejected 4V100 before any upload or allocation.
  Legacy branch job `1798309` subsequently timed out; it is not a closure canary
  and its overlay/logs are retained. Its old workflow can still start subsequent
  stages. This is historical evidence of the old capacity policy, superseded
  by the user's October 17 instruction to run without estimated space gates.

## Remaining acceptance work

CP2K 2026.2 has four SIFs from the original corrected installation recipe:
4V100 `1837815`, 16V100 `1838194`, 8V100V0 `1838191`, and DSPRHBM retry
`1838412` all completed `0:0`. They remain build-only artifacts with the
original system ELPA linkage, not published multi-partition releases. The
original GitHub workflow `39078998459` ended with failure and is retained.
[Artifact/diagnostic status](CP2K-VALIDATION.md),
[installation repair and original identities](evidence/cp2k-install-layout-20261018/README.md).

Fresh private-ELPA recipe workflow `39094062144` uses identity
`v2026.2-g67b5da876dd6-r4d454c740309`. All four dependency jobs completed and
sealed. By 14:21 Asia/Shanghai, 4V100 application `1839644` had completed `0:0`
in 38:30 and science `1840021` passed all fourteen single/two-node cases in
7:59. The workflow exported and read back the complete runtime delivery and
published it. A separate read-only call to the frozen publication validator
rechecked the image, science, export bytes and module successfully. This is
the first accepted private-ELPA release delivery; physical `/opt` installation
and native scientific execution are not claimed by export integrity checks.
16V100 application `1839900` completed `0:0` in 24:52 and science `1840585`
completed `0:0` in 13:30. All fourteen single/two-node calculations passed;
the workflow read back 1,126 native-export files and published the delivery.
A separate full frozen-controller publication revalidation passed. Maximum
energy/force differences across node counts were `4.77e-12 Ha` and
`1.10e-11 Ha/Bohr`.
8V100V0 application `1839913` completed `0:0` at 14:16:27 in 1:22:48 and
produced a 188,928,000-byte SIF with successful final version/loader/feature
checks and an independently matched SHA256. About 56 minutes went into clone
preparation and 27 minutes into application setup/build/packaging/checks.
Science job `1841482` completed `0:0` in 15:24 on two 8V100V0 nodes. All
fourteen calculations passed; maximum energy/force differences across node
counts were `2.14e-11 Ha` and `1.30e-11 Ha/Bohr`. Native export readback verified
1,126 files and the delivery was published. Independent full frozen-controller
publication validation passed. The clone receipt confirms the sealed source
is unchanged.
DSPRHBM application `1839750` failed mounting missing `/opt/mellanox/hcoll`.
The new fix maps the shared HPCX copy to MPI's expected absolute path, and
CPU MPI/libtool preflight `1841002` passed `0:0`. Fresh CPU workflow
`39101032802`, frozen at `33e80c8`, uses new recipe `fb4adb0598fc`.
Its dependency job `1841174` completed `0:0` in 1:08:26 on `d9470n02` at
14:53:38. The workflow automatically handed off to application job `1841857`,
which completed `0:0` in 43:12 at 15:40:22. Clone preparation occupied about
26:32; application setup/build/packaging/checks occupied the remaining 16:40.
Science `1842261` completed `0:0` in 18:10 at 16:02:15 on `d9470n02,04`.
All fourteen calculations passed, including small/large private-ELPA execution;
maximum single/two-node energy and force differences were `4.32e-12 Ha` and
`1.40e-11 Ha/Bohr`. The workflow read back 1,125 native runtime files and
published the delivery. Independent full frozen-controller publication
validation passed. All four release partitions now have accepted deliveries;
the CPU recovery and GPU deliveries retain different recipe/build-directory
identities, with the same CP2K source. Preserve their exact absolute prefixes.
The CPU dependencies had already succeeded. Repeating them followed from the
overly broad recipe identity: a mount-policy repair changes the prefix and
invalidates exact checkpoint reuse, although dependency sources/compilers did
not change. Future dependency reuse should separate dependency compatibility
from runtime/container policy with explicit provenance and preserved absolute
paths. This CPU recovery completed without another restart; eight GCC 14.3.0
Libint workers were observed at 14:19.
[Initial submissions](evidence/cp2k-install-layout-20261018/native-elpa-formal-start.json),
[actual handoffs](evidence/cp2k-install-layout-20261018/native-elpa-application-handoff.json),
[first publication and independent revalidation](evidence/cp2k-install-layout-20261018/native-elpa-first-publication.json),
[second publication and independent revalidation](evidence/cp2k-install-layout-20261018/native-elpa-second-publication.json),
[CPU HCOLL preflight](evidence/cp2k-install-layout-20261018/hcoll-cpu-preflight.json),
[fresh CPU submission and accounting](evidence/cp2k-install-layout-20261018/hcoll-cpu-recovery-start.json),
[completed CPU dependencies and application handoff](evidence/cp2k-install-layout-20261018/dsprhbm-native-elpa-application-start.json),
[clone receipt and CPU/GPU progress](evidence/cp2k-install-layout-20261018/native-elpa-remaining-progress.json),
[14:06 compile progress](evidence/cp2k-install-layout-20261018/native-elpa-progress-1406.json).
[8V100V0 SIF, timings, science submission and CPU failure explanation](evidence/cp2k-install-layout-20261018/native-elpa-8v100v0-artifact.json).
[8V100V0 accepted publication and independent validation](evidence/cp2k-install-layout-20261018/native-elpa-third-publication.json).
[DSPRHBM accepted publication and independent validation](evidence/cp2k-install-layout-20261018/native-elpa-fourth-publication.json).

The legacy HCOLL alias is now a compilation-only compatibility bind. Site MPI
needs HCOLL at runtime, and generated native modules already record its real
shared HPCX path. Existing accepted private ELPA libraries contain an inherited
legacy RUNPATH fallback. Native-export job `1841692` ran the actual 16V100
export at the canonical prefix with the alias absent: version, loader, PBE OT,
ordinary diagonalization and forced ELPA all passed. Actual loader traces
confirmed the real HPCX HCOLL/OCOMS paths, with energy/force agreement against
the accepted SIF. Follow-up `1841817` passed the same three cases on two native
MPI nodes, with alias absence and actual library paths checked independently
on each rank. No application SIF was executed or physical `/opt` changed.
New builds remove the private-ELPA fallback before hashes/manifests are written;
export, final SIF checks and launch omit the alias, and runtime policy rejects
it in ELF paths and native entries. Existing frozen controllers and accepted
artifacts are preserved. Physical installation acceptance remains separate.
Full Linux CI at `dc08073` passed 782 tests (780 passed, two optional skips),
including real ELF loading after removing the alias and standalone installed
verifier execution. This does not reassign new policy to frozen artifacts.
[Native execution](evidence/cp2k-install-layout-20261018/hcoll-native-alias-free.json),
[two-node native execution](evidence/cp2k-install-layout-20261018/hcoll-native-multinode-alias-free.json),
[real ELPA copy normalization](evidence/cp2k-install-layout-20261018/real-elpa-normalization.json),
[Linux CI proof](evidence/cp2k-install-layout-20261018/hcoll-github-validation.json),
[native deployment and shared dependencies](CP2K-NATIVE-MODULE.md).

The original workflow is not fully successful. Its GPU result jobs also failed
at startup because reusable workflows incorrectly required caller-scope SSH
secrets while this deployment supplies them in the `hpc` Environment. Both
reusable delivery workflows now allow environment-only credentials, and their
execution steps fail before preparing a key if either resolved credential is
missing. The credential-free result jobs need no environment. Twenty-one
focused workflow/resolver tests pass locally; Linux CI at `6cbe030` passed
777 tests (775 passed, two optional skips). Live workflow `39102356436` passed
both reusable and parent result jobs with environment-only credentials and an
intentional release skip. No extra Slurm build was submitted. This validates
the repaired result path, without claiming another accepted delivery.
Original frozen runs retain their failed states and accepted
artifact proofs remain independently valid.
[GitHub annotations and secret-scope diagnosis](evidence/cp2k-install-layout-20261018/github-environment-secret-result.json).

CPU recovery workflow `39101032802` also ended with a failed GitHub conclusion
at 16:16:37 after successful dependency, application and acceptance jobs.
Its frozen `33e80c8` reusable `result` job retained the old required
caller-scope SSH secrets and failed before running. Current code already
contains the `6cbe030` repair and its real skip/result validation above. The
original failed workflow is preserved alongside the independently revalidated
accepted CPU delivery.
[CPU workflow job states and annotations](evidence/cp2k-install-layout-20261018/dsprhbm-workflow-result.json).

Accepted CP2K 2026.2 deliveries are available on all four partitions.
The system ELPA library's generic allocation code was independently shown to
execute AVX512 on an AVX2-only node, despite advertising both kernel sets.
The new recipe therefore builds CP2K's own pinned ELPA 2026.02.002 under
`<CP2K prefix>/dependencies/elpa`, with GCC 14.3.0, native partition flags and
V100 kernels. It includes the upstream communicator/GPU-setup repairs and
exports the dependency with the software directory.
[Independent ISA evidence](evidence/cp2k-install-layout-20261018/system-elpa-isa-probe.json).

A separate shared ELPA 2026.02.001 delivery now preserves the site's GNU/CUDA
configuration while splitting AVX2 and AVX512 into distinct final `/opt/devtools`
prefixes. Both SIFs passed 48 numerical solves plus C/Fortran consumer and actual
module checks. The administrator has installed the exact trees and public module;
physical jobs `1847651`–`1847653` passed 144 CPU/GPU solves across all three V100
partitions, including actual loader evidence and fresh C/Fortran consumers.
DSPRHBM passed 24 CPU solves through the host-array C API (`1847682`), while the
explicit pointer-detecting C API failed without a CUDA driver (`1847654`).
This interface limitation initially prevented unconditional pure-CPU acceptance.
On October 19, CPU-only build `1848059` completed on DSPRHBM and passed all 24
original explicit-C-interface solves. The shared recipe disables GPU support at
compile time for `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu`, removing
CUDA dependencies and pointer dispatch. Four new partition-module checks
`1848160`–`1848163` passed, retaining the accepted V100 libraries and selecting the
new CPU library for DSPRHBM. The 12.81 MiB supplement passed deployment preflight.
[CPU supplement and partition selector evidence](evidence/elpa-cpu-20261019/README.md).
The administrator package now upgrades the existing `elpa/2026.02.001-2603-gnu-auto`
name in place. Jobs `1861785`–`1861788` passed the same four-partition selections,
fresh numerical consumers and loader checks; V100 checks also unloaded a shell's
previous auto module after replacement. The installer first installs the CPU
prefix, then backs up the known old module and replaces it atomically. It refuses
unknown existing module content. The old `-partition` candidate is not installed.
[Auto module upgrade evidence](evidence/elpa-auto-20261019/README.md).
The administrator subsequently installed the CPU prefix and replaced the auto
module. Physical jobs `1861947`–`1861950`, run by `stardust`, all completed `0:0`
and passed 168 solves (48 per V100 partition; 24 on DSPRHBM) through the original
explicit C APIs. Fresh Fortran consumers, installed inventories, public module
load/unload and both-rank ELPA/MPI/BLAS/real-HCOLL traces also passed. The CPU
library contains no CUDA linkage or pointer-dispatch symbols. Independent
revalidation pins all numerical cases and hashes 205 raw files; maximum spectrum,
residual and orthogonality errors were `2.22e-15`, `6.46e-16` and `1.665e-15`.
The shared ELPA physical deployment is accepted on all four partitions for these
small single-node correctness cases. Large-system performance and multi-node
scaling remain outside this evidence.
[Physical auto module acceptance](evidence/elpa-auto-native-20261019/README.md).
This does not replace CP2K's accepted private ELPA dependency.
[Shared ELPA build, compatibility fixes and deployment evidence](evidence/elpa-site-20261018/README.md).

Private-ELPA pilot `1838654` built successfully and passed small OT, ordinary
diagonalization, DFT-D4 and TBLITE. Its small diagonalization did not pass the
ELPA dispatch threshold, so actual ELPA remains a separate required test.
Diagnostic `1838989` found root-only pilot-overlay permissions and loaded the
old system library. A separately exported, readable copy passed all eight calculations in
`1839059` (`COMPLETED 0:0`, 16:08), including small/large ELPA on one and two
nodes. Each rank proved private linkage; ELPA recorded GPU kernel selection
and positive solver calls. Original artifacts and failed evidence remain
unchanged. [Diagnostic results and raw evidence](evidence/cp2k-install-layout-20261018/native-elpa-16v100-multinode.json).

The user has retired the old CP2K 2026.1 comparison. Fresh acceptance uses
`cp2k_acceptance.py`: one/two-node candidate consistency, OT and ordinary/forced
ELPA agreement, DFT-D4, TBLITE and larger PBE water workloads. Actual solver
calls, rank placement, inputs and artifact identity are verified. Timings are
reported without claiming a baseline speed ratio. Full-matrix scientific acceptance,
artifacts, export readback and publication remain incomplete. The new
HCOLL repair passed 774 Linux CI tests (772 passed, two optional skips) at
`33e80c8`. Physical deployment can use the packaged native partition selector
under one site module name; [deployment assessment](CP2K-NATIVE-MODULE.md).

Recovery job `1837454` completed `0:0` in 18:52, producing a verified
187,449,344-byte diagnostic SIF. Installation and final SIF verification passed:
CP2K 2026.2, GCC 14.3.0, all required features, 47 ELF files and no missing
dependencies. PBE OT executed directly from the SIF, converged in 46 SCF
steps and reported actual GPU grid work. The recovery reused 1,486 of 1,487
compiled objects with unchanged hashes/mtimes, and preserved the original
failed image's full SHA256. The diagnostic retains its old source identity,
has no formal artifact/publication record and does not substitute for the new
workflow. Its fixture data and single-GPU smoke do not establish comparative
performance or multi-GPU acceptance.
[SIF path, checksum and runtime proof](evidence/cp2k-install-layout-20261018/recovery-sif.json).
Read-only SIF inspection also confirmed installed data and its configured
lookup directory. The later formal-SIF smoke verified its own installed data
and dynamic lookup, as recorded above.
[Earlier diagnostic installed-data checks](evidence/cp2k-install-layout-20261018/installed-data.json).

Actual PBE smoke exposed a CUDA runtime compatibility issue after successful
compile/version/ldd checks: host driver 550.163.01 could not JIT CUDA 12.9 PTX.
Using the existing SDK CUDA 12.9 compatibility libraries, diagnostic `1834460`
completed both PBE OT and STANDARD diagonalization (`0:0`, 31 seconds), with
energy difference `5.684e-14 Ha` and maximum force difference `2.710e-10 Ha/Bohr`.
Production commit `9352d6d` records compatibility library priority and requires it
in final GPU linkage. Fresh four-target release workflow
[39056762371](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39056762371)
uses recipe `4b6cb146fa983f69d82e5f7a990eba00b879a1c1e7464e317cc4740dbb41793b`.
The later installation failures described above prevented formal delivery.
Frozen workflow `39054631316` also lacks this runtime correction and cannot
establish acceptance of the corrected recipe.
[PBE attempts and actual loaded libraries](evidence/cp2k-hdf5-static-20261018/pbe-smoke.json).

The pinned 2026.1 baseline also passed PBE OT in its default module environment
(`1834594`, `COMPLETED 0:0`, 16 seconds). Its energy differs from the corrected
candidate diagnostic by `8.53e-13 Ha`; maximum force difference is `1.00e-12 Ha/Bohr`.
Actual baseline process maps show the host 550.163.01 driver and no dynamic
NVRTC in the sampled process. No baseline environment change is required by this
test. These separate small diagnostics establish numerical consistency only.
[Baseline results and retained setup failures](evidence/cp2k-hdf5-static-20261018/baseline-pbe-smoke.json).
The production commit passed 732 CI tests: 730 passes and two optional skips.

The repository's 64-water (192-atom, 1,472 orbital functions) diagnostic also
passed OT and ordinary diagonalization (`1834681`, `COMPLETED 0:0`). Retained
timings prove 112 and 24 calls through `cp_fm_diag_cusolver`, respectively;
the pinned source binds that path to `cusolverMpSyevd`. Energy and maximum force
differences were `7.73e-12 Ha` and `1.54e-9 Ha/Bohr`. Actual loaded libraries,
GPU grid/DBCSR timings and unchanged full binary/image hashes were checked.
This is single-node diagnostic execution, with no baseline performance verdict
and no formal SIF claim. [Raw results](evidence/cp2k-hdf5-static-20261018/water64-smoke.json).

At 02:12 Asia/Shanghai on October 18, the corrected workflow had these native
dependency jobs. Their frozen controller, source, recipe, Slurm scripts and
home-backed temporary storage were verified against the requested identity.
[Submission evidence](evidence/cp2k-hdf5-static-20261018/compat-formal-start.json).

| Target | Dependency job | Observed state |
|---|---|---|
| 16V100 | 1834668 | RUNNING on 16v100n05 |
| 4V100 | 1834662 | RUNNING on 4v100n10 |
| 8V100V0 | 1834667 | PENDING |
| DSPRHBM | 1834666 | RUNNING on d9470n02 |

The full compile diagnostic found that v2026.2's TBLITE API is incompatible
with the hard-coded development dependency. The repair selects TBLITE from
the pinned CP2K source and uses release's official 0.6.0 bundle plus both
gradient fixes. Diagnostic `1833642` compiled all source objects, then failed
at final executable linking because FindHDF5 omitted the static Fortran C stub.
The next recipe selects native HDF5 static targets and explicitly discovers
its external ZLIB provider. Diagnostic `1833977` completed `0:0` in 15:51 on 4V100: full compile,
version, ldd and all required feature flags passed; the old image hash was
unchanged. Fresh four-target release workflow `39054631316` now uses controller
`9f17f71` and recipe `e139a320651ce69c11cdbd1763ae875f98992de261a465e9e19a24849086bf40`.
Final SIF and scientific/performance acceptance are still pending. [HDF5 repair](evidence/cp2k-hdf5-static-20261018/README.md).
Existing workflow `39047391019` keeps its original frozen recipe and is not
evidence for these corrections.
[TBLITE source compatibility](evidence/cp2k-tblite-release-20261018/README.md).

At 00:03 Asia/Shanghai on October 18, fresh workflow
[39047391019](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39047391019)
had submitted corrected CP2K release dependencies on all four targets:
16V100 `1833131`, 4V100 `1833135`, 8V100V0 `1833125`, DSPRHBM `1833124`.
Commit `e8c0969` adds COSMA's required ScaLAPACK variant, after the previous
4V100 and 16V100 applications failed on missing pxgemm CMake targets.
The previous four dependency allocations succeeded, but none established
application acceptance. The new commit passed 722 CI tests with two optional
skips. [Root cause and evidence](evidence/cp2k-cosma-scalapack-20261018/README.md).
The first verified v2026.2 SIF remains the immediate goal; direct-generalized
tuning stays in issue #9 and does not block the build.
At 00:13, all four targets had installed the corrected COSMA/COSTA libraries.
Diagnostic `1833202` completed the full CP2K configure with unchanged source
image hashes; compile diagnostic `1833253` subsequently failed at TBLITE's API.
The remaining two old applications were deliberately cancelled after checking
their missing interfaces, with all historical files retained.

At 23:27 Asia/Shanghai, fresh 4V100 dependency job `1832262` completed `0:0`
in 38:43 and handed its sealed image to application job `1832733`, running
on `4v100n06`. The other three dependency jobs remained active. The priority
is the first verified v2026.2 SIF and its existing acceptance gates.
[CPU/accounting evidence](evidence/cp2k-release-followup-20261017/README.md)
records actual logical CPUs used as well as allocation-normalized utilization.
The user confirmed PBE OT and diagonalization as the primary runtime scope;
optional direct-generalized tuning is deferred to
[issue #9](https://github.com/AI4SAI/sai-hpc-software/issues/9).

Fresh release workflow [39041714911](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39041714911)
uses controller `2c7fd3e097cf61c0d0ee6612cb2cc96eebb96677`, recipe
`a280393e353fee8faa1c09209b34c42eb0f55d0a44ad0543bed1ed57101735ec`, and
v2026.2 source `67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`. It selects all four
targets, with SpLA GEMM offload ON for the three V100 partitions and OFF for
DSPRHBM. Validation passed 721 tests (719 passed, two optional skips).
Real dependency-config probe `1832078` resolved COSMA/COSTA/tiled-mm and SPLA
with GCC 14.3.0 on 4V100; complete before/after image hashes matched.
[Configuration and CI evidence](evidence/cp2k-spla-offload-20261017/README.md).
These are prerequisite checks; actual dependency/application and
science/export/publication outcomes must still complete under this fresh identity.

At 22:45:52 Asia/Shanghai, actual Slurm submissions were:

| Target | Dependency job | Observed state |
|---|---|---|
| 16V100 | 1832260 | RUNNING on 16v100n81; native profile ready, dependency concretization started |
| 4V100 | 1832262 | RUNNING on 4v100n13; preparing home-backed image |
| 8V100V0 | 1832264 | PENDING; scheduler priority |
| DSPRHBM | 1832265 | RUNNING on d9470n02; preparing home-backed image |

[Verified requests, script hashes, accounting and log tails](evidence/cp2k-spla-offload-20261017/fresh-release-start.json)
bind these jobs to the new controller and recipe. All compilation, caches and
temporary files remain under project home. No application SIF or accepted
multi-partition release is claimed at this submission snapshot.

Latest follow-up, 22:16 Asia/Shanghai on October 17: all four private-driver
dependency jobs completed `0:0`, but all four application jobs failed. 4V100
`1831311` and 16V100 `1831609` hit CMake cleanup `EISDIR`; DSPRHBM `1831428`
could not resolve COSMA's installed COSTA dependency; 8V100V0 `1831584` failed
installing the toml-f license directory and ended with a driver cleanup timeout.
The latter's exact cause remains unconfirmed. There is no new accepted SIF.
[Accounting and retained logs](evidence/cp2k-spla-offload-20261017/production-container-fixes.json).

The GPU recipe now enables SpLA GEMM offload, explicitly disables it on CPU,
and requires matching CMake settings plus GPU version capability. The user
confirmed large-system usage. A real V100 dependency probe passed numerical
checks; 1024/2048-square DGEMMs were 1.32x/2.48x faster than four CPU threads,
including transfers. This is not CP2K whole-application performance acceptance.
Commit `f35bf85` also fixes transitive CMake dependency lookup. Its GitHub CI
passed 710 tests with two optional skips; 26 focused tests passed on SAI.
[Results and validation](evidence/cp2k-spla-offload-20261017/README.md).

Read-only mount qualification exposed a changed 4V100 dependency image hash.
The original seal is preserved and must fail verification; that image is now
a diagnostic source only. A repaired private driver opens the backing file
`O_RDONLY`; job `1831878` passed all twelve filesystem, explicit-sync,
read-only hash and reader/writer-lock cases. The new binary is checksum-pinned
in the recipe and installed read-only in the home cache.
[Driver qualification](evidence/fuse2fs-readonly-20261017/README.md).

The CMake cleanup failure mechanism was reproduced independently in job
`1831861`: clean overlays passed 8192 deletions; old numeric work directories
caused `renameat` to return `EISDIR`. This establishes a fuse-overlayfs work-name
collision, without proving every old application error has the same cause.
[Reproducer and retained failures](evidence/cp2k-overlay-workdir-20261017/README.md).

The new application-copy preparation is now integrated. It verifies the sealed
source and exact copy while holding OFD locks, removes only reviewed temporary
work entries, rejects unexpected contents or hard links, and retains the derived
hash and read-only filesystem checks. Job `1832137` passed on a new 256 MiB
fixture: 8193 entries removed, 8192 file operations without error, three fsck
checks passed, source hash and upper payload sentinel unchanged. Sixteen
preparation/seal tests and 37 driver/lifecycle/recovery tests also passed.
This fixture establishes the repair behavior; the next real CP2K build remains
required. [Repair evidence](evidence/cp2k-overlay-workdir-20261017/fix-proof-result.json).

Frozen controllers and dependency seals retain their original identities.
Fresh compilation and science/export/publication gates remain required.

Earlier follow-up, 21:16 Asia/Shanghai on October 17: 4V100's dependency image
is sealed and application-stage job `1831311` is RUNNING on `4v100n05`
(started 21:09:11), still in image preparation. The other three new dependency
jobs remain RUNNING and no new SIF exists.
[Handoff and seal evidence](evidence/cp2k-production-handoff-20261017.json).

At 21:05 Asia/Shanghai on October 17, private-driver 4V100
dependency job `1830994` completed `0:0` in 37:19. 16V100 `1830993`,
8V100V0 `1830995` and DSPRHBM `1830997` remained in Libint compilation;
HDF5/Cython/PLUMED were complete on all four. The 4V100 seal and all new
application SIFs were absent at the snapshot. The historical DSPRHBM dependency
job `1828946` completed in 3:55:16 and handed off to application job `1831223`,
which was preparing its original image. [Production follow-up and CPU samples](evidence/cp2k-production-followup-20261017.json).

[Review of pinned CP2K/DBCSR sources and older installations](CP2K-V100-TUNING.md)
found that SpLA was installed as a dependency but not enabled in the CP2K
application. Commit `6d9b709` restores the old 2026.1 configuration and checks
the application CMake cache. Its 16 contract tests and 710-test CI passed
(708 passed, two optional skips). Running frozen controllers retain the
configuration difference. Actual SpLA calls also require GEMM offloading,
which the installed baseline disables, so this is not established loss of a
baseline runtime capability. Existing builds and acceptance continue; real
build validation of the corrected configuration requires a fresh recipe. The
review also records runtime tuning priorities and the recommendation for an
optional shared `/opt/devtools` image driver; no system installation occurred.

The October 17 CPU-efficiency investigation found whole-image `fsync` under
the `fuse2fs 1.47.3` global lock, despite correct `-j8`/`-j12` settings and no
CPU throttling. A pinned upstream fix plus an Apptainer/Lustre OFD-lock adapter
passed isolated DSPRHBM `1830438` and 16V100 `1830468` probes. Two-trial median
32-file compilation improved 27.89x and 20.20x; explicit synchronization,
filesystem checks, 161-file clean-remount verification, and competing-writer
rejection passed. The pinned driver is now part of the CP2K build recipe and
CI deployment contract; existing jobs retain their original drivers/controllers.
Fresh four-target release workflow
[39032306213](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39032306213)
passed validation and submitted dependency jobs `1830993` (16V100), `1830994`
(4V100), `1830995` (8V100V0), and `1830997` (DSPRHBM). All four were RUNNING
at 20:28:46. At 20:30:38, actual 16V100 compilation used 7.144/8 logical CPUs
(89.30%) over ten seconds, with the private driver observed in `/proc`; the
old same-node job used 0.492/8 CPUs (6.15%) over the concurrent sample.
The stages differ, so this is utilization evidence rather than whole-build
speedup or CP2K application acceptance. CI passed 709 tests (707 plus two
optional skips); 20 focused tests passed on SAI. Next: dependencies must seal,
then the existing workflow compiles CP2K, verifies its final SIF, runs science
and performance, exports the native payload and publishes only after those gates.
At 20:36:23 all four new jobs were still RUNNING, each with GCC/G++/GFortran
14.3.0 and the pinned driver recorded. All four HDF5 builds had completed in
1:50–3:27; 16V100 and 4V100 had reached PLUMED compilation, with Libint still
building. No dependency seal or application artifact existed yet.
[Production execution evidence](evidence/cp2k-private-driver-production-20261017.json).
[Efficiency report, patch and retained evidence](BUILD-IO-EFFICIENCY.md).

Earlier official `v2026.2` four-partition attempt:
workflow [39018993951](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39018993951).
At 18:06 Asia/Shanghai, 4V100 `1828944` and DSPRHBM `1828946` were compiling
dependencies. 16V100 `1828943` was cancelled at 18:05:44 after an operator-added
read-only `srun` audit requested one CPU and triggered SAI's eight-logical-CPUs
per GPU rule. This was an agent operational error, not a CP2K compiler result.
The failed audit's output, old overlay and build log remain preserved. The
replacement uses the normal native dependency allocation; do not add a step
with a CPU/GPU request that violates the site's rule.

The home-storage audit observed `TMPDIR=/workspace/tmp`, a FUSE overlay root,
and an open `work.ext3` under `~/sai-hpc-software/runs/`; there was no separate
host `/tmp` or `/var/tmp` mount. The current controller removes both free-space
gates and reservation waiting, while retaining run locks and real write errors.
Its complete SAI suite passed 703 tests (701 passed, two optional MD-cache skips),
with test temporary files also in home. Running old controllers are not rewritten.

Fresh retry workflow [39023790564](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39023790564)
uses controller `975f8f8` for release `v2026.2` on 16V100 and 8V100V0.
At 18:15, actual jobs `1829656` (16V100, `16v100n04`, start 18:13:53) and
`1829644` (8V100V0, `8v100v0n09`, start 18:13:01, two GPUs/twelve CPUs) were
RUNNING and recorded `capacity_policy=filesystem-enforced`. 16V100 was
concretizing dependencies after its GCC 14 profile/bootstrap; 8V100V0 was
preparing the overlay/container. Neither is an application-build success.
[Home-storage policy and audit evidence](evidence/cp2k-home-storage-policy-20261017.json).

Earlier, at 17:51 Asia/Shanghai on October 17, new 16V100 dependency job `1828943`
was RUNNING for 51:44 on `16v100n37` (start 16:59:26), with its disk reservation
and all three GCC 14.3.0 compiler observations. DSPRHBM `1828946` was also
RUNNING for 51:19 with those checks. Both were compiling dependencies, not
the CP2K application. 8V100V0 `1828945` failed `1:0` at 17:29:59 after waiting
30 minutes for disk reservation; compilation never started. 4V100 `1828944`
was allocated at 17:45:59 but was also waiting for disk reservation. The initial Actions
staging delay was per-file SSH overhead, not a Slurm resource wait. Subsequent
controller uploads are batched into one connection.

The prior GCC 14 attempt completed dependency stages on 16V100, 4V100 and
DSPRHBM; 8V100V0 hit its four-hour limit in Libint. All three application jobs
then failed on a false HDF5 check (`ON` versus the expected `yes`). A separate
CMake default also selected GCC 13 `f95/cc` despite the loaded GCC 14 module.
The new recipe checks parallel HDF5 before extra compilation and explicitly
pins C/C++/Fortran and MPI wrapper compilers. Short native probes `1828866`
and `1828868` passed HDF5 checks and real GCC 14 CMake compilation/execution
on 16V100 and DSPRHBM. 4V100 probe `1828867` failed before compiler execution
because Apptainer's `fuse2fs` overlay mount timed out.
The corrected suite ran 702 tests on SAI: 700 passed, two optional MD source
cache checks skipped. The upload change additionally passed 32 lifecycle and
recovery tests. Old overlays/controllers remain preserved under their original
identities; recipe changes require fresh prefixes. These are dependency-stage
and small compiler-probe results, not CP2K application or scientific acceptance.
The subsequent batch-upload revision also passed GitHub's 703-test suite
(701 passed, two optional MD cache checks skipped).
The historical development SIF
was rechecked on 16V100 by `1814450`: checksum, version and `ldd` passed;
scientific acceptance/publication remain unproven. Space recovered to about
109 GiB at release submission and was about 72 GiB at this audit. Live budgets
include the two compiling dependency overlays and old development job `1828523`;
after their remaining growth, 4V100 was roughly 50 MiB short of its 16 GiB
overlay plus the old 4 GiB safety reserve. This historical estimate no longer
gates new jobs. 8V100V0's failed dependency stage skipped application/acceptance;
there is still no successful release application SIF.
[Historical loader/source evidence](evidence/cp2k-release-priority-20261016.json).
[Historical parallel-build evidence](evidence/cp2k-gcc14-parallel-20261017.json).
[Current retry and diagnostic evidence](evidence/cp2k-cmake-hdf5-retry-20261017.json).

| Software | DSPRHBM | 16V100 | 4V100 | 8V100V0 |
|---|---|---|---|---|
| ABACUS | Pending | Pending | Pending | Pending |
| CP2K | GCC 14 dependencies: 1828946 RUNNING | 1829656 RUNNING; dependency configuration | GCC 14 dependencies: 1828944 RUNNING | 1829644 RUNNING; overlay/container preparation |
| DeePMD-kit | Not a GPU target | Pending | Pending | Pending |
| LAMMPS | Not a GPU target | Pending | Pending | Pending |
| GPUMD | Not a GPU target | Published; native module execution pending | Capacity blocked | Pending |

1. The final MD repeat/recovery audit and integrated suite passed. Continue to keep
   controller upload/fingerprint and workflow inputs aligned when diagnosing
   canary failures.
2. Keep all canaries, caches and temporary files in home while preserving
   logs, artifacts and recovery checkpoints. Run without software disk
   reservations or estimated free-space gates, per the user's instruction.
   Diagnose actual write/quota errors if they occur; do not move builds to
   host `/tmp` or delete historical checkpoints automatically.
3. GPUMD job `1790711` compiled and linked `gpumd`, `nep` and `gnep`, but
   failed packaging because current upstream replaced `LICENCE` with `LICENSES/`.
   The license layout is fixed and preflighted before compilation. Fresh job
   `1790933` completed from snapshot `b9cbf83` with exit `0:0` in 20:07 and
   passed artifact verification; science/performance job `1792098` passed
   against that exact SIF, which is now published. The failed overlay/log remain
   preserved. Its 3 GiB overlay is based on measured prior usage of 340 MiB plus
   conservative export/JIT headroom, and still passed the normal 13 GiB free-space
   gate under the historical policy. Rerun the corrected exported-native module
   probe in a new task, then expand to 4V100 and 8V100V0 with that exact recipe.
4. Current priority (2026-10-16): run official CP2K `v2026.2` release canaries
   on 16V100, 4V100 and 8V100V0, followed by DSPRHBM with its GPU scientific
   reference. Explicitly retry the previously attempted release; retain the
   development channel and its remaining acceptance work. The working checkout
   is now `~/sai-hpc-software/development/workflow-closure`. Record source,
   recipe/SIF hashes, Slurm jobs, raw science/performance and any repeat.
5. Validate each real delivery's export, manifests and module invocation, in
   addition to the synthetic tool probes above. Publish only after every gate
   passes. Regressions or failed builds require diagnosis; a passing local test
   or historical result is insufficient.
6. Review the integrated branch/PR and operational workflow evidence. Do not
   describe the matrix as closed until all 17 applicable entries are proven.

ABACUS and CP2K source/dependency preflights are ready. MD's pinned DeepMD
`025f4f698d4ff50c0b5538f19dc0b00ad1459ba3` and LAMMPS
`1b8520aa9c049778e329114ea9d9f51a06dee153` are complete in the remote cache;
dependencies and four `sbatch --test-only` requests passed. These are not real
builds. MD's local plan was regenerated for `6ca236f` in
`/tmp/sai-md-canary-20261015-current`; the original preflight remains preserved.
Any further controller edit requires a fresh recipe/plan before upload.
Queue start estimates from `--test-only` are advisory, not evidence that a
submitted job is blocked or a reason to restart one.

## Local validation

The latest full integration checkpoint ran **692 unittest tests, all passed,
with no skips**, using the preserved upstream cache read-only. Actionlint 1.7.12 validates every
workflow, including reusable phase and final-result jobs. The four PR validation
workflows passed on `b5b1391`; remote builds are intentionally not selected by PR
events. Shell syntax checks and
Python compilation also passed. These checks do not replace the pending real
scientific/performance matrix.
