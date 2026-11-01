#!/usr/bin/env bash
set -euo pipefail
package_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
bash "$package_dir/install.sh"
output_root=/org/org-open-sai/proj-sai-hpc-software/runtime-tests/nepadapters-installed-$(date +%Y%m%d-%H%M%S)-$$
for partition in $(/usr/bin/python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["partitions"]))' "$package_dir/release.json"); do
    /usr/bin/python3 -B "$package_dir/control/nepadapters_native.py" installed \
        "$package_dir" "$output_root/$partition" --partition "$partition"
done
printf 'Validation output: %s\n' "$output_root"
printf 'After both jobs per partition finish: python3 -B %s/control/nepadapters_native.py verify OUTPUT/PARTITION\n' "$package_dir"
