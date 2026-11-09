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
work; it never permits skipping acceptance. The current upstream source and
package selection remain authoritative; enabling ccache does not apply an
unmerged upstream patch or change the CMake generator.

The former per-object dependency scan and raw-content hashing had a cost. The experiment in
`experiments/lammps-ccache` uses the same pinned PR #5308 source twice, fresh
overlays, one node, 24 workers and identical options. It separately records
configuration, compilation/linking, installation and numerical validation.
These measurements are distinct from the previous comparison of PR #5308
against its base without any compiler cache.

The [November 9 qualification of the former scanner](evidence/lammps-ccache-20261109/README.md) completed
both builds and all 28 numerical executions. On 16V100, compile/link time fell
from 48m10.88s with a cold cache to 11m18.00s with a warm cache (76.55% less time,
4.26x speedup). All 2,543 cacheable calls hit; 107 special CUDA commands still
compiled normally. This is a fully unchanged-source comparison on the pinned
PR #5308 source. Daily hit rates depend on changed inputs and compiler options;
other partitions populate their own caches on their first build.
