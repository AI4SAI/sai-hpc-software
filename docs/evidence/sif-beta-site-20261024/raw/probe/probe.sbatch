#!/bin/bash
#SBATCH --time=00:05:00
source /etc/profile.d/lmod.sh
module use /home/stardust/sai-hpc-software/modulefiles/apps
module load deepmd-kit/master-beta
export TMPDIR="$PWD/tmp"
mkdir -p "$TMPDIR"
deepmd-python -c 'import importlib.util, json, torch, deepmd; print(json.dumps({"deepmd":deepmd.__file__, "torch":torch.__version__, "cuda":torch.cuda.is_available(), "devices":torch.cuda.device_count(), "nccl":torch.distributed.is_nccl_available(), "horovod":importlib.util.find_spec("horovod") is not None}))'
