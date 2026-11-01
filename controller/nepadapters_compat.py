#!/usr/bin/env python3
"""Adapt the pinned NEPAdapters frontend to LAMMPS's 2026 Kokkos types.

This changes plugin sources only. The installed LAMMPS and its Kokkos build
remain read-only. Double precision is still enforced by the upstream asserts.
"""
import argparse
import hashlib
import json
from pathlib import Path


def adapt(source, lammps):
    source, lammps = Path(source), Path(lammps)
    types = (lammps / 'src/KOKKOS/kokkos_type.h').read_text()
    if 'kkacc_1d' not in types or 'double_1d_6_lr' not in types:
        raise ValueError('this release adapter requires the 2026 Kokkos type API')
    communication = (lammps / 'src/KOKKOS/kokkos.h').read_text()
    if 'reverse_comm_legacy' not in communication or 'reverse_pair_comm_legacy' not in communication:
        raise ValueError('this release adapter requires the 2026 Kokkos communication API')
    replacements = {
        'DAT::tdual_xfloat_1d': 'DAT::tdual_double_1d',
        'DAT::tdual_efloat_1d': 'DAT::tdual_double_1d',
        'DAT::t_efloat_1d': 'DAT::t_double_1d',
        'DAT::tdual_virial_array': 'DAT::tdual_double_1d_6_lr',
        'DAT::t_virial_array': 'DAT::t_double_1d_6_lr',
        'X_FLOAT': 'KK_FLOAT',
        'F_FLOAT': 'KK_ACC_FLOAT',
        'E_FLOAT': 'KK_ACC_FLOAT',
        'reverse_comm_classic': 'reverse_comm_legacy',
        'reverse_pair_comm_classic': 'reverse_pair_comm_legacy',
    }
    records = {}
    for name in ('pair_nep_adapters_cuda.h', 'pair_nep_adapters_cuda.cpp'):
        path = source / 'frontends/lammps' / name
        before = path.read_bytes()
        text = before.decode()
        counts = {old: text.count(old) for old in replacements}
        if not any(counts.values()):
            raise ValueError('plugin source is already patched or has changed: ' + name)
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text)
        records[name] = dict(before_sha256=hashlib.sha256(before).hexdigest(),
                            after_sha256=hashlib.sha256(text.encode()).hexdigest(),
                            replacements=counts)
    return dict(schema=1, patch='lammps-2026-kokkos-types', files=records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('lammps', type=Path)
    parser.add_argument('receipt', type=Path)
    args = parser.parse_args()
    args.receipt.write_text(json.dumps(adapt(args.source, args.lammps), indent=2) + '\n')
