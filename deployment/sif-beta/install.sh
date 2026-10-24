#!/usr/bin/env bash
# Install stable forwarding modules once; stardust rotates their home targets.
set -euo pipefail
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
action=${1:---check}
destination=${2:-/opt/modules/modulefiles/apps}
[[ $action == --check || $action == --install ]] || { echo 'usage: bash install.sh [--check|--install [module-root]]' >&2; exit 2; }
[[ $destination == /* && $(realpath -m -- "$destination") == "$destination" ]] || { echo 'module-root must be absolute and canonical' >&2; exit 1; }
python3 -B "$package/check-public.py"

# Preflight all five entries before writing any public module.
for name in abacus cp2k lammps deepmd-kit gpumd; do
  branch=master
  [[ $name != abacus && $name != lammps ]] || branch=develop
  relative=$name/$branch-beta-later
  source=$package/modulefiles/$relative
  home_module=/home/stardust/sai-hpc-software/modulefiles/apps/$relative
  [[ -r $source && -r $home_module ]] || { echo "Missing readable module: $relative" >&2; exit 1; }
  [[ ! -L $destination/$name && ! -L $destination/$relative ]] || { echo "Redirected public module: $relative" >&2; exit 1; }
  if [[ -e $destination/$relative ]]; then
    cmp -- "$source" "$destination/$relative" || { echo "Existing different module: $relative" >&2; exit 1; }
  fi
  echo "$relative -> $home_module"
done

[[ $action == --install ]] || exit 0
for name in abacus cp2k lammps deepmd-kit gpumd; do
  branch=master
  [[ $name != abacus && $name != lammps ]] || branch=develop
  relative=$name/$branch-beta-later
  if [[ ! -e $destination/$relative ]]; then
    [[ -d $destination/$name ]] || install -d -m 0755 -- "$destination/$name"
    # No clobber: a concurrently installed module must not be overwritten.
    (set -o noclobber; umask 022; cat -- "$package/modulefiles/$relative" > "$destination/$relative")
    chmod 0444 -- "$destination/$relative"
  fi
done
echo 'Installed stable beta gateways. Daily rotation needs no /opt changes.'
echo 'Discover new names with: module --ignore_cache avail beta'
