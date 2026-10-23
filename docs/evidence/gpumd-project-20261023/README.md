# GPUMD release and master project-account delivery

The October 23 release run builds upstream `v5.9.1` at
`11ba6fbdb4d71b2cdb351b39972fa057521585b3` inside Apptainer on each native
V100 partition. Controller commit `87a77ddf1bf3448abf9aef5cc8e8a2db0233d791`
and recipe `f9c263cf362c30efe86c10074e10ee88f30f6280c09f5885ba8971c7b530c079`
are pinned in the frozen runs. The canonical build ID is
`v5.9.1-g11ba6fbdb4d7-rf9c263cf362c`.

[GitHub workflow 39955137406](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39955137406)
owns the build, science, export and publication phases. [delivery.json](delivery.json)
records the observed state and independent revalidation.

| Partition | Build job | Build elapsed | Science job | Current status |
| --- | --- | --- | --- | --- |
| 4V100 | 1911768 | 1m20s | 1912292 | Accepted, exported and published |
| 16V100 | 1911773 | 1m59s | 1911814 | Accepted, exported and published |
| 8V100V0 | 1911767 | 2m56s | 1911819 | Accepted, exported and published |

Every job uses account `proj-sai-hpc-software` and a working directory below
`/org/org-open-sai/proj-sai-hpc-software`. Source, objects and installation are
inside the file-backed overlay. Host scratch stays in the project tree.
The small base SIF and independent source cache were copied there; existing
home data was preserved. The build uses public `fuse2fs/1.47.4-sai`, GCC 13.3,
CUDA 12.9.1 and `sm_70`, with native CPU flags for each partition. All three
commands (`gpumd`, `nep`, `gnep`) retain DeePMD and PLUMED support.

The science jobs completed in 94–129 seconds. Static gold
energy/forces match exactly; NEP prediction, JIT/generic training, GNEP
finite-difference forces, PLUMED force feedback and DeePMD PyTorch evaluation
pass. GNEP maximum finite-difference force error is 0.00314 eV/Å. The frozen
2000-atom, 10000-step NEP benchmark alternates candidate/baseline executions
after warmup. Median candidate/baseline throughput ratios are 0.99961 on
16V100, 0.99824 on 4V100 and 0.99524 on 8V100V0. No performance retest was needed.

Independent verification reparses raw scientific outputs, checks the SIF and
controller hashes, re-reads all 402 exported files per accepted target and
validates module metadata. These native trees are staging exports. The
physical canonical `/opt/apps/gpumd/GPUMD-release-<build-id>/<partition>`
directories have not been installed or tested natively; no physical `/opt`
acceptance is claimed. Current upstream master has separate accepted artifacts
and evidence below.

The accepted container module is available within an allocated GPU job with:

```bash
module use /org/org-open-sai/proj-sai-hpc-software/modulefiles/apps
module load gpumd/release/v5.9.1-g11ba6fbdb4d7-rf9c263cf362c
```

The module chooses the actual Slurm partition. Its launchers use the pinned
SIF and site dependencies; they do not require an `/opt` installation of this
new GPUMD version.

[overlay-acl.json](overlay-acl.json) preserves the first attempt
(`39953638995`, jobs `1911630`, `1911632`, `1911636`), which stopped before
compilation. Project default ACLs were copied into the overlay seed and
contained identities unmapped by fakeroot. Initializing only private scratch
without inherited ACLs fixed container writes; project/input/result ACLs
remain unchanged.

The frozen 4V100 build and science jobs initially requested flood QoS. Their
queue settings were updated to the project-authorized `rush-1o2gpu` and
shorter time limits, preserving original scripts and logging each adjustment
under the corresponding `results/` directory. Future CI uses 30-minute
builds and 15-minute science jobs. It selects `rush-1o2gpu` on 4V100/8V100V0
and `flood-1o2gpu` on 16V100: the latter partition disallows `rush-1o2gpu`,
while `rush-gpu` requires four GPUs. Both selected QoS entries have
`UsageFactor=1` and no configured preemption targets.

[master-first-attempt.json](master-first-attempt.json) records the first
current-master attempt (`39961430052`). 4V100/8V100V0 compiled all three
executables but failed relocated JIT because packaging mistakenly excluded
the new upstream `main_nep/nep/` source directory. The pending 16V100 job
`1912383` was cancelled after its partition rejected the chosen QoS.
Commit `66d7b8e90acc570ffcb7af2ba97920d92484c21b` fixes both problems;
[workflow 39963334471](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39963334471)
passed all targets with unchanged scientific and performance gates.


## Accepted current master

[development.json](development.json) records independent revalidation of
upstream `master` at `2c93346fce4461de1c42c77e7fe74cdb6b0e3df6` (October 21).
Controller `66d7b8e90acc570ffcb7af2ba97920d92484c21b` and recipe
`59fe63bf80c87937f8baded42710195d30fd54dea0a39d36639713c5bf29ae47` are pinned.
The canonical build ID is `master-2026-10-21-g2c93346fce44-r59fe63bf80c8`.

| Partition | Build job | Build elapsed | Science job | Science elapsed | Candidate/baseline throughput |
| --- | --- | --- | --- | --- | ---: |
| 4V100 | 1912478 | 1m13s | 1912533 | 1m34s | 0.998318 |
| 16V100 | 1912477 | 2m09s | 1912536 | 2m02s | 0.997244 |
| 8V100V0 | 1912479 | 3m14s | 1912539 | 2m05s | 0.997689 |

All six jobs completed with `0:0`. All three master artifacts are accepted,
exported and published, with 457 files per native export. Static gold
energy/force errors and JIT/generic loss differences are zero. NEP prediction,
GNEP training/prediction/finite-difference forces, PLUMED force feedback and
DeePMD PyTorch GPU checks pass. Throughput differs by less than 0.3% in the
same frozen 2000-atom NEP benchmark; no retest was required. These compact
acceptance cases do not certify performance for every production workload.

[development-allocations.json](development-allocations.json) independently
records the actual project account, working directory, partition-permitted
QoS and 30-minute build allocation, plus private scratch mode 0700 with no
inherited POSIX ACLs. Science accounting confirms the 15-minute allocations.
All code, source, objects and runtime scratch use the project tree or its
contained overlay. Physical `/opt` installation remains pending for master.

Within a GPU allocation, load the accepted development container module with:

```bash
module use /org/org-open-sai/proj-sai-hpc-software/modulefiles/apps
module load gpumd/development/master-2026-10-21-g2c93346fce44-r59fe63bf80c8
```

The GitHub validation at this controller revision ran 967 tests (2 skipped),
with all remaining tests and shell syntax checks passing. The local GPUMD
suite passed all 52 tests, including preservation of the reorganized JIT
sources and both upstream runtime source layouts.
