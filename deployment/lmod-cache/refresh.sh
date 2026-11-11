#!/bin/bash
# Refresh the configured system spider cache with the installed upstream tool.
set -eo pipefail

case "${1:---check}" in
    --check|--refresh) sai_cache_action=${1:---check} ;;
    --help|-h)
        printf 'Usage: bash refresh.sh [--check|--refresh]\nRun --refresh as the cache owner or an authorized administrator.\n'
        exit 0 ;;
    *) printf 'Usage: bash refresh.sh [--check|--refresh]\n' >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || exit 2

sai_cache_dir=/opt/modules/lmod/cache
sai_cache_tool=/opt/modules/lmod/9.2.4/libexec/update_lmod_system_cache_files
sai_module_roots=(
    /opt/modules/modulefiles/apps
    /opt/modules/modulefiles/devtools
    /opt/modules/modulefiles/compiler
    /opt/modules/modulefiles/mathlib
    /opt/modules/modulefiles/mpi
)
[[ -x "$sai_cache_tool" ]]
for sai_root in "${sai_module_roots[@]}"; do [[ -d "$sai_root" ]]; done
stat -c '%A %U:%G %y %n' "$sai_cache_dir" "$sai_cache_dir/system.txt"
if [[ ! -w "$sai_cache_dir" ]]; then
    printf 'Cannot write %s as %s. Run as entropy/root or obtain cache-maintenance write access.\n' \
        "$sai_cache_dir" "$(id -un)" >&2
    exit 1
fi
# The upstream helper copies the previous cache over its existing backup.
for sai_backup in "$sai_cache_dir"/spiderT.old.lua "$sai_cache_dir"/spiderT.old.luac_*; do
    if [[ -e "$sai_backup" && ! -w "$sai_backup" ]]; then
        printf 'Cannot update existing cache backup: %s\n' "$sai_backup" >&2
        exit 1
    fi
done
if [[ "$sai_cache_action" == --check ]]; then
    printf 'LMOD_CACHE_REFRESH_READY\n'
    exit 0
fi

source /etc/profile.d/lmod.sh
sai_modulepath=$(IFS=:; printf '%s' "${sai_module_roots[*]}")
"$sai_cache_tool" -d "$sai_cache_dir" -t "$sai_cache_dir/system.txt" "$sai_modulepath"
printf 'LMOD_SYSTEM_CACHE_REFRESHED\n'
