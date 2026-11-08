# LAMMPS compilation source audit — 2026-11-08

The clean Make/Ninja pair suggests investigating work done by the compiler,
particularly the Kokkos compiler wrapper, before changing the build generator.
This is a source and generated-rule audit. None of the proposed changes has a
measured full-build speedup or runtime acceptance yet.

The source examined is the measured LAMMPS revision
[`d291d572cf3c73a8a720774b78482a7c931304ca`](https://github.com/lammps/lammps/tree/d291d572cf3c73a8a720774b78482a7c931304ca),
including bundled Kokkos 5.2.1. The build used GCC 14.3.0 and CUDA 12.9.1.
The existing [benchmark report](README.md) and raw archive supply timings and
compiler commands. Generated rules were additionally read with `debugfs`
without writing to either completed build overlay. Installed NVCC help and
the wrapper's `--show` mode were checked; no new cluster compilation was run.
The [audit evidence](source-audit-evidence.json) records generated rules,
compiler help, the wrapper dry run, timing groups and hashes of inspected
upstream source files.

## 1. Generate header dependencies during the existing NVCC invocation

This is the first experiment to try because it can retain all source files,
optimization settings, backends and scientific code.

The bundled [`nvcc_wrapper`](https://github.com/lammps/lammps/blob/d291d572cf3c73a8a720774b78482a7c931304ca/lib/kokkos/bin/nvcc_wrapper#L197)
intercepts `-MD` / `-MMD` and requests a separate dependency pass. At
[the dependency execution path](https://github.com/lammps/lammps/blob/d291d572cf3c73a8a720774b78482a7c931304ca/lib/kokkos/bin/nvcc_wrapper#L620)
it compiles the object, then starts NVCC again with `-M`.

The measured Ninja build has **2,544 compiler commands using this wrapper**.
All 22 generated CXX compiler rules, including scanned and unscanned variants,
pass `-MD -MT ... -MF ...`; the Make rule for `atom.cpp` does too. A wrapper
dry run reproduces the two invocations:

```text
nvcc ... -x cu example.cpp -c -o example.o &&
nvcc ... -x cu example.cpp -M -MT example.o -o example.o.d
```

The installed CUDA 12.9.1 compiler supports
`--generate-dependencies-with-compile` (`-MD`), with `-MF` and `-MT`.
An opt-in wrapper change can pass these through and remove the standalone
dependency invocation on this supported toolchain. This removes a second
NVCC invocation per wrapper compilation; the compiler's internal work and
the resulting wall-time saving must still be measured. It is especially
relevant to a build involving repeated header reads through FUSE, although
the earlier short filesystem sample does not prove that these scans are the
dominant bottleneck.

Validation must compare dependency target names and header sets, test that a
changed header rebuilds its consumers in an isolated build, and cover host-only
mode, failures and `-MMD` semantics. The old wrapper maps both `-MD` and `-MMD`
to `-M`, which includes system headers; changing that behavior should be an
explicit decision rather than an accidental consequence. Then measure a clean
full build and repeat the package/style and numerical gates.

## 2. Compile independent host sources with the host compiler

[`KOKKOS.cmake`](https://github.com/lammps/lammps/blob/d291d572cf3c73a8a720774b78482a7c931304ca/cmake/Modules/Packages/KOKKOS.cmake#L182)
explicitly describes processing every translation unit with the device
compiler. Our recipe selects `nvcc_wrapper` as `CMAKE_CXX_COMPILER`; the
wrapper unconditionally adds `-x cu` to C++ inputs. The actual command database
confirms this for ordinary `atom.cpp`, `library.cpp` and OPENMP sources.

The command/timing breakdown is:

| Source group | Compiler commands | Sum of measured task durations |
| --- | ---: | ---: |
| LAMMPS `src/`, excluding KOKKOS and OPENMP | 1,396 | 25,515.49 s |
| LAMMPS OPENMP | 371 | 6,901.81 s |
| LAMMPS KOKKOS | 407 | 40,461.03 s |
| Kokkos library | 24 | 540.47 s for 23 matched outputs |

These are overlapping task wall durations, including waits. They are neither
CPU seconds nor additive elapsed build time. Other libraries and generated
sources are omitted from this table. In particular, preinstalling the small
Kokkos library does not remove the 407 LAMMPS Kokkos translation units.

The wrapper already supports `--host-only`, which retains the configured host
compiler and removes CUDA-only flags. Selectively applying it, or introducing
separate host and CUDA targets/languages, could avoid device preprocessing for
independent host sources while retaining all packages. The 1,767 non-KOKKOS
files above are an investigation set, not an established safe allowlist.

This requires a dependency-based boundary. The bundled
[`Kokkos_Setup_Cuda.hpp`](https://github.com/lammps/lammps/blob/d291d572cf3c73a8a720774b78482a7c931304ca/lib/kokkos/core/src/setup/Kokkos_Setup_Cuda.hpp#L12)
rejects a non-CUDA compiler when CUDA is enabled. Generated style registration
files also include Kokkos classes. Keep these and any other Kokkos-dependent
units on the device compiler, preserve ABI/compile definitions, and validate
the combined library. Merely changing `CMAKE_CXX_COMPILER` is insufficient:
Kokkos can automatically install a launcher that redirects it to NVCC again.

## 3. Reduce unnecessary template instantiation

There are concrete small source candidates, rather than a reason to remove
Kokkos styles or supported runtime choices:

- [`pair_kokkos.h:110`](https://github.com/lammps/lammps/blob/d291d572cf3c73a8a720774b78482a7c931304ca/src/KOKKOS/pair_kokkos.h#L110)
  computes `need_dup` from `std::is_same_v`, then uses an ordinary `if` around
  `Kokkos::Experimental::contribute`. An `if constexpr` can discard the unused
  dependent calls during instantiation. Its benefit depends on how much work
  the compiler already avoids and needs measurement.
- [`pair_table_rx_kokkos.cpp:298`](https://github.com/lammps/lammps/blob/d291d572cf3c73a8a720774b78482a7c931304ca/src/KOKKOS/pair_table_rx_kokkos.cpp#L298)
  gives the `ev_tally` helper a `TABSTYLE` template parameter that is unused by
  its signature or body. Removing that parameter would let the four table
  styles share that helper specialization. It would not remove the distinct
  table-interpolation kernels. This translation unit took 343.34 seconds in
  the parallel benchmark, but the removable helper's share is unknown.

Both candidates need compilation and numerical regression, and any adopted
change should also retain runtime performance. There is no basis yet to claim
a percentage saving from either one.

## Other options and their limits

- NVCC 12.9.1 supports `--split-compile=N` to parallelize device optimization.
  Test a bounded value such as two threads together with a reduced outer job
  count and measured peak memory. The current wrapper forwards an unrecognized
  `--split-compile=2` to GCC via `-Xcompiler`, as verified in `--show` mode;
  it needs proper NVCC option forwarding or a scoped `NVCC_APPEND_FLAGS`.
- Installed NVCC help says `--threads` is ignored when compiling for one
  architecture. The current SM70-only build therefore has no expected benefit
  from simply adding `--threads=2`.
- `--Ofast-compile=min` is supported by the compiler and wrapper, but it disables
  some device optimizations. It needs application performance measurements and
  should rank below eliminating redundant work.
- Final shared-library linking took only 34.98 seconds. Changing the linker
  cannot account for the roughly hour-long build. Global unity builds or
  precompiled headers would need separate CUDA/translation-unit compatibility
  work; there is no evidence yet to prioritize them over the findings above.

Recommended sequence: first test native dependency generation, then selective
host compilation, then narrowly scoped template changes. Keep each change
separate so clean-build time, memory, feature parity and scientific behavior
can be attributed to it.
