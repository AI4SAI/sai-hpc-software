#!/usr/bin/env python3
"""Retry export with umask 022, preserving the failed bundle and frozen gates."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import sys

PROJECT = Path('/home/stardust/sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
CONTROL = ROOT / 'controller/acceptance/298825d3c4af605ee1846041637ac67cd0db9daf/lammps-release-20261020'
RECIPE = '345e160f793a5c2c724a13ccf52191e0f5034a5fee637238a2f42a9467d9eb6e'
BUILDS = {
    '4V100': 'md-39403117741-1-2026-10-20-9bf6ae82814537d7-export',
    '16V100': 'md-39403084059-1-2026-10-20-5fef92e054bacdaf-export',
    '8V100V0': 'md-39427644750-1-2026-10-20-1dfd6f53c113abda',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('partition', choices=BUILDS)
    args = parser.parse_args()
    os.umask(0o022)
    os.environ['SAI_SOFTWARE_ROOT'] = str(PROJECT)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(CONTROL))
    from md_tracking import fingerprint
    from md_publication import acceptance, publish, verify
    from source_cache import checksum
    if fingerprint(CONTROL) != RECIPE:
        raise ValueError('frozen acceptance controller changed')
    build = BUILDS[args.partition]
    task = ROOT / 'runs' / build
    science = build + '-science-recovery-' + RECIPE[:12]
    performance = build + '-performance'
    with (task / 'results/follow-export.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (task / 'results/publication.json').exists():
            manifest = verify(build)
            print(json.dumps(dict(build_run=build, published=manifest['published'], reverified=True)))
            return
        # Reparse completed raw evidence before changing any export path.
        delivery, image, digest, evidence = acceptance(build, science, performance)
        if evidence['performance']['evidence']['performance_scope'] != 'gpu':
            raise ValueError('expected explicit GPU performance scope')
        destination = ROOT / 'exports' / build
        archive = ROOT / 'exports/recovery-attempts' / (build + '-umask-077')
        record = dict(build_run=build, partition=args.partition, artifact=str(image),
                      artifact_sha256=digest, controller=str(CONTROL), recipe_sha256=RECIPE,
                      helper_sha256=checksum(Path(__file__)), umask='022',
                      reason='Multi-install export wrapper root inherited umask 077; re-export from unchanged SIF',
                      started_at=datetime.now(timezone.utc).isoformat(), state='prepared')
        audit = task / 'results/publication-export-recovery.json'
        if audit.exists() or archive.exists() or destination.with_name(build + '.json').exists():
            raise ValueError('existing recovery or export receipt requires inspection')
        if not destination.is_dir() or destination.is_symlink() or destination.resolve() != destination:
            raise ValueError('missing canonical failed export')
        record['original_root_mode'] = oct(stat.S_IMODE(destination.stat().st_mode))
        if record['original_root_mode'] != '0o700':
            raise ValueError('failed export does not match the diagnosed umask issue')
        record['archived_export'] = str(archive)
        record['metadata_sha256'] = {
            str(path.relative_to(destination)): checksum(path)
            for pattern in ('*/*/*/share/sai/manifest.json', '*/*/*/share/sai/export.json')
            for path in destination.glob(pattern)
        }
        if len(record['metadata_sha256']) != 4:
            raise ValueError('expected both paired exported inventories and receipts')
        with audit.open('x') as output:
            json.dump(record, output, sort_keys=True)
            output.write('\n')
        archive.parent.mkdir(parents=True, exist_ok=True)
        destination.rename(archive)
        print(json.dumps(dict(archived=str(archive), retrying=build)), flush=True)
        try:
            manifest = publish(build, science, performance)
            if verify(build) != manifest:
                raise ValueError('publication did not roundtrip')
            record.update(state='published-and-reverified', publication_sha256=checksum(task / 'results/publication.json'),
                          native_export=manifest['native_export']['directory'])
        except BaseException as error:
            record.update(state='stopped', error=repr(error))
            raise
        finally:
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            audit.write_text(json.dumps(record, sort_keys=True) + '\n')
        print(json.dumps(record, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
