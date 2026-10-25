#!/usr/bin/env python3
"""Record the frameworks as installed, without hiding allocated GPUs."""
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sys

import torch
import tensorflow as tf
import jax

record = dict(python=sys.executable, job=os.environ['SLURM_JOB_ID'],
              partition=os.environ['SLURM_JOB_PARTITION'],
              cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'),
              torch=dict(version=torch.__version__, cuda=torch.version.cuda,
                         devices=[torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]),
              tensorflow=dict(version=tf.__version__, build=tf.sysconfig.get_build_info(),
                              built_with_cuda=tf.test.is_built_with_cuda(),
                              gpu_devices=[str(device) for device in tf.config.list_physical_devices('GPU')]),
              jax=dict(version=jax.__version__, devices=[str(device) for device in jax.devices()]),
              horovod=importlib.util.find_spec('horovod') is not None)
assert len(record['torch']['devices']) == 2, record
Path('frameworks.json').write_text(json.dumps(record, indent=2) + '\n')
