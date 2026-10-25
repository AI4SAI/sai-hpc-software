"""Flush writable overlay metadata before Apptainer starts driver teardown."""
import argparse
import json
import os
from pathlib import Path
import time


def synchronize(phase, workspace=Path('/workspace'), results=Path('/results')):
    if phase not in ('dependencies', 'build', 'metadata', 'export'):
        raise ValueError('overlay synchronization requires a writable phase')
    marker = workspace / '.sai-overlay-sync'
    started = time.monotonic()
    # fuse2fs's writable-file fsync calls ext2fs_flush2(fs, 0), including the
    # filesystem metadata and backing image. A host sync of the image alone
    # cannot flush metadata still held in the userspace driver.
    print('CP2K_OVERLAY_SYNC_START ' + phase, flush=True)
    with marker.open('wb') as stream:
        stream.write((phase + '\n').encode())
        stream.flush()
        os.fsync(stream.fileno())
    record = dict(phase=phase, synchronized=True, marker=str(marker),
                  elapsed_seconds=time.monotonic() - started)
    with (results / ('overlay-sync-' + phase + '.json')).open('x') as stream:
        stream.write(json.dumps(record) + '\n')
    print('CP2K_OVERLAY_SYNC_COMPLETE ' + phase, flush=True)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase')
    synchronize(parser.parse_args().phase)
