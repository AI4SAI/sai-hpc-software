#!/usr/bin/env bash
# CP2K toolchain's ELPA pin/patch, installed at this delivery's final prefix.
set -euo pipefail
source_tree=${1:?pinned CP2K source required}
archive_dir=${2:?verified source archive directory required}
work=${3:?ELPA build directory required}
prefix=${4:?ELPA final dependency prefix required}
target=${5:?CP2K target required}
jobs=${BUILD_JOBS:-8}
toolchain="$source_tree/tools/toolchain/scripts/stage5/install_elpa.sh"
version=$(sed -nE 's/^elpa_ver="([0-9.]+)"$/\1/p' "$toolchain")
upstream_sha=$(sed -nE 's/^elpa_sha256="([0-9a-f]{64})"$/\1/p' "$toolchain")
[[ "$version" == 2026.02.002 && "$upstream_sha" == 02e3c59fec53cd8eb66a4cc15fa4fbf190cf9654227223fb8ad55713e9427932 ]] || {
  echo "unsupported CP2K toolchain ELPA pin: $version $upstream_sha" >&2; exit 1;
}
[[ "$prefix" == "${INSTALL_PREFIX:?}/dependencies/elpa" && "$prefix" == /opt/apps/cp2k/* ]]
[[ "$work" == /workspace/* && "$jobs" =~ ^[1-9][0-9]*$ ]]
case "$target" in
  dsprhbm) gpu=no ;;
  4v100-avx512|16v100-avx2|8v100v0-avx512) gpu=yes ;;
  *) echo "unsupported ELPA target: $target" >&2; exit 2 ;;
esac
avx512=no
if grep -qw avx512f /proc/cpuinfo; then avx512=yes; fi
if [[ "$target" == 16v100-avx2 ]]; then [[ "$avx512" == no ]]; fi
grep -qw avx2 /proc/cpuinfo
archive="$archive_dir/elpa-$version.tar.gz"
patch_file="$source_tree/tools/toolchain/scripts/stage5/elpa-$version-library-only.patch"
printf '%s  %s\n' "$upstream_sha" "$archive" | sha256sum --check --status
[[ ! -e "$work/source" && ! -e "$prefix/lib/libelpa_openmp.so" ]]
mkdir -p "$work/source" "$work/build" "$prefix/share/sai"
# /usr is bound from the host, but /etc/alternatives is deliberately not.
# ELPA's generated Makefile invokes literal awk as well as configure's AWK.
# Supply a build-local alias to the real host binary, not its dangling link.
mkdir -p "$work/tools"
for candidate in /usr/bin/gawk /usr/bin/mawk /usr/bin/awk; do
  if [[ -x "$candidate" ]] && "$candidate" 'BEGIN { exit 0 }'; then
    ln -s "$candidate" "$work/tools/awk"
    export AWK="$work/tools/awk" PATH="$work/tools:$PATH"
    sha256sum "$candidate" > "$prefix/share/sai/awk-build-tool.sha256"
    break
  fi
done
[[ -x "$work/tools/awk" ]] || { echo "ELPA requires a working awk" >&2; exit 1; }
sha256sum "$toolchain" "$archive" "$patch_file" > "$prefix/share/sai/source-archives.sha256"
tar --no-same-owner --strip-components=1 -xzf "$archive" -C "$work/source"
patch --batch --forward -l -d "$work/source" -p1 < "$patch_file"
# Upstream ELPA PR #85: repeated GPU diagonalization otherwise leaks an MPI
# communicator per call. The 2026.02.002 archive predates this two-line fix.
python3 - "$work/source/src/GPU/check_for_gpu_template.F90" "$prefix/share/sai" <<'PY'
from pathlib import Path
import hashlib, json, sys
path, metadata = Path(sys.argv[1]), Path(sys.argv[2])
before = path.read_bytes()
anchor = b'      OBJECT%mpi_setup%nRanks_comm_parent_per_node = int(np_total_per_nodeMPI, kind=ik)\n'
if before.count(anchor) != 1 or b'call mpi_comm_free(mpi_comm_all_per_nodeMPI, mpierr)' in before:
    raise ValueError('unexpected ELPA communicator setup; review upstream patch')
after = before.replace(anchor, anchor + b'\n      call mpi_comm_free(mpi_comm_all_per_nodeMPI, mpierr)\n')
path.write_bytes(after)
(metadata / 'mpi-communicator-fix.json').write_text(json.dumps(dict(
    upstream='https://github.com/marekandreas/elpa/pull/85',
    upstream_commit='20be982db53a671b9aac866a726aebe153a766e6',
    source_sha256=hashlib.sha256(before).hexdigest(),
    patched_sha256=hashlib.sha256(after).hexdigest()), sort_keys=True) + '\n')
PY
# Same complex-number compatibility edits as the pinned CP2K toolchain.
sed -i 's/creal(/__real__(/g; s/cimag(/__imag__(/g' "$work/source/src/GPU/CUDA/cudaFunctions_template.h"
mpi=${MPI_HOME:?native site MPI required}
blas=${OPENBLAS_ROOT:?native site BLAS required}
cuda=/opt/devtools/nvidia/cuda-12.9.1
for compiler in CC CXX FC; do
  value=${!compiler}
  [[ "$value" == /opt/devtools/gcc/14.3.0/bin/* && "$("$value" -dumpfullversion)" == 14.3.0 ]]
done
export OMPI_CC="$CC" OMPI_CXX="$CXX" OMPI_FC="$FC"
flags='-O3 -march=native -mtune=native -fno-lto'
# CUDA 12.9 has the same GCC-14 compatibility switch as CP2K's toolchain.
options=(--prefix="$prefix" --libdir="$prefix/lib" --enable-openmp=yes
  --enable-shared=yes --disable-static --with-test-programs=no
  --disable-c-tests --disable-cpp-tests --disable-doxygen-doc
  --enable-avx-kernels=yes --enable-avx2-kernels=yes --enable-avx512-kernels="$avx512"
  --enable-nvidia-gpu-kernels="$gpu" --with-cuda-path="$cuda"
  --with-NVIDIA-GPU-compute-capability=sm_70 --without-cusolver)
printf 'CP2K_NATIVE_ELPA version=%s target=%s avx512=%s gpu=%s prefix=%s\n' "$version" "$target" "$avx512" "$gpu" "$prefix"
cd "$work/build"
printf '%q ' "$work/source/configure" "${options[@]}" > "$prefix/share/sai/configure-command.txt"
printf '\n' >> "$prefix/share/sai/configure-command.txt"
"$work/source/configure" "${options[@]}" \
  CC="$mpi/bin/mpicc" CXX="$mpi/bin/mpicxx" FC="$mpi/bin/mpifort" CPP='cpp -E' \
  CFLAGS="$flags" CXXFLAGS="$flags" FCFLAGS="$flags" \
  CUDA_CFLAGS='-std=c++14 -allow-unsupported-compiler' OMPI_MCA_plm_rsh_agent=/bin/false \
  LDFLAGS="-Wl,--allow-multiple-definition -Wl,--enable-new-dtags -L$blas/lib -Wl,-rpath,$blas/lib -lstdc++" \
  LIBS='-lscalapack -lopenblas' > configure.log 2>&1 || { tail -80 configure.log; exit 1; }
make -j "$jobs" > make.log 2>&1 || { tail -80 make.log; exit 1; }
make install > install.log 2>&1 || { tail -80 install.log; exit 1; }
cp config.log config.h "$prefix/share/sai/"
printf '%s\n' "$version" > "$prefix/share/sai/version"
printf '%s\n' "$target" > "$prefix/share/sai/target"
printf '%s\n' "$flags" > "$prefix/share/sai/compiler-flags"
lscpu > "$prefix/share/sai/build-lscpu.txt"
ldd "$prefix/lib/libelpa_openmp.so" | tee "$prefix/share/sai/ldd.txt"
! grep -q 'not found' "$prefix/share/sai/ldd.txt"
sha256sum "$prefix/lib/"*.so.* > "$prefix/share/sai/libraries.sha256"
echo CP2K_NATIVE_ELPA_INSTALLED
