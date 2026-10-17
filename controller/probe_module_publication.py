#!/usr/bin/env python3
"""Lmod-only fixtures in an isolated experiment, never scientific artifacts."""
import argparse
from pathlib import Path
import re

from module_publication import publish_module
from delivery_layout import artifact_path
from release_contract import make_identity


def stage(root, phase):
    expected = Path.home() / 'sai-hpc-software/experimental'
    if (root.parent != expected or not re.fullmatch(r'module-publication-[a-z0-9-]+', root.name)
            or root.resolve() != root):
        raise ValueError('probe must use its isolated, fixed experiment root')
    if phase == 'initial' and root.exists():
        raise ValueError('do not overwrite an existing probe')
    pairs = [('dsprhbm', 'cpu-first'), ('8v100v0-avx512', 'gpu-other')] if phase == 'initial' else [('dsprhbm', 'cpu-next')]
    for target, run in pairs:
        identity = make_identity('cp2k', 'development', 'module-routing-fixture', '0' * 40,
                                 'NOT-SOFTWARE-MODULE-PROBE', '0' * 64, target)
        artifact = artifact_path(root, identity, run)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('NOT A SIF: isolated module-routing metadata fixture only\n')
        launcher = root / f'controller/{run}/cp2k'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('#!/bin/bash\necho "module routing fixture is not CP2K" >&2\nexit 125\n')
        launcher.chmod(0o555)
        modules = ['module load openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto',
                   'module load fftw/3.3.10 saiblas/2603-gnu-auto']
        if target != 'dsprhbm':
            modules += ['module load cuda/12.9.1 nvmplibs/26.7-tmp']
        publish_module(root, {'software': 'cp2k', 'version': identity['source_version'], 'target': target,
                             'source_sha': identity['source_sha'], 'recipe_sha256': identity['recipe_sha256'],
                             'identity': identity},
                       artifact, launcher, modules, 'CP2K')
    print('cp2k/development/' + identity['build_id'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('phase', choices=('initial', 'next'))
    args = parser.parse_args()
    stage(args.root, args.phase)
