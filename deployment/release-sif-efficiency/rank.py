#!/usr/bin/env python3
"""Use the production SIF launcher with the same observation shim in both arms."""
import json
import os
from pathlib import Path
import sys

plan_path, route, operation, *arguments = sys.argv[1:]
plan_path = Path(plan_path)
plan = json.loads(plan_path.read_text())
root = plan_path.parent
probe = str(Path.cwd() / 'observe.py')
original = [plan['binary'], *arguments]
shim = ['/usr/bin/python3', '-B', probe, operation, plan['binary'], *arguments]
if route == 'native':
    os.execv(shim[0], shim)
assert route == 'sif'
sys.path.insert(0, str(root / 'control/controller'))
import sif_runtime as runtime

command = runtime.command


def observed(*args, **kwargs):
    argv, env = command(*args, **kwargs)
    assert argv[-len(original):] == original
    argv[-len(original):] = shim
    return argv, env


runtime.command = observed
runtime.main([str(root / 'bundle/catalog.json'), plan['software'], plan['program'], *arguments])
