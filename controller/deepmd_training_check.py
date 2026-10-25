#!/usr/bin/env python3
"""Observe real two-GPU training and restart; never change DDP options."""
import atexit
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def worker():
    import torch
    import torch.distributed as dist

    original = torch.nn.parallel.DistributedDataParallel.__init__
    reports = []

    def digest(model):
        result = hashlib.sha256()
        for name, parameter in model.named_parameters():
            result.update(name.encode())
            result.update(parameter.detach().cpu().contiguous().numpy().tobytes())
        return result.hexdigest()

    def observe(self, *args, **kwargs):
        original(self, *args, **kwargs)
        report = dict(rank=dist.get_rank(), world_size=dist.get_world_size(),
                      local_rank=int(os.environ['LOCAL_RANK']), pid=os.getpid(),
                      backend=dist.get_backend(), device_ids=self.device_ids,
                      cuda_current_device=torch.cuda.current_device(),
                      find_unused_parameters=self.find_unused_parameters,
                      initial_parameters_sha256=digest(self.module), forward_calls=0,
                      gradient_devices=[], gradients_finite=True, gradient_calls=0,
                      empty_trainable_parameters=[name for name, parameter in self.module.named_parameters()
                                                  if parameter.requires_grad and parameter.numel() == 0])
        reports.append(report)

        def forward(module, arguments):
            report['forward_calls'] += 1

        self.register_forward_pre_hook(forward)

        def gradient(grad):
            report['gradient_calls'] += 1
            report['gradient_devices'] = sorted(set(report['gradient_devices']) | {str(grad.device)})
            report['gradients_finite'] &= torch.isfinite(grad).all().item()
            return grad

        for parameter in self.module.parameters():
            if parameter.requires_grad and parameter.numel():
                parameter.register_hook(gradient)

        def finish():
            report['final_parameters_sha256'] = digest(self.module)
            write(f"rank-{report['rank']}.json", report)

        atexit.register(finish)

    torch.nn.parallel.DistributedDataParallel.__init__ = observe
    sys.argv = ['dp', '--pt', 'train', *sys.argv[2:]]
    runpy.run_module('deepmd', run_name='__main__')


def inventory():
    import deepmd
    import torch

    root = Path(deepmd.__file__).parent
    files = {}
    for relative in ('pt/train/training.py', 'pt/model/descriptor/se_a.py', 'tf/train/run_options.py'):
        path = root / relative
        content = path.read_text()
        lines = content.splitlines()
        indices = {j for i, line in enumerate(lines)
                   if any(key in line for key in ('find_unused_parameters', 'self.compress_info =',
                                                   'self.compress_data =', 'import horovod', 'Switch to serial'))
                   for j in range(max(0, i - 2), min(len(lines), i + 9))}
        files[relative] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                              excerpts=[f'{i + 1}: {lines[i]}' for i in sorted(indices)])
    versions = {}
    for package in ('deepmd-kit', 'torch', 'tensorflow', 'jax', 'triton', 'horovod', 'numpy'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return dict(python=sys.executable, deepmd=str(root), versions=versions,
                torch_cuda=torch.version.cuda, nccl=torch.cuda.nccl.version(),
                cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'),
                devices=[dict(index=i, name=torch.cuda.get_device_name(i)) for i in range(torch.cuda.device_count())],
                horovod=importlib.util.find_spec('horovod') is not None,
                ldconfig=Path('/sbin/ldconfig').exists(), nvcc=shutil.which('nvcc'),
                affinity=sorted(os.sched_getaffinity(0)), source=files)


def prepare(directory, descriptor):
    import numpy as np

    control = Path(__file__).resolve().parent
    fixture = json.loads((Path('/runtime/case') / 'fixture.json').read_text())
    data = directory / 'data'
    (data / 'set.000').mkdir(parents=True)
    np.savetxt(data / 'type.raw', fixture['atom_types'], fmt='%d')
    (data / 'type_map.raw').write_text('O\nH\n')
    for name, values in dict(coord=fixture['coordinates'], box=fixture['box'],
                             energy=[fixture['reference']['energy']], force=fixture['reference']['forces']).items():
        np.save(data / 'set.000' / (name + '.npy'),
                np.repeat(np.asarray(values, dtype=np.float64).reshape(1, -1), 8, axis=0))
    configuration = json.loads((control / 'deepmd_training_input.json').read_text())
    if descriptor == 'dpa2':
        configuration['model']['descriptor'] = json.loads((control / 'deepmd_training_dpa2.json').read_text())['model']['descriptor']
    configuration['learning_rate']['decay_steps'] = 4
    configuration['training'].update(numb_steps=8, save_freq=4)
    write(directory / 'input.json', configuration)
    configuration['training'].update(numb_steps=12, disp_file='restart-lcurve.out')
    write(directory / 'restart.json', configuration)


def train(directory, restart=False):
    # The build-science container has an isolated network with loopback only.
    # Explicit local_addr prevents torchrun from advertising an unresolvable
    # compute-node FQDN while retaining an automatically selected free port.
    command = [sys.executable, '-m', 'torch.distributed.run', '--standalone',
               '--local-addr=127.0.0.1', '--nproc_per_node=2',
               str(Path(__file__).resolve()), 'worker', 'restart.json' if restart else 'input.json']
    if restart:
        command += ['--restart', 'model.ckpt-8.pt']
    start = time.monotonic()
    with (directory / ('restart.log' if restart else 'training.log')).open('w') as log:
        result = subprocess.run(['timeout', '--kill-after=15s', '180s', *command], cwd=directory,
                                stdout=log, stderr=subprocess.STDOUT)
    report = dict(command=command, returncode=result.returncode, elapsed_seconds=time.monotonic() - start)
    if result.returncode:
        return report
    curve = directory / ('restart-lcurve.out' if restart else 'lcurve.out')
    rows = [[float(value) for value in line.split()] for line in curve.read_text().splitlines()
            if line.strip() and not line.startswith('#')]
    ranks = [json.loads((directory / f'rank-{rank}.json').read_text()) for rank in range(2)]
    expected_steps = list(range(9, 13)) if restart else list(range(1, 9))
    # A restart also prints the loaded checkpoint step before the new updates.
    steps = [int(row[0]) for row in rows if not restart or row[0] > 8]
    assert steps == expected_steps, rows
    assert all(math.isfinite(value) for row in rows for value in row), rows
    assert {rank['rank'] for rank in ranks} == {0, 1}
    assert {rank['cuda_current_device'] for rank in ranks} == {0, 1}
    assert all(rank['world_size'] == 2
               and (rank['backend'] == 'nccl' or 'cuda:nccl' in rank['backend'].split(','))
               and (not rank['empty_trainable_parameters'] or rank['find_unused_parameters'])
               and rank['gradients_finite'] and rank['gradient_calls'] > 0
               and rank['forward_calls'] >= len(expected_steps)
               and rank['gradient_devices'] == [f"cuda:{rank['local_rank']}"]
               and rank['initial_parameters_sha256'] != rank['final_parameters_sha256'] for rank in ranks), ranks
    assert ranks[0]['final_parameters_sha256'] == ranks[1]['final_parameters_sha256'], ranks
    checkpoint = directory / ('model.ckpt-12.pt' if restart else 'model.ckpt-8.pt')
    assert checkpoint.stat().st_size > 0
    report.update(passed=True, learning_curve=rows, ranks=ranks,
                  checkpoint=dict(name=checkpoint.name, bytes=checkpoint.stat().st_size,
                                  sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest()))
    return report


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'worker':
        worker()
        return
    # Cache writes stay in the allocated private tmpfs (SIF) or project scratch (native).
    scratch = Path(os.environ['TMPDIR']) / ('deepmd-training-' + str(os.getpid()))
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ.update(TRITON_CACHE_DIR=str(scratch / 'triton'),
                      TORCHINDUCTOR_CACHE_DIR=str(scratch / 'inductor'),
                      TORCH_EXTENSIONS_DIR=str(scratch / 'extensions'),
                      XDG_CACHE_HOME=str(scratch / 'cache'), CUDA_CACHE_PATH=str(scratch / 'cuda'),
                      TORCH_DISTRIBUTED_DEBUG='DETAIL')
    os.environ.update(NCCL_SOCKET_IFNAME='lo', GLOO_SOCKET_IFNAME='lo')
    for name in ('triton', 'inductor', 'extensions', 'cache/torch/kernels', 'cuda'):
        (scratch / name).mkdir(parents=True, exist_ok=True)
    write('inventory.json', inventory())
    identity = json.loads(os.environ.get('SAI_DEEPMD_DELIVERY', '{}')).get('identity', {})
    job = os.environ.get('SAI_MD_ALLOCATED_JOB', os.environ.get('SLURM_JOB_ID'))
    partition = os.environ.get('SLURM_JOB_PARTITION', identity.get('partition'))
    if not job or not partition:
        raise ValueError('training acceptance requires an identified allocation and partition')
    summary = dict(job=job, partition=partition,
                   installation=sys.argv[1], cases={})
    for descriptor in ('se_e2_a', 'dpa2'):
        directory = Path.cwd() / descriptor
        prepare(directory, descriptor)
        report = dict(training=train(directory))
        if report['training'].get('passed'):
            for rank in range(2):
                shutil.copyfile(directory / f'rank-{rank}.json', directory / f'initial-rank-{rank}.json')
            report['restart'] = train(directory, restart=True)
        report['passed'] = bool(report['training'].get('passed') and report.get('restart', {}).get('passed'))
        summary['cases'][descriptor] = report
        write('summary.json', summary)
    summary['passed'] = all(case['passed'] for case in summary['cases'].values())
    write('summary.json', summary)
    if not summary['passed']:
        raise RuntimeError('DeePMD two-GPU training/restart failed; inspect training logs')


if __name__ == '__main__':
    main()
