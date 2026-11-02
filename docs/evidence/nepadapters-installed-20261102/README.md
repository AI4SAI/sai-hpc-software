# Installed NEPAdapters module verification — 2026-11-02

The administrator installed the simplified public module
`nepadapters/1.0.4-lammps30Sep2026`. Its SHA256 is
`4ecf824343b9f81480f02026ce64dc8572d45844cc4a93795a2193eae384119e`, matching
the release manifest. [Installation state](installation-state.json) confirms
the two accepted plugins and license are the only runtime files in
`/opt/apps/nepadapters/1.0.4-lammps30Sep2026`; the old partition copies are gone,
and all installed LAMMPS binary/library hashes match their recorded values.

Tests use the actual public `/opt` modules, physical LAMMPS and site MPI.
All work and scratch are under
`/org/org-open-sai/proj-sai-hpc-software/runtime-tests/nepadapters-installed-module-20261102-103222`.

| Partition | Module load/unload | Single/two GPUs on one node | One GPU on each of two nodes |
| --- | --- | --- | --- |
| 16V100 | 2114529 passed | 2114144 passed | 2114145 passed |
| 4V100 | 2114530 passed | 2114148 passed | 2114149 passed |
| 8V100V0 | 2114531 passed | Not repeated; 2114152 cancelled pending | 2114153 passed |

See [Slurm accounting](accounting.txt), [numerical submissions](submissions.json)
and [module submissions](module-submissions.json). Completed checks have exit
`0:0`. The previously identified ECC-error node `8v100v0n09` is excluded.

The module check starts with `module purge` and loads only NEPAdapters. It verifies
that the matching LAMMPS and MPI are loaded automatically and that
`info styles pair` lists `nep/cpu` and `nep/gpu/kk` through the module's plugin
path. Unloading removes the plugin path and its automatically loaded dependency.
The check then explicitly loads LAMMPS, attaches and detaches NEPAdapters, and
verifies that `PATH`, `LD_LIBRARY_PATH`, the LAMMPS prefix and the existing plugin
paths are preserved. No `SAI_NEPADAPTERS_PARTITION` variable is created.
The [shell check](check-module.sh) and [environment assertions](module-environment.py)
are recorded along with receipts for [16V100](module-16V100.json),
[4V100](module-4V100.json) and [8V100V0](module-8V100V0.json).

Numerical receipts for [16V100](native-16V100.json), [4V100](native-4V100.json)
and [8V100V0 distributed](native-8V100V0-distributed.json) were verified again
from raw outputs. The scope is the pinned eight-atom Pb/Te NEP4 case and 100
GPU dynamics steps. All completed GPU layouts have energy error 1.91e-7 eV,
maximum force-component error 2.76e-7 eV/angstrom and maximum virial-component
error 1.46e-6 eV, below the existing 1e-6/1e-6/5e-6 limits. LAMMPS and both
plugins resolve the same physical `libmpi.so.40` within each partition; per-rank
GPU UUIDs confirm distinct devices. CUDA Kokkos, double precision and
`comm device gpu/aware on` are verified in the numerical outputs.

8V100V0 had only isolated single GPUs available, so its extra local retest
2114152 never started. Its time limit was shortened to two minutes to allow
backfill, then it was [cancelled while pending](8V100V0-local-retest.json).
The identical plugin payload already passed single/two GPUs on that partition
in [job 2103050](../nepadapters-shared-20261102/README.md). That earlier result
remains applicable to the unchanged binaries; it is not counted as a new
post-install local run. The current public module and cross-node GPU calculation
both passed on 8V100V0.

The initial module probe incorrectly expected `lmp -h` to list dynamic plugin
styles. That command exits before runtime plugin discovery, so jobs
2114446/2114447/2114448 failed the probe assertion. Their
[submission](module-help-probe-submissions.json) and [logs](help-probe-failures.json)
are retained. The corrected check runs `info styles pair` through a real LAMMPS
input; all three retries passed. No application, plugin or public module changes
were needed.
