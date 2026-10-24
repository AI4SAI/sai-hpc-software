#!/usr/bin/env bash
# Administrator-driven deployment; default only verifies accepted sources.
set -euo pipefail
umask 022
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo=$(cd -- "$package/../.." && pwd -P)
control=$repo/controller
[[ ! -f "$package/controller/sif_modules.py" ]] || control=$package/controller
snapshot=$package/snapshot.json
[[ ! -f "$package/payload/catalog.json" ]] || snapshot=$package/payload/catalog.json
case "${1:---check}" in
  --check)
    exec python3 -B "$control/sif_modules.py" "$snapshot"
    ;;
  --install)
    destination=${2:-/opt/apps/sai-sif/20261024}
    exec python3 -B "$control/sif_modules.py" "$snapshot" \
      --destination "$destination" --copy-images --module-root /opt/modules/modulefiles/apps
    ;;
  *) echo "usage: bash install.sh [--check | --install [new-destination]]" >&2; exit 2 ;;
esac
