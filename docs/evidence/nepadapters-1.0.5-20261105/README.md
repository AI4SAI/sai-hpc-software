# NEPAdapters 1.0.5 shared release acceptance

Upstream [v1.0.5](https://github.com/MagTheoryLab/NEPAdapters/releases/tag/v1.0.5),
commit `3ceba8f9b69765ac29d9f2f4f1c4f76cee8ecf37`, was published on 2026-11-05.
Its mixed/nonperiodic LAMMPS boundary support and CPU Spin1 per-atom virial fix
are included. The pinned source archive SHA256 is
`81be02f627503b6453a59d4cc228c1d7dbad9f99b50b2d603c151aa53b931e0b`.

The addon uses the installed LAMMPS 30Sep2026 (`8de817dd79bf`), GCC 14.3,
CUDA 12.9.1, Kokkos 5.2.1, C++20 and SM70. CPU native specialization is off.
There is one shared pair of plugins, with no embedded MPI RPATH/RUNPATH and
no copied MPI. Actual `ldd`/launcher records confirm they resolve the same site
Open MPI 5.0.10 library as LAMMPS on every partition (AVX2 on 16V100/8V100V0,
AVX512 on 4V100). Installed application paths are read-only build dependencies.

## Build and scientific evidence

| Partition | SIF build | SIF plus native single/two GPU | Native two nodes |
| --- | --- | --- | --- |
| 16V100 | 2212555 | 2212738 | 2212739 |
| 4V100 | same image | 2212742 | 2212743 |
| 8V100V0 | same image | 2212747 | 2212748 |

All seven jobs completed with exit `0:0`; see [accounting](accounting.txt).
The one compilation took 5:17. Every native local job rechecks the exact SIF.
Native jobs took 13–63 seconds. The [handoffs](accepted-handoffs.json) locate
the frozen run, cases, raw outputs and controller snapshots in project storage.
The unchanged source request and recipe pins are in [build-request.json](build-request.json).

[Build output](build.log) and the [artifact receipt](sif.json) record the upstream
Spin1 [CTest XML](upstream-spin-tests.xml): exactly two executed tests, one and
four OpenMP threads, with zero failures/skips. They compare per-atom virial
components between batch and LAMMPS-neighbor CPU APIs, including the OpenMP
reduction path. These tests are not installed into `/opt`.

The final SIF and native checks use the pinned eight-atom Pb/Te NEP4 fixture.
CPU and CUDA Kokkos GPU energies, all force components and virial are compared
to upstream labels under `p p p`, `p p f` and `f f f`, followed by 100 GPU MD
steps for every boundary mode. The mixed case wraps the translated cluster
across the periodic x face, exercising periodic ghost neighbors. The fully
nonperiodic case uses an interior cluster: it proves that mode executes with
the reference numbers, not a comprehensive free-surface or deposition test.
GPU energy error is at most 1.91e-7 eV, force-component error 2.76e-7 eV/angstrom,
and virial-component error below 1.5e-6 eV; limits are 1e-6/1e-6/5e-6 respectively.

[16V100](native-16V100.json), [4V100](native-4V100.json) and
[8V100V0](native-8V100V0.json) receipts include each layout, boundary, numerical
result, host MPI resolution and rank/GPU UUID. Single GPU, two GPUs on one node
and one GPU on each of two nodes all pass. Multi-rank checks use device
communication and CUDA-aware MPI. This validates correctness for these cases;
there is no performance or all-model compatibility claim. NEP3 and LAMMPS
CUDA qNEP remain unsupported upstream.

## Overlay lifecycle

First attempt 2212456 failed before compilation because the recently introduced
managed-overlay parser rejected the additional read-only LAMMPS build image;
its [failure log](failed-build-2212456.log) and untouched run remain available.
The CUDA plugin requires matching Kokkos source headers/generated configuration,
which the native LAMMPS installation does not export. The corrected helper
therefore retains that build image explicitly read-only and owns only the new
run's writable `work.ext3`. Foreign writable images, aliases and duplicates
are rejected. No public Apptainer/fuse2fs binary or LAMMPS installation changed.

The successful retry's [two lifecycle receipts](managed-overlays.json) show a
clean writable build teardown (1.03 s) and clean read-only export teardown
(0.0034 s). Both have payload/driver/unmount status zero, accepted/completed
true and forced_stop false. The writer and all original cleanup/error gates
remain managed. The later canonical-path and skipped-test checks strengthen
preflight/replay of this same accepted run; they do not modify its frozen
controller, recipe or artifacts. The 8V100V0 tests explicitly exclude the
previously diagnosed ECC node `8v100v0n09`.

## Administrator handoff

The public package is
`/home/stardust/sai-hpc-software/deployment/nepadapters-1.0.5`.
The [manifest](../../../deployment/nepadapters-1.0.5/release.json) installs only:

```text
/opt/apps/nepadapters/1.0.5-lammps30Sep2026/
  lib/nepadapterscpuplugin.so
  lib/nepadaptersgpuplugin.so
  share/licenses/nepadapters/LICENSE.NEP_CPU
/opt/modules/modulefiles/apps/nepadapters/1.0.5-lammps30Sep2026
```

The payload is 32,341,245 bytes (30.84 MiB); the SIF is 19,464,192 bytes
(18.56 MiB). SIF SHA256 is
`f94aa8e5e5265d0785e07a926824006252e23da527bf187cbd3c45897e2c166e`;
CPU plugin `912d5dffe69aae2c9179133eacd5db835377a6785bd9c963bd86ecacfa173a2f`;
GPU plugin `c7514b590c96a6fbcae57139f6cf3c4395d85f4d40d49b0aa91be897493fa271`.

[Public preflight](public-preflight.json) passed as stardust; the package and
its parents are publicly readable/traversable, and `saiuser01` owns both target
parent directories. These are permission/preflight checks, not an installation
performed as that administrator. The new physical prefix/module do not yet
exist. The old 1.0.4 package preflight still passes, as do every installed
LAMMPS executable/library hash pin. The new manifest has an empty `replaces`
list and preserves those existing installations.

The administrator runs:

```sh
cd /home/stardust/sai-hpc-software/deployment/nepadapters-1.0.5
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

After actual installation, stardust can run `bash submit-native-checks.sh` to
check the public module. The prepared module only loads the accepted LAMMPS
module and sets one shared plugin path; LAMMPS controls partition/MPI selection.

The final implementation head `813ec3b` passed [full CI](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41986229127):
1,203 tests, two skipped, plus the dedicated NEPAdapters and managed-overlay
checks. Subsequent changes only archive evidence and deployment metadata.
