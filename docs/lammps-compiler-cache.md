# LAMMPS compiler cache

The standalone LAMMPS workflow builds in a new container overlay on every run.
It can reuse individual compiled objects after checking the current compilation
inputs. Configuration, linking, installation, SIF creation and scientific
acceptance still run normally. This does not change installed modules or add a
runtime dependency on ccache.

`controller/md_ccache.py` provides the compiler launcher. Before each lookup it
asks the actual compiler for its current dependencies with `-M`, including
system headers, and hashes the bytes of every returned file. Their paths and
SHA-256 digests form an additional cache namespace. This detects a newly added
header earlier in the include search path as well as changed existing headers.
Even a comment-only edit changes the namespace. A changed file does not prevent
unrelated translation units from using their existing objects.

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
compression, and a 20 GiB limit per partition. Unsupported scanning modes or
unreadable dependencies fall back to real compilation. The normal ccache race
and unsupported-input protections remain enabled. The workflow assumes the
private source tree and installed toolchain are not modified during a build.

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
Results retain `ccache-context.json`, `ccache-stats.log` and
`ccache-statistics.json`. Parallel runs use separate statistics logs.

The first build for a partition/toolchain fills its cache. Later commits reuse
only unaffected compilation units. An empty or evicted cache increases compile
work; it never permits skipping acceptance. The current upstream source and
package selection remain authoritative; enabling ccache does not apply an
unmerged upstream patch or change the CMake generator.

The additional dependency scan and raw-content hashing have a cost, so the
warm-cache speedup must be measured on the cluster. The experiment in
`experiments/lammps-ccache` uses the same pinned PR #5308 source twice, fresh
overlays, one node, 24 workers and identical options. It separately records
configuration, compilation/linking, installation and numerical validation.
These measurements are distinct from the previous comparison of PR #5308
against its base without any compiler cache.
