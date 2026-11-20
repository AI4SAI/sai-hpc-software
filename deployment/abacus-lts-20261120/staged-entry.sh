#!/usr/bin/env bash
# Maintainer probe only: load the public module against staged native files.
set -eo pipefail
source /etc/profile.d/lmod.sh
module purge
module use /staged-modules
module load abacus/LTS-20261120-auto
test "$(command -v abacus)" = "$SAI_ABACUS_PREFIX/bin/abacus"
module unload abacus/LTS-20261120-auto
module load abacus/LTS-20261120-auto
test "$(command -v abacus)" = "$SAI_ABACUS_PREFIX/bin/abacus"
case "$1" in
    version) exec abacus --version ;;
    libraries) exec /usr/bin/ldd "$SAI_ABACUS_PREFIX/bin/abacus" ;;
    science) exec abacus ;;
    *) exit 2 ;;
esac
