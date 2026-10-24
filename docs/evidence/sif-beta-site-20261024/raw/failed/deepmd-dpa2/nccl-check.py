import json
import os
from pathlib import Path
import torch
import torch.distributed as dist

rank = int(os.environ['LOCAL_RANK'])
torch.cuda.set_device(rank)
dist.init_process_group('nccl')
value = torch.tensor([rank + 1.0], device=f'cuda:{rank}')
dist.all_reduce(value)
assert dist.get_world_size() == 2 and value.item() == 3.0
record = dict(rank=dist.get_rank(), local_rank=rank, world_size=dist.get_world_size(),
              backend=dist.get_backend(), device=str(value.device),
              gpu=torch.cuda.get_device_name(rank), result=value.item())
Path(f'nccl-rank{rank}.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record), flush=True)
dist.destroy_process_group()
