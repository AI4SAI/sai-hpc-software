# SpinGNN2 formal releases on SAI

The existing Daily workflow resolves `Hyyu-Lab/SpinGNN` formal releases and
delivers native `libspingnn2`, Python/ASE inference and `lmp_spingnn2`.
SpinGNN2 does not follow development branches or prereleases and has no beta
promotion. The existing 03:00 Asia/Shanghai VM scheduler still dispatches
`main`; a feature branch or pull request alone does not activate scheduled
delivery. There is no additional timer.

## Entry points and qualification

Use `.github/workflows/daily.yml` with `software=spingnn2`, `tracks=release`
and a target or `all`. `mode=smoke` checks the real relay/SSH identity and
the configured publication root without allocating compute. `mode=start`
freezes the resolved tag and full commit; `mode=resume` continues the frozen
generation. The normal relay checkpoint handles transport interruptions and
Slurm waits. No other software's target matrix is enlarged.

| Target | Partition | Recipe |
|---|---|---|
| `dsprhbm` | DSPRHBM | CPU/OpenMP, MKL |
| `16v100-avx2` | 16V100 | CUDA SM70, Kokkos VOLTA70 |
| `4v100-avx512` | 4V100 | CUDA SM70, Kokkos VOLTA70 |
| `8v100v0-avx512` | 8V100V0 | CUDA SM70, site MPI **AVX2** |
| `8a100m40` | 8A100M40 | Disabled until actual A100 acceptance |
| `8a100m80` | 8A100M80 | Disabled until actual A100 acceptance |

`controller/spingnn_site.json` records which targets have completed initial
qualification. Unqualified or unavailable targets are explicitly skipped;
accepted targets proceed independently. A100 nodes were `unk*` during initial
qualification. An SM80 cross-compile is not A100 runtime acceptance.

The initial v2.0.0 receipts cover all four enabled production targets:

| Partition | Successful build | Installed science | Public module check |
|---|---|---|---|
| DSPRHBM | 2947871 (recovery) | 2948062 | 2949506 |
| 16V100 | 2948654 | 2948684 | 2949539 |
| 4V100 | 2948655 | 2948685 | 2949494 |
| 8V100V0 | 2948656 | 2948686 | 2949920 |

These jobs belong to the initial installation. Daily adoption reuses their
evidence; it does not submit a second initial build. The installed version is
already available as `spingnn/2.0.0`. The `spingnn/release` entry described below
becomes available only after the reviewed Daily controller adopts and publishes
the accepted installations.

CPU-MISC is a free partition excluded from SpinGNN production magnetic
calculations. It is not a release target and cannot be selected explicitly,
through `all`, by retry, or by resuming a former request. Its original
experimental evidence is retained as history; it grants no deployment or
bootstrap adoption eligibility. No module fragment or release selector is
generated for CPU-MISC, even if an old pointer or qualification entry exists.

The current relay blocks unfinished ineligible SpinGNN tasks before polling
held jobs or invoking their frozen controllers. Other targets continue; the
generation reports the blocked target explicitly. Historical CPU-MISC claim
identities are checked for consistency only while reading the attempt history,
so they do not block supported targets or other software. New claims, resumes,
builds, adoption and publication continue to reject those identities. Existing
remote controllers or module files are not rewritten by a branch update;
withdrawal of public installation entries is a separate deployment operation.

A frozen SpinGNN controller whose site configuration still includes CPU-MISC
is also blocked: even a supported-target completion could regenerate its old
shared selector and restore the retired route. Start a new generation using
the reviewed controller, with `retry_releases=true` if the release was already
claimed; do not edit the frozen snapshot. Compatible SpinGNN
generations and other software continue normally.

Source tag/SHA/target changes trigger independent attempts. An unchanged
formal release is skipped even if a previous attempt failed or the recipe
changed: choose `retry_releases=true` to retry that selection explicitly.
Attempts have separate prefixes; a resumed request must keep its exact source,
controller and identity. Interrupted or ambiguous Slurm submissions fail
closed instead of creating duplicate jobs.

## Recipe and evidence

`spingnn_build.sh`, `spingnn_fixture.py`, and `spingnn_science.py` are the
parameterized recipes from the initial SAI installations. The official first
source is v2.0.0, commit
`2f4e7ca93e64b7113df1a9e9eafae4c1dc8279ec`. LAMMPS is pinned to
`stable_30Sep2026_update1`, commit `098ffdefa525cabc82272925de02e9884f58c657`,
including its annotated tag identity, before applying the upstream overlay.
Dependencies use the existing GCC, CMake, MPI, CUDA and MKL installations.
The MPI auto modules select the actual node's ABI; allocation, CPU, module,
wrapper, library hash and linker records are retained with each build.

SM70 is a documented SAI adaptation, not upstream v2.0.0 support. The exact
first-install patch is `controller/spingnn_sm70.patch`. `spingnn_source.py`
accepts only the five reviewed original or already-adapted file hashes; an
unknown or partially adapted release needs a newly qualified recipe. CPU
builds retain upstream source unchanged. CUDA requests allocate one GPU and
let site policy derive CPU/memory resources.

The fixture is an untrained six-atom periodic SOC numerical test, seed 1793.
Export uses a private environment derived from the existing Torch environment;
the installed inference venv has no Torch dependency. Native and independent
NumPy reference results must agree with Torch eager for energy, atomic energy,
forces, spin forces, tangent spin forces and virial below `1e-10`. LAMMPS
energy/force/spin errors must be at most `1e-11`, for baseline and rotated
spins, with nonzero forces and spin response. All seven fixture hashes and
installed executable/library hashes must match. CUDA qualification additionally
requires the requested backend/architecture, actual `sp2_` kernel execution
in Nsight, CUDA library mappings and the LAMMPS GPU pair style.

Inference runs one MPI rank, two OpenMP threads and one GPU when applicable.
The retained original DSPRHBM receipt used four OpenMP threads. These checks
do not establish trained-model accuracy, training support, multi-rank/node
scaling, long spin dynamics, or performance at large system sizes.

Initial accepted installations can be adopted through the pinned per-target
records in `spingnn_bootstrap.json`. Adoption rereads the original successful
Slurm accounting, scientific outputs and complete runtime inventory; it does
not rebuild, move a venv or rerun the first scientific job. Changed evidence or
runtime bytes fail explicitly.

The CPU-MISC withdrawal also updated production documentation and source
rebuild instructions. Refreshing retained GPU inventories requires reconstructing
their previous bytes from the deployment's `before/` snapshots and reproducing
the earlier complete inventory hash. Original build scripts remain pinned to
those preserved snapshots, with evidence paths constrained to the project's
`runs` tree. Binary and scientific evidence hashes remain unchanged.

## Native publication

The reviewed software-specific root is `/home/stardust/spingnn`; it is trusted
controller configuration, never an upstream release parameter. This exception
does not change any other software's `/opt` policy, the site's permissions, or
the old `spingnn-dream` module.

New builds use
`versions/<version>/<full-source-sha>/<full-recipe-sha>/<attempt>/<partition>`.
Installed-prefix science, module unload, successful Slurm accounting and a
matching sealed inventory precede publication. Complete immutable module and
receipt files are written to the same filesystem and published without
overwriting existing content. A root lock protects independent per-target
release pointers; an older completion cannot replace a newer accepted result.
The selector remembers the exact fragment at load time so pointer rotation
cannot change the identity used by unload.

```bash
source /etc/profile.d/lmod.sh
module use /home/stardust/spingnn/modulefiles
module load spingnn/release
module whatis spingnn/release
command -v python lmp_spingnn2
# Run inside an allocation for the selected partition, using your own account.
module unload spingnn/release
```

Login defaults to the accepted DSPRHBM CPU build. Inside an allocation,
`SLURM_JOB_PARTITION` selects an independently accepted target and fails
explicitly if none exists. Immutable versions also remain loadable as
`spingnn/<target>/<build-id>-<attempt>`.

Controller snapshots, claims, submission handles and scientific evidence live
under `/org/org-open-sai/proj-sai-hpc-software/experimental/spingnn2-release`.
Public modules live under `modulefiles/spingnn`; JSON receipts live separately
under `release-receipts` so Lmod does not mistake them for modules. Each native
prefix carries corresponding sources, the applied adaptation and license
notices. The existing initial versioned module remains available.
