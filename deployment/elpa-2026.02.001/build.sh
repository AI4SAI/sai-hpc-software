#!/usr/bin/env bash
# Same upstream release and GPU features as the site's existing GNU ELPA.
set -euo pipefail
umask 022
isa=${1:?avx2, avx512 or dsprhbm}
source /control/environment.sh "$isa"
for c in gcc g++ gfortran; do [[ "$(/usr/bin/$c -dumpfullversion)" == 13.3.0 ]]; done
archive=/input/elpa-2026.02.001.tar.gz
printf '%s  %s\n' a379f27f4dbd27b2ee45017afec656d064301e97150c874649bdfd64957b75ed "$archive" | sha256sum -c
[[ ! -e /workspace/source && ! -e "$ELPA_ROOT/lib/libelpa_openmp.so" ]]
mkdir -p /workspace/source /workspace/build "$ELPA_ROOT/share/sai/reference"
tar --no-same-owner --strip-components=1 -xzf "$archive" -C /workspace/source
patch --batch --fuzz=0 -p1 -d /workspace/source < /control/cuda-aware-cpu.patch
# ELPA upstream PR #85, merged 2026-10-15, is absent from this release archive.
python3 - <<'PY'
import hashlib, json, subprocess
from pathlib import Path
source = Path('/workspace/source/src/GPU/check_for_gpu_template.F90')
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
before = digest(source)
assert before == 'd8b569137da84ad080c3646a57537290a315fb407adb3131558fee66632798a7'
patch = Path('/control/mpi-communicator.patch')
with patch.open('rb') as stream:
    subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', '/workspace/source'], stdin=stream, check=True)
after = digest(source)
assert after == 'af18210afa4781c58144941d1f5fef6b4807afdf4eac57bc5e6b7fd6463c9741'
import os
out = Path(os.environ['ELPA_ROOT'])/'share/sai/mpi-communicator-fix.json'
out.write_text(json.dumps(dict(upstream='https://github.com/marekandreas/elpa/pull/85',
    upstream_commit='20be982db53a671b9aac866a726aebe153a766e6',
    source_sha256=before, patched_sha256=after, patch_sha256=digest(patch)), indent=2)+'\n')
PY
flags='-O2 -g -fno-omit-frame-pointer -fopenmp -fno-lto'
case "$isa" in
  avx2) flags+=' -march=x86-64-v3 -mtune=znver3 -mno-avx512f'; avx512=no ;;
  avx512) flags+=' -march=x86-64-v4 -mtune=znver4'; avx512=yes ;;
  dsprhbm) flags+=' -march=x86-64-v4 -mtune=sapphirerapids'; avx512=yes ;;
esac
cuda=/opt/devtools/nvidia/cuda-12.9.1
hcoll=/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll
gpu_options=(--enable-nvidia-gpu-kernels=yes --with-cuda-path="$cuda"
  --with-NVIDIA-GPU-compute-capability=sm_70,sm_75,sm_80,sm_86,sm_89
  --enable-nvidia-cub --with-cusolver --enable-cuda-aware-mpi
  --enable-nvidia-sm80-gpu --enable-nvidia-sm80-gpu-kernels)
cuda_ldflags=" -Wl,-rpath,$cuda/lib64"
if [[ "$elpa_backend" == cpu ]]; then
  gpu_options=(--enable-nvidia-gpu-kernels=no --enable-nvidia-sm80-gpu-kernels=no
    --enable-amd-gpu-kernels=no --enable-intel-gpu-sycl-kernels=no
    --disable-cuda-aware-mpi --without-cusolver --disable-nvidia-cub)
  cuda_ldflags=
fi
options=(--prefix="$ELPA_ROOT" --libdir="$ELPA_ROOT/lib"
  --enable-openmp=yes --enable-static=no --enable-shared=yes
  --disable-c-tests --disable-cpp-tests --with-test-programs=no --disable-doxygen-doc
  --enable-avx-kernels=yes --enable-avx2-kernels=yes --enable-avx512-kernels="$avx512"
  "${gpu_options[@]}"
  CC="$MPI_HOME/bin/mpicc" CXX="$MPI_HOME/bin/mpicxx" FC="$MPI_HOME/bin/mpifort" CPP='cpp -E'
  CFLAGS="$flags" CXXFLAGS="$flags" FCFLAGS="$flags"
  LDFLAGS="-Wl,--allow-multiple-definition -Wl,--enable-new-dtags -L$OPENBLAS_ROOT/lib -Wl,-rpath,$OPENBLAS_ROOT/lib -Wl,-rpath,$hcoll/lib$cuda_ldflags -lstdc++"
  LIBS='-lscalapack -lopenblas'
  SCALAPACK_LDFLAGS="-L$OPENBLAS_ROOT/lib" SCALAPACK_FCFLAGS="-I$OPENBLAS_ROOT/include")
cp /input/reference/* "$ELPA_ROOT/share/sai/reference/"
cp /control/{build.sh,environment.sh,smoke.c,verify.sh,cuda-aware-cpu.patch,mpi-communicator.patch,comm-trace.c,verify-lifecycle.py} "$ELPA_ROOT/share/sai/"
printf '%s\n' "$flags" > "$ELPA_ROOT/share/sai/compiler-flags.txt"
printf '%s\n' "$elpa_isa" > "$ELPA_ROOT/share/sai/isa"
printf '%s\n' "$elpa_backend" > "$ELPA_ROOT/share/sai/backend"
printf '%q ' /workspace/source/configure "${options[@]}" > "$ELPA_ROOT/share/sai/configure-command.txt"
printf '\n' >> "$ELPA_ROOT/share/sai/configure-command.txt"
lscpu > "$ELPA_ROOT/share/sai/build-lscpu.txt"
module -t list > "$ELPA_ROOT/share/sai/modules.txt" 2>&1
sha256sum "$archive" /control/* > "$ELPA_ROOT/share/sai/recipe.sha256"
cd /workspace/build
echo "ELPA_CONFIGURE_START $isa $(date -Is)"
/workspace/source/configure "${options[@]}" > configure.log 2>&1 || { tail -n 80 configure.log; exit 1; }
echo "ELPA_MAKE_START $isa jobs=$BUILD_JOBS $(date -Is)"
# Upstream builds its unused test helper library even with test-programs=no;
# that helper races the generation of elpa.mod. Build the actual deliverables
# first, and omit uninstalled test libraries from the install target.
make -j "$BUILD_JOBS" libelpa_openmp.la elpa2_print_kernels_openmp > make.log 2>&1 || { tail -n 100 make.log; exit 1; }
make install noinst_LTLIBRARIES= noinst_PROGRAMS= > install.log 2>&1 || { tail -n 80 install.log; exit 1; }
# The site's MPI libtool records contain a historical HCOLL alias. Keep the
# real SDK path at runtime, including in the installed development metadata.
python3 - "$ELPA_ROOT" "$hcoll" "$elpa_backend" <<'PY'
import json, re, subprocess, sys
from pathlib import Path
p, hcoll, backend = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
so = (p/'lib/libelpa_openmp.so').resolve()
dynamic = subprocess.check_output(['readelf','-d',str(so)], text=True)
paths = re.findall(r'\((?:RUNPATH|RPATH)\).*?\[(.*?)\]', dynamic)
assert len(paths) == 1
retained = [s for s in paths[0].split(':') if not s.startswith('/opt/mellanox/hcoll')]
subprocess.run(['chrpath','-r',':'.join(retained),str(so)],check=True)
la = p/'lib/libelpa_openmp.la'
la.write_text(la.read_text().replace('/opt/mellanox/hcoll', hcoll))
# Upstream's .pc lists -lcudart et al. but omits CUDA's -L directory. Preserve
# the final physical CUDA path so a fresh pkg-config consumer links correctly.
pc = p/'lib/pkgconfig/elpa_openmp.pc'
text = pc.read_text()
assert text.count('Libs: ') == 1
if backend == 'nvidia':
    text = text.replace('Libs: ', 'Libs: -L/opt/devtools/nvidia/cuda-12.9.1/lib64 -Wl,-rpath,/opt/devtools/nvidia/cuda-12.9.1/lib64 ')
else:
    assert not re.search(r'lib(?:cuda|cublas|cusolver)|-l(?:cuda|cublas|cusolver)', dynamic + text)
pc.write_text(text)
(p/'share/sai/runtime-paths.json').write_text(json.dumps(dict(before=paths, after=retained),indent=2)+'\n')
PY
cp config.log config.h "$ELPA_ROOT/share/sai/"
[[ -e "$ELPA_ROOT/include/elpa" ]] || ln -s elpa_openmp-2026.02.001/elpa "$ELPA_ROOT/include/elpa"
echo "ELPA_INSTALLED $isa $(date -Is)"
# Keep the installed library, headers/modules, pkg-config metadata and docs.
# Source, object files and temporary test executables stay in the home run.
bash /control/create_rootfs.sh /workspace/export
mkdir -p "/workspace/export$(dirname "$ELPA_ROOT")"
cp -a "$ELPA_ROOT" "/workspace/export$ELPA_ROOT"
chmod -R a+rX,go-w /workspace/export
mksquashfs /workspace/export /workspace/elpa.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS" > /workspace/pack.log 2>&1
echo "ELPA_PACKED $isa $(date -Is)"
