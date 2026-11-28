"""Reviewed site configuration; release metadata cannot select host write roots."""
import json
from pathlib import Path


def hardware(target):
    # Extra partitions belong only to SpinGNN. The shared TARGETS/PARTITIONS
    # and every other software's default matrix remain unchanged.
    from remote_controller import TARGETS
    if target in ('dsprhbm', '4v100-avx512', '16v100-avx2', '8v100v0-avx512'):
        return TARGETS[target]
    if target == 'cpu-misc':
        raise ValueError('CPU-MISC is excluded from SpinGNN production use')
    if target in ('8a100m40', '8a100m80'):
        return dict(partition={'8a100m40': '8A100M40', '8a100m80': '8A100M80'}[target],
                    qos='rush-1o2gpu', gpus=1, cuda_arch='80',
                    cpu_arch='x86-64-v3', dependency_isa='avx2')
    raise ValueError('unregistered SpinGNN target')


def site():
    path = Path(__file__).with_name('spingnn_site.json')
    if path.is_symlink():
        raise ValueError('redirected SpinGNN site configuration')
    value = json.loads(path.read_text())
    root = Path(value['root'])
    if not root.is_absolute() or '..' in root.parts or str(root) != value['root']:
        raise ValueError('noncanonical SpinGNN deployment root')
    return value


def prefix(label, sha, recipe, partition, run=None):
    # Attempts have separate SIFs and export bundles, never separate runtime
    # prefixes. The exact same versioned path is used in the image and /opt.
    import re
    version = label.removeprefix('v')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[.+-][A-Za-z0-9.-]+)?', version):
        raise ValueError('SpinGNN requires a concrete release version')
    return str(Path('/opt/apps/spingnn') / ('v' + version) / partition)


def target_policy(target):
    profile = hardware(target)
    settings = site()
    if target not in settings['targets']:
        raise ValueError('unregistered SpinGNN target')
    record = settings['targets'][target]
    if (type(record['enabled']) is not bool or record['backend'] != ('cuda' if profile['gpus'] else 'cpu')
            or record['partition'] != profile['partition']
            or (record['enabled'] and not record.get('mpi_prefix', '').startswith('/opt/devtools/openmpi/'))
            or (not record['enabled'] and not record.get('reason'))):
        raise ValueError('invalid SpinGNN target qualification')
    return record
