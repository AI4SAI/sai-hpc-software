#!/usr/bin/env bash
# Run after --install, as the ordinary cluster account that will own the jobs.
set -euo pipefail
package_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec /usr/bin/python3 -B "$package_dir/native_checks.py" submit "${1:-all}"
