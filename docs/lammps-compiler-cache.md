# LAMMPS compiler cache

The standalone LAMMPS workflow builds in a new container overlay on every run.
It can reuse individual compiled objects after checking the current compilation
inputs. Configuration, linking, installation, SIF creation and scientific
acceptance still run normally. This does not change installed modules or add a
runtime dependency on ccache.

`controller/md_ccache.py` delegates input validation to ccache's standard direct
mode and preprocessor fallback. It no longer invokes a dependency scan before
every lookup. Changed source or header contents, compiler options and toolchain
inputs invalidate affected results. Equivalent preprocessor output, such as a
comment-only edit, may safely reuse an object. Depend mode is disabled so that
system headers remain checked even when a command uses `-MMD`.

Ccache's [official direct-mode documentation](https://ccache.dev/manual/4.14.3.html#_the_direct_mode)
describes a rare limitation: adding a header earlier in the search path can
shadow a previously used header without invalidating its manifest. After CMake
configuration, this workflow inventories the names and symlink targets under
the source and include directories from `compile_commands.json`, plus the
host compilers' default include directories and CUDA headers. A changed layout
selects a new cache namespace for the build. File contents and timestamps do
not enter this inventory; ccache handles contents itself. Thus content-only
changes retain unrelated hits, while file additions/removals conservatively
invalidate the build's previous namespace. The inventory runs once per build,
after CMake has generated its headers. Configuration compiler probes run
without caching. Response-file or other unsupported input forms compile normally.

Ccache also checks the compiler arguments and compiler contents. A per-build
context records the partition, expanded native CPU flags, GCC specifications,
the actual GCC/NVCC code generators and their shared libraries. It is hashed as
an additional input. `nvcc_wrapper` itself is checked by content. Thus a wrapper
which has not changed cannot conceal a changed underlying compiler. External
library headers participate in the dependency scan; linking against libraries
is always performed afresh.

Ccache 4.14's native-CPU query uses `-###`, which `nvcc_wrapper` routes to NVCC
and NVCC rejects. The launcher marks `-march=native` and `-mtune=native` with
ccache's `--ccache-skip` when invoking that wrapper. Both options still enter
the direct hash and reach the compiler unchanged; the expanded CPU options
are checked through the context above. This avoids the unsupported query
without ignoring either the options or the actual CPU architecture.

The launcher removes inherited `CCACHE_*` overrides and supplies its own
configuration: content checks, empty `sloppiness`, no inode/stat-only cache,
compression, and a 20 GiB limit per partition. The normal ccache race and
unsupported-input protections remain enabled. The workflow assumes source/include
layouts and the installed toolchain are stable during compilation; generated
dependencies must precede their consumers.

The checksum-pinned official ccache 4.14.3 static executable is transferred by
the CI runner when missing. Compute nodes do not download it. All persistent
data lives under the funded project, outside the software installation:

```text
/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-lammps/cache/
  tools/ccache-4.14.3/ccache
  compiler/lammps/
    16v100-avx2/
    4v100-avx512/
    8v100v0-avx512/
```

Each partition has a private cache owned by the build account. Only the build
phase binds it writable at `/ccache-store`; the executable is bound read-only
at `/ccache-tool`. Temporary compiler files stay in the project-backed overlay
at `/workspace/tmp/ccache`. Export and runtime acceptance do not bind the cache.
Results retain `ccache-context.json`, `ccache-layout.json`, `ccache-stats.log` and
`ccache-statistics.json`. Parallel runs use separate statistics logs.

The first build for a partition/toolchain fills its cache. Later commits reuse
only unaffected compilation units. An empty or evicted cache increases compile
work; it never permits skipping acceptance. Source selection and the CMake
generator remain unchanged.

## Host compiler for sources without Kokkos

The shared LAMMPS build recipe also backports the 19-line CMake change from
[upstream PR #5308](https://github.com/lammps/lammps/pull/5308), pinned to
`f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7`. It adds `nvcc_wrapper --host-only`
to ordinary C++ sources that do not include the upstream list of Kokkos-related
headers. Kokkos and CUDA sources retain their CUDA compilation path. This
reduces cold-build compiler work independently of ccache.

`controller/md_lammps_host_only.py` runs after the normal DeePMD registration
adaptation and before CMake. It is included in the recipe fingerprint and the
frozen controller uploaded to SAI. The same helper serves release, prerelease
and development builds on all three GPU targets, including paired builds.
It never changes the selected upstream commit or the installed dependency.

An already present block is reused. A different upstream `--host-only`
implementation is left alone. An older wrapper or an unrecognized CMake
insertion point keeps its original build rules and reports a `skipped-*`
status; it does not block the daily build merely because this optimization
cannot be applied. No compute-node download of a moving PR is involved.

After configuration, the helper inspects `compile_commands.json`. An applied
backport must produce effective host-only commands and must not assign them
to `src/KOKKOS` or CUDA translation units. The `lammps-host-only.json` receipt
records the upstream commit, patch and CMake hashes, decision, compile-database
hash and command counts. Standalone jobs copy it into their build results even
if compilation fails; successful SIFs retain it under `share/sai/`.

The backported parent CMake file is byte-identical to the pinned upstream PR,
including after normal DeePMD registration. The current
`stable_30Sep2026_update1` also has the supported insertion point and wrapper.
Seven tests exercise the actual CMake source selection, disabled backends,
idempotency, future upstream rules, unsupported layouts and invalid generated
commands. The [complete native build and numerical comparison](evidence/lammps-pr5308-20261108/README.md)
already passed all 28 executions with 71 packages: cold compile/link decreased
from 52m53.71s to 38m49.57s in one matched pair. It covers the pinned development
source on 16V100; it does not establish release or other-partition speedups.

## Compiler-cache measurements

The former per-object dependency scan and raw-content hashing had a cost. The experiment in
`experiments/lammps-ccache` uses the same pinned PR #5308 source twice, fresh
overlays, one node, 24 workers and identical options. It separately records
configuration, compilation/linking, installation and numerical validation.
These measurements are distinct from the previous comparison of PR #5308
against its base without any compiler cache.

The [November 9 standard-cache qualification](evidence/lammps-ccache-standard-20261109/README.md)
completed both builds and all 28 numerical executions. On the same 16V100 node,
compile/link time fell from 46m49.64s cold to 1m51.89s warm (96.02% less time,
25.11x speedup). All 2,543 cacheable calls hit with zero misses; 107 special
CUDA commands still compiled normally. The once-per-build include inventory
took 7.43s cold and 6.01s warm. This is a fully unchanged-source comparison on
the pinned PR #5308 source. Daily hit rates depend on changed inputs and
compiler options; other partitions populate their own caches on their first
build. Switching from the former cache policy also requires populating the new
namespace; the experiment's private cache is not installed as a production cache.

The [former scanner's qualification](evidence/lammps-ccache-20261109/README.md)
recorded 48m10.88s cold and 11m18.00s warm. It ran on a different allocation/node,
so that comparison is historical rather than an isolated same-node measurement
of removing the extra dependency scan.
