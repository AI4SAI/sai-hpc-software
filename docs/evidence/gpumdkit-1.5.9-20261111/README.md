# GPUMDkit v1.5.9 portable SIF and administrator handoff

Upstream [v1.5.9](https://github.com/zhyan0603/GPUMDkit/releases/tag/v1.5.9),
released October 21, is pinned to `073371a45c9f58b0c0e2ca9ba7a7934705c6a677`.
The site currently has `/opt/apps/GPUMDkit/main-20260207`, whose actual CLI banner
is 1.5.1(dev). Updating adds the upstream fixes for active-learning selection,
ASE energy/virial interpretation and stress signs, compressed LAMMPS data input,
and improved thermal-transport processing. The new upstream release still prints
1.5.8 in its own banner; static records preserve that fact and identify the exact
tag/commit instead of changing upstream source.

## Payload

One generic x86_64 installation at `/opt/apps/GPUMDkit/1.5.9`, with a separate
`gpumdkit/1.5.9` module. No old installation, `.bashrc`, LAMMPS or GPUMD binary was
modified. Python 3.12.13 and numerical/Python dependencies are installed inside
the base SIF with a new project-backed prefix bound at the final `/opt` path.
Calorine 4.1 and NepTrain 2.2.0 compile with GCC 14.3 and explicit
`-march=x86-64 -mtune=generic`; their upstream build scripts add no native CPU
specialization. Vendor manylinux wheels retain their normal runtime dispatch.
This is one cluster x86_64 runtime, not separately built AVX2/AVX512 payloads.

The final SIF is 279,998,148 bytes (267.0 MiB), SHA256:

```text
1c0424c49a39c3b3e7928ba8b5863ec32c87a3d92dde3e8d3886d1c3ab137df2
```

The application has 22,393 regular files, 1,382 symlinks and 2,491 directories;
regular-file content totals 914,851,212 bytes (872.5 MiB). Project setgid flags
are removed from published directory modes, and Conda transaction metadata stays
outside the payload. Normal Python `.dist-info`, runtime resources, upstream
scripts/templates, headers and libraries are retained. Workflow code and custom
tests are outside `/opt`.

## Verified behavior

| Job | Route | Node | Result |
| --- | --- | --- | --- |
| 2375499 | Actual final SIF and matching native staged tree | 16v100n04 | COMPLETED 0:0, 2m27s |
| 2375591 | Actual installer extraction; real Lmod module | 16v100n07 | COMPLETED 0:0, 16s |

[Slurm accounting](accounting.txt) and [native module output](slurm-2375591.log)
record completion. The tests use actual `gpumdkit` commands for:

- POSCAR to extxyz with exact atoms/cell/coordinates.
- Gzipped LAMMPS atomic data to extxyz.
- A periodic-distance fixture with a 0.2 Å nearest image.
- Calorine NEP4 predictions of three Pb/Te frames, with two CPU workers.
- Numerical energy/force/virial comparisons against pinned upstream labels.
- A real PNG parity plot using the Agg backend and dependency inspection.

Maximum errors: energy 0 eV/atom; force 8.33e-16 eV/Å; virial 5.00e-16 eV/atom.
The [raw-output-bound native receipt](acceptance.json) records file hashes. SIF
and native receipts are additionally in the
[deployment package](../../../deployment/gpumdkit-1.5.9/release.json), with exact
image hash and completed job accounting. This is representative correctness
coverage, not a benchmark or validation of every optional script.

The administrator installer was run with `umask 077` under:

```text
/org/org-open-sai/proj-sai-hpc-software/operations/gpumdkit-native-stage-20261111
```

[The install log](stage-install.log) records successful extraction and full hash,
mode and symlink checks. Real Lmod loading selected the extracted native entry;
all functional checks and module unloading passed. Python's relative RPATH
finds its runtime libraries without adding its entire lib directory to the
user's `LD_LIBRARY_PATH`. [Login-readable help/whatis](module-help.txt) works and
has one Features-bearing whatis entry.

Initial jobs are preserved as diagnostics: 2375065 lacked the NepTrain metadata
build dependency `setuptools-scm`; 2375172 built the runtime and SIF but a test
expected the string `0.2000` instead of the correct displayed `0.200`; 2375303
passed SIF science but caught inherited setgid directory modes before native
export. Dependencies, numeric parsing and export modes were corrected. The
successful final repack reused the compiled extension bytes.

## Administrator handoff

The SIF, full inventory and scripts are publicly readable at:

```text
/home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9
```

`install.sh --check` passed. The reviewed command is:

```bash
bash /home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9/install.sh --install
bash /home/stardust/sai-hpc-software/deployment/gpumdkit-1.5.9/install.sh --verify
```

Public `/opt` installation is pending the administrator; successful staged
acceptance is not reported as a completed public installation.

OVITO, DeepMD/DPA inference, deprecated PyNEP, ferrodispcalc and NepTrainKit GUI
remain separate optional tools. GPUMD/NEP engines are loaded independently for
workflows that need them. This package does not require host MPI or CUDA.
