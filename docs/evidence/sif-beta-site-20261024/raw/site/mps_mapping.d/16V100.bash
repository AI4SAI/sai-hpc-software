#!/bin/bash
export GPUS_PER_SOCKET=2
export CORES_PER_SOCKET=8
source "$(dirname "${BASH_SOURCE[0]}")/_common.bash"
