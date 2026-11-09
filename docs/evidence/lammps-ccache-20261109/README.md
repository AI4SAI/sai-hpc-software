# LAMMPS strict compiler cache — 2026-11-09

For identical sources and options in fresh container overlays, a warm compiler
cache reduced full compilation/linking from **48m10.88s to 11m18.00s**, a
**76.55%** time reduction and **4.26x** speedup in this pair. The full flow fell
from **53m10s to 16m44s (68.53%)**. Both SIFs passed all **28 numerical executions**
and retained **71 packages** with identical complete style inventories.

This measures a fully warm cache for unchanged inputs. Subsequent source edits
reuse only unaffected compilation units; their hit rate and build time depend
on which dependencies changed. This is one sequential cold/warm pair on a shared
cluster, with OS caches left intact, not an application runtime benchmark or
a multi-partition performance estimate. The daily workflow does not apply the
unmerged upstream PR used as this experiment's pinned source.

## Measurements

| Measurement | Cold cache | Warm cache |
| --- | ---: | ---: |
| Configure | 0m29.79s | 0m31.85s |
| Compile and link | 48m10.88s | 11m18.00s |
| Install inside container | 0m46.14s | 0m54.65s |
| Full build/export/SIF check, including probes | 53m10.00s | 16m44.00s |
| Build process tree CPU seconds | 28991.32 | 2917.95 |
| Average logical CPUs used by build process tree | 10.03 | 4.30 |
| Sampled build process tree peak PSS | 25.62 GiB | 4.15 GiB |
| Cache hits / misses | 0 / 2543 | 2543 / 0 |

Each build has **2651** compile-command records, of which **2650** are executed.
The extra record belongs to the unused Kokkos `impl_git_version` target.
The launcher deliberately bypasses **107** commands without the supported
object-compilation form, including CUDA fatbin/response-file work;
those still compile normally. The warm hit rate among cacheable calls is
**100.00%**. Both builds recorded zero ccache internal,
compiler and preprocessing errors.

The configure/build/install timers exclude the preliminary cache probes; the
full-flow timer includes them, source preparation, packaging, filesystem checks
and SIF inventory verification. Both variants ran the same probes. PSS is
sampled about every two seconds across CMake's descendants; it excludes filesystem
page cache and external container daemons and can miss short peaks. Slurm's
whole-job MaxRSS is retained separately and is not assigned to either variant.

## Controls and cache policy

- Pinned upstream PR #5308 source: `f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7`. The only tracked-source
  adaptation is the existing DeePMD registration integration.
- Same allocation and node `16v100n32.mr-sai.ai`, 16 physical cores / 32
  logical CPUs, identical affinity and **24 workers**, Unix Makefiles, Release
  `-O3`, GCC 14.3.0, CUDA 12.9.1, SM70 and native CPU tuning.
- MPI/OpenMP, GPU, Kokkos CUDA, cuFFT, CUDA MPS, installed DeePMD 3.2.0,
  PLUMED 2.10.1, Eigen and Voro++ retain their existing settings.
- Each build starts from a separate fresh overlay, checkout and build tree.
  Only the private compiler cache is shared. The cold submission checks for
  zero cached result/manifest objects and never clears an existing cache.
- Compile-command databases, expanded native/toolchain context, source diff and
  host-only selection records are byte-identical across the pair.
- The versioned LAMMPS install prefix does not appear in any of the 2,651
  compiler commands or in the compiler context; the per-run host path is also
  absent from that context. Daily delivery labels therefore do not by themselves
  invalidate all objects. Generated source/header content is still checked.
- Before each lookup, the actual compiler scans all dependencies, including
  system headers. The launcher hashes their raw bytes and paths. Ccache also
  checks compiler arguments and content. No mtime-only or relaxed header check
  is enabled. Linking, installation and acceptance always run afresh.

The [cache design](../../lammps-compiler-cache.md) describes the pinned ccache
4.14.3 executable, compiler/code-generator/shared-library checks, native CPU
context, cleared inherited overrides and unsupported-mode fallback.

Post-run inspection corrected the experiment's empty-cache guard: ccache 4.14
uses plain hash filenames, rather than the older M/R suffixes. Future cold
submissions allow only statistics/tag files and reject every other file. All
persisted result/manifest entry creation times fall within this cold run, and
its log records 2,543 misses with zero hits. The guard correction and corrected
file-type accounting do not change the measured cold/warm pair.

## Execution and numerical acceptance

Build job **2312407** and science job **2313814** both
completed with Slurm state `COMPLETED` and exit code `0:0`.

- Cold flow: `2026-11-09T11:25:17+08:00` to `2026-11-09T12:18:27+08:00`.
- Warm flow: `2026-11-09T12:18:27+08:00` to `2026-11-09T12:35:11+08:00`.
- Each variant passed 24 real GCC, Kokkos host/CUDA and native NVCC cache probes:
  cold misses, warm hits, same-size/same-mtime header change misses, comment-only
  source change misses, and correct values from linked executables.
- Both SIFs passed version, dynamic-loader, package and style inventory checks.
- Host OpenMPI 5.0.10 launched one/two-rank DeePMD TF/PT/JAX energy, force and
  virial cases with PLUMED (`atol=1e-7`, `rtol=1e-5`).
- One/two-rank GPU and Kokkos LJ/Coulomb/PPPM cases passed with explicit
  GPU/cuFFT assertions (`atol=2e-6`, `rtol=2e-5`).

The accepted frozen reference SHA256 is
`e5f75411c7891cb4df63bb71410daa6f21e5c91b6ee7bb8f987b41e4e0880478`.
There are 14 numerical cases per SIF plus four version/inventory executions.
DeePMD cases establish interface and numerical compatibility; they do not prove
that every backend operation executes on a GPU or measure backend performance.
This is one-node regression, not the production two-node publication contract
or a runtime speedup claim. These experimental SIFs are not published as beta
modules or installed in physical `/opt`.

At implementation commit `edac6887a20edf174bf6af1edfc9daed0bde972e`, the clean
GitHub suite ran 1,277 tests successfully (2 environment skips), including both
real-compiler cache tests. All four validation workflows passed. Local targeted
tests also passed; the initial full local suite's missing-CMake errors were
resolved by installing CMake privately and rerunning all 10 tests of that module.
The [GitHub status receipt](github-validation-edac688.json) and
[full validation log](github-validation-edac688.log.gz) retain those results.

## Preserved setup attempts

| Attempt | Job | Outcome and diagnosis |
| --- | ---: | --- |
| v1 | 2311964 | Failed after 2m06s: optional CUDA debugger library entered an overly broad toolchain check; narrowed to actual compilation tools. |
| v2 | 2312011 | Failed after 2m47s: nvcc_wrapper did not forward `-MF` during standalone `-M`; switched the scan's dependency output to `-o`. |
| v3 | 2312165 | Failed after 1m22s: the probe's link retained `-x cu` for an object file; corrected the test link. |
| v4 | 2312210 | Stopped deliberately after 17m27s: native-option queries caused ccache internal errors in the full build. No useful main-cache objects were produced. |

The v4 fix uses documented `--ccache-skip` only for the wrapper's native CPU
options: the options remain hashed and reach the compiler unchanged; the
expanded native flags remain in the context. Native flags were also added to
all cluster probes and a real-compiler regression test. The successful v5 pair
uses that fix; earlier failed receipts and raw cache diagnostics remain archived.

## Storage and evidence

Cluster root:
`/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-lammps`.

- Experiment: `generator-benchmarks/ccache-20261109-v5`.
- Frozen controller: `generator-benchmarks/ccache-20261109-v5-snapshot/controller`.
- Science: `runtime-tests/ccache-20261109-v5-science`.
- Images: `runs/ccache-20261109-v5-{cold,warm}/candidate.sif`.

| Candidate | Bytes | SHA256 |
| --- | ---: | --- |
| cold | 285216768 | `68037f35b1d278b9bde7bad7c5f06edf4593c5948d510a16c794db57ab7061d6` |
| warm | 285216768 | `807be2c0b0b01e9c8f5defb7c360fbe9876c53374d2bc18ec13038520e5f3102` |

The `lmp` executable itself is byte-identical in both candidates: SHA256
`603b9b9c4040370aa3f780e5004c532b249414995d5bf1884ac0c4df5b587cd1`.

The measured 16V100 cache contains 2543 results and 2543 manifests, using
300.71 MiB of file content (416.16 MiB allocated, including statistics).
The configured limit is 20 GiB per partition. Other partitions
start cold; different source/compiler options may also miss this experiment's
entries. Cache data stays in the project and is never exported into a SIF or
transferred through the VPS. No administrator installation is needed.

[summary.json](summary.json) contains unrounded measurements.
[evidence.raw.tar.gz](evidence.raw.tar.gz) contains **536 files** with
controller snapshots, requests, logs, compiler commands, cache statistics,
process samples, Slurm accounting, filesystem checks, numerical outputs and
rank/loader traces. Every file was verified against the archived SHA256 manifest
after download. Image and model binaries remain on the cluster.

Archive SHA256: `245f29d17073a1914b611c6968384edb953c79bcdb84e29a232233f766af218b`.
The corrected remote archive is `generator-benchmarks/ccache-20261109-v5/evidence-v2.raw.tar.gz`;
the first archive with incorrect file-type counts remains preserved there.
