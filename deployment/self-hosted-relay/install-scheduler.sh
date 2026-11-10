#!/usr/bin/env bash
set -euo pipefail

[ "$(id -un)" = sai ] || { echo 'Run as the dedicated sai account' >&2; exit 1; }
case "${1:---prepare}" in
    --prepare|--install) mode=${1:---prepare} ;;
    *) echo 'Usage: install-scheduler.sh [--prepare|--install]' >&2; exit 2 ;;
esac
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
umask 077
mkdir -p /home/sai/.local/lib/sai-hpc-scheduler /home/sai/.local/state/sai-hpc-scheduler /home/sai/.config/systemd/user
install -m 644 "$source_dir/dispatch_daily.py" /home/sai/.local/lib/sai-hpc-scheduler/dispatch_daily.py
for name in sai-hpc-scheduler.service sai-hpc-scheduler.timer; do
    install -m 644 "$source_dir/$name" "/home/sai/.config/systemd/user/$name"
done
systemctl --user daemon-reload
if [ "$mode" = --prepare ]; then
    echo 'Scheduler files prepared; timer enablement is unchanged.'
    exit 0
fi

if [ ! -f /home/sai/.config/sai-hpc-scheduler/token ]; then
    python3 - <<'PY'
from pathlib import Path
import getpass, os
path = Path('/home/sai/.config/sai-hpc-scheduler/token')
path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
token = getpass.getpass('Repository Actions read/write token (hidden): ').strip()
if not token or any(c.isspace() for c in token):
    raise SystemExit('No valid token supplied; timer not enabled')
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(token + '\n')
PY
fi
python3 /home/sai/.local/lib/sai-hpc-scheduler/dispatch_daily.py --mode check
systemctl --user enable --now sai-hpc-scheduler.timer
systemctl --user start sai-hpc-scheduler.service
systemctl --user list-timers sai-hpc-scheduler.timer --no-pager
