#!/usr/bin/env bash
set -euo pipefail
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec python3 "$package/install.py" "$@"
