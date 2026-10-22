#!/usr/bin/env bash
set -euo pipefail
[[ ${OMPI_COMM_WORLD_RANK:?} == 0 && ${OMPI_COMM_WORLD_SIZE:?} == 1 ]]
[[ -n ${CUDA_VISIBLE_DEVICES:-} && "$CUDA_VISIBLE_DEVICES" != *,* ]]
taskset -pc $$ > affinity.txt
lscpu -p=CPU,CORE,SOCKET > topology.txt
python3 - <<'PY'
import json,os,socket
with open('rank.json','w') as f:
    json.dump(dict(host=socket.gethostname(),rank=0,ranks=1,affinity=sorted(os.sched_getaffinity(0)),
                   gpu=os.environ['CUDA_VISIBLE_DEVICES'],omp_threads=os.environ['OMP_NUM_THREADS'],
                   mpi=os.environ['MPI_HOME'],image=os.environ['SAI_CP2K_IMAGE']),f,sort_keys=True)
PY
exec bash "${SAI_TUNING_LAUNCHER:?}" "$@"
