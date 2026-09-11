#!/usr/bin/env bash
set -euo pipefail
op=$1; software=$2; sha=$3; version=$4; target=$5
[[ "$software" == abacus && "$sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$version" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]
[[ "$target" == dsprhbm || "$target" == 4v100-avx512 || "$target" == 16v100-avx2 || "$target" == a100 ]]
export PATH=/usr/bin:/bin TMPDIR=/workspace/tmp
export INSTALL_PREFIX="/opt/software/$software/$version/$target"
metadata() {
    mkdir -p "$INSTALL_PREFIX/share/sai"
    printf '%s\n' "$sha" > "$INSTALL_PREFIX/share/sai/source-sha"
    printf '%s\n' "$target" > "$INSTALL_PREFIX/share/sai/target"
    module -t list > "$INSTALL_PREFIX/share/sai/modules.txt" 2>&1
    lscpu > "$INSTALL_PREFIX/share/sai/hardware.txt"
    env | sort | grep -E '^(CUDA|ELPA|MPI|OMPI|OPAL|OPENBLAS|PMIX|ScaLAPACK)_' \
      > "$INSTALL_PREFIX/share/sai/resolved-environment.txt" || true
    cp /workspace/build/CMakeCache.txt "$INSTALL_PREFIX/share/sai/"
    cp /control/abacus_features.py /control/abacus_dependency_lock.json "$INSTALL_PREFIX/share/sai/"
    # %q serializes values as literals; no module initialization is necessary
    # when inspecting/running the final, read-only SIF.
    {
        printf '%s\n' '# Source this file for either the SIF or an exported /opt installation.' \
          'sai_abacus_prefix=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)'
        for key in PATH LD_LIBRARY_PATH LIBRARY_PATH OPAL_PREFIX PMIX_INSTALL_PREFIX MPI_HOME OMPI_HOME; do
            if [[ -v "$key" ]]; then printf 'export %s=%q\n' "$key" "${!key}"; fi
        done
        printf '%s\n' 'export PATH="$sai_abacus_prefix/bin:$PATH"' \
          'export LD_LIBRARY_PATH="$sai_abacus_prefix/dependencies/libtorch/lib:$sai_abacus_prefix/dependencies/nep/lib:${LD_LIBRARY_PATH:-}"' \
          'unset sai_abacus_prefix'
    } > "$INSTALL_PREFIX/share/sai/runtime-env.sh"
}
case "$op" in
  build)
    mkdir -p /workspace/tmp
    test ! -e /home/stardust/.ssh
    test ! -w /input/repository
    test ! -w /control
    test ! -w /opt/devtools
    git init /workspace/source
    # Git's local fetch transports its shallow boundary correctly (unlike a
    # shallow .bundle); the immutable host cache still retains full ancestry.
    git -C /workspace/source -c core.hooksPath=/dev/null fetch --depth=1 --no-tags /input/repository "$sha"
    git -C /workspace/source -c core.hooksPath=/dev/null checkout --detach "$sha"
    test "$(git -C /workspace/source rev-parse HEAD)" = "$sha"
    source /control/environment.sh "$target"
    module -t list 2>&1
    bash /control/abacus_build.sh "$target"
    metadata
    ;;
  metadata)
    source /control/environment.sh "$target"
    test -x "$INSTALL_PREFIX/bin/abacus"
    metadata
    ;;
  export)
    # Staging and squashfs creation stay within the ext3 image.
    # These two fixed paths are INSIDE the container, including on a repack.
    rm -rf -- /workspace/export
    rm -f -- /workspace/final.squashfs
    bash /control/create_rootfs.sh /workspace/export
    cp -a /opt/software /workspace/export/opt/software
    mksquashfs /workspace/export /workspace/final.squashfs -noappend -all-root -no-xattrs -processors "$BUILD_JOBS"
    ;;
  verify)
    source "$INSTALL_PREFIX/share/sai/runtime-env.sh"
    test "$(cat "$INSTALL_PREFIX/share/sai/source-sha")" = "$sha"
    info=$("$INSTALL_PREFIX/bin/abacus" --info)
    printf '%s\n' "$info"
    dependencies=$(ldd "$INSTALL_PREFIX/bin/abacus")
    printf '%s\n' "$dependencies"
    if [[ "$dependencies" == *"not found"* ]]; then exit 1; fi
    if [[ "$target" != dsprhbm ]]; then
        for feature in CUSOLVERMP CUBLASMP NCCL_PARALLEL_DEVICE; do
            grep -qx "ENABLE_${feature}:BOOL=ON" "$INSTALL_PREFIX/share/sai/CMakeCache.txt"
        done
        grep -Eq 'CUSOLVERMP Support:[[:space:]]+yes' <<< "$info"
        grep -Eq 'libcusolverMp.so.*=> /opt/devtools/nvidia/mp_libs/lib/' <<< "$dependencies"
        grep -Eq 'libcublasmp.so.*=> /opt/devtools/nvidia/mp_libs/lib/' <<< "$dependencies"
        grep -Eq 'libnccl.so.*=> /opt/devtools/nvidia/nccl_' <<< "$dependencies"
    fi
    # Required compile-time features and actual dynamic loader paths are both
    # checked. Scientific parity and speed remain separate benchmark evidence.
    python3 "$INSTALL_PREFIX/share/sai/abacus_features.py" "$INSTALL_PREFIX" "$target"
    if touch "$INSTALL_PREFIX/.write-test" 2>/dev/null; then echo 'artifact must be read-only' >&2; exit 1; fi
    ;;
  *) exit 2;;
esac
