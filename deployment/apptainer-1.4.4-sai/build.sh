#!/usr/bin/env bash
# Build a non-setuid runtime into a staging tree; never changes /opt.
set -euo pipefail
bundle=${1:?official apptainer-1.4.4.tar.gz}
work=${2:?empty project build directory}
case "$work" in /org/org-open-sai/proj-sai-hpc-software/*) ;; *) exit 2;; esac
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
[[ ! -e "$work" ]]
mkdir -p "$work"/{tmp,gocache,gopath,stage}
export TMPDIR="$work/tmp" GOCACHE="$work/gocache" GOPATH="$work/gopath"
export PATH=/opt/devtools/golang/1.25.4/bin:/usr/bin:/bin
export GOPROXY=off GOTOOLCHAIN=local
printf '%s  %s\n' eb806e22dabfb6549c398b55e50c747e4c51b57f8879da9e29813de40af54b48 "$bundle" | sha256sum -c -
tar -xzf "$bundle" -C "$work"
cd "$work/apptainer-1.4.4"
patch -p1 < "$here/graceful-driver-stop.patch"
cp "$here/graceful_stop_test.go" internal/pkg/image/driver/sai_graceful_stop_test.go
./mconfig --without-suid --prefix=/opt/devtools/apptainer/1.4.4
make -C builddir -j "${SLURM_CPUS_PER_TASK:-8}"
go test -v ./internal/pkg/image/driver -run TestSAI
make -C builddir DESTDIR="$work/stage" install
"$work/stage/opt/devtools/apptainer/1.4.4/bin/apptainer" --version
find "$work/stage" -type f -perm /6000 -print -quit | (! read -r _)
cd "$work/stage"
tar -czf "$work/apptainer-1.4.4-sai.tar.gz" opt
sha256sum "$work/apptainer-1.4.4-sai.tar.gz"
