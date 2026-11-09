# Standard LAMMPS compiler cache qualification — 2026-11-09

On identical pinned LAMMPS sources, a fully warm standard ccache reduced compile/link time from **46m49.64s to 1m51.89s** (96.02% less time; 25.11x). Each SIF passed 14 numerical executions (**28 total**) and retained **71 packages** with identical styles.

This is one cold/warm pair with fresh overlays, checkout and build trees, the same allocation and 24 workers. It measures unchanged compilation inputs. Actual daily reuse depends on source, header, option and toolchain changes. OS page caches were not flushed. This is not an application runtime performance measurement or two-node publication acceptance.

| Measurement | Cold | Warm |
| --- | ---: | ---: |
| Configure | 0m35.20s | 0m33.25s |
| Include-layout inventory | 7.43s | 6.01s |
| Compile/link | 46m49.64s | 1m51.89s |
| Install | 0m44.19s | 0m37.34s |
| Full build/export/SIF verification, including probes | 53m23.00s | 6m44.00s |
| Build process-tree CPU seconds | 28527.89 | 343.66 |
| Sampled peak process-tree PSS | 23.51 GiB | 1.41 GiB |
| Cache hits / misses | 0 / 2543 | 2543 / 0 |

The header-layout inventory contains 36,758 entries and is identical across the pair. The source diff, compile commands and actual-toolchain context also match. Both builds passed 32 real GCC/Kokkos/NVCC cache probes: unchanged input hits, changed-header misses despite preserved size/mtime, equivalent-comment reuse, effective-flag invalidation and new shadowing-header invalidation. Probe caches are separate from the measured compiler cache. Configuration probes, linking, installation, SIF creation and scientific checks run afresh. Special CUDA fatbin/response-file work still compiles normally.

## Policy and official limitation

Input-content validation now uses standard ccache direct mode with preprocessor fallback; the launcher no longer invokes the compiler for a dependency scan before each lookup. Compiler-content/native-CPU checks remain, sloppiness is empty, and depend mode is disabled so that `-MMD` does not omit system-header validation. A once-per-build inventory of include names and symlink targets starts a new namespace when the file layout changes. Content-only edits leave unaffected compilation units reusable.

The source of the new-header caveat is the **ccache project's official manual**, [The direct mode](https://ccache.dev/manual/4.14.3.html#_the_direct_mode):

> header files that were used by the compiler are recorded, but header files that were not used, but would have been used if they existed, are not.

This is not a LAMMPS statement. The inventory addresses this documented case for the prepared source/include layout. Source and toolchain inputs must remain stable while compilation runs, and generated dependencies must precede their consumers.

## Provenance and acceptance

- Pinned LAMMPS PR #5308 source: `f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7`. Existing DeePMD registration is the only tracked source adaptation. The daily workflow does not apply that upstream PR to its selected sources.
- Node `16v100n25.mr-sai.ai`; allocation of 16 physical / 32 logical CPUs; 24 build workers; GCC 14.3.0, CUDA 12.9.1, SM70; Unix Makefiles and existing 71-package options.
- Build job `2319272` and science job `2320795` both completed with `COMPLETED 0:0`; detailed Slurm accounting is archived.
- The collector independently read `bin/lmp` and every regular shared/static library under `lib/` directly from each SIF and checked the export manifest. Both `bin/lmp` and `lib/liblammps.so.0` have identical cold/warm sizes and SHA256 hashes. SIF image hashes differ because each image also contains its own build metadata.
- One/two host-MPI ranks exercised GPU/Kokkos LJ, Coulomb and PPPM, plus DeePMD TF/PT/JAX energy/force/virial and PLUMED (14 numerical executions per image). Both images also passed version, dynamic-loader, package and style inventory checks.
- Frozen reference SHA256: `e5f75411c7891cb4df63bb71410daa6f21e5c91b6ee7bb8f987b41e4e0880478`.
- These benchmark images are unpublished and are not physical `/opt` installations. Normal per-partition production publication acceptance remains required.

The tested implementation is repository commit `1d4864c04a639a0889aaf4c4b310650b4070480e`. Its four GitHub validation workflows passed; the primary full-suite run completed 1,280 tests with two optional tests skipped, including the actual compiler cache tests. Local focused runs also passed 9 compiler/policy tests, 10 standalone workflow tests and 18 lifecycle tests. The `validation/` directory preserves the check receipts and logs; the source/controller files in the raw archive identify the implementation actually executed on SAI.

The earlier [strict-scanner experiment](../lammps-ccache-20261109/README.md) recorded 48m10.88s cold and 11m18.00s warm. That run used a different allocation/node; comparisons with it are historical observations, not an isolated same-node measurement of removing the scanner.

## Evidence

`summary.json` and `evidence.raw.tar.gz` retain the frozen controller, source/compile provenance, phase/process samples, cache statistics, allocation/accounting and numerical output. All **512** archived files were independently size/hash checked after download.

Archive SHA256: `08a4773a6f15971f59eebb8a2260cbb324d3a8007d81e8c4b54d16dbed8b8fbb`.

Remote experiment: `/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-lammps/generator-benchmarks/ccache-standard-20261109-v1`.

