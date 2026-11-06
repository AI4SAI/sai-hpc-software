#!/usr/bin/env bash
set -euo pipefail

[ "$(id -un)" = sai ] || { echo 'Run as the dedicated sai account' >&2; exit 1; }
base=/home/sai/ci
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
umask 077
mkdir -p "$base"/{runner,bin,work/home,tmp,ssh,deployment,cache/repositories,state}

download() {
    local url=$1 digest=$2 output=$3
    if [ ! -f "$output" ]; then curl -fL --retry 3 "$url" -o "$output"; fi
    printf '%s  %s\n' "$digest" "$output" | sha256sum -c -
}

if [ ! -x "$base/runner/bin/Runner.Listener" ]; then
    archive="$base/tmp/actions-runner-2.338.2.tar.gz"
    download https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-linux-x64-2.338.2.tar.gz \
        baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40 "$archive"
    tar -xzf "$archive" -C "$base/runner"
fi
if [ ! -x "$base/bin/gh" ]; then
    archive="$base/tmp/gh-2.103.3.tar.gz"
    download https://github.com/cli/cli/releases/download/v2.103.3/gh_2.103.3_linux_amd64.tar.gz \
        4c806978ffce1cec70853302a56d70ac6da4f19bae7adca4e1a8ab00633dd91d "$archive"
    tar -xOzf "$archive" gh_2.103.3_linux_amd64/bin/gh > "$base/bin/gh"
    chmod 755 "$base/bin/gh"
fi
if [ ! -f "$base/runner/.runner" ]; then
    IFS= read -r registration_token
    [ -n "$registration_token" ]
    (cd "$base/runner" && ./config.sh --unattended --url https://github.com/AI4SAI/sai-hpc-software \
        --token "$registration_token" --name do2-sai-hpc --labels sai-hpc-relay --work "$base/work")
    unset registration_token
fi
install -m 644 "$source_dir/sandbox.py" "$base/deployment/sandbox.py"
mkdir -p /home/sai/.config/systemd/user
install -m 644 "$source_dir/sai-hpc-runner.service" /home/sai/.config/systemd/user/sai-hpc-runner.service
systemctl --user daemon-reload
systemctl --user enable --now sai-hpc-runner.service
systemctl --user status sai-hpc-runner.service --no-pager
