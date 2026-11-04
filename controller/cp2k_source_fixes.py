#!/usr/bin/env python3
"""Apply guarded CP2K source repairs and record the exact changed bytes."""
import hashlib
import json
from pathlib import Path
import sys

UPSTREAM = 'https://github.com/cp2k/cp2k/pull/6261'
COMMIT = '8d7e2e4a022e56b628f89b206a0b2877c498fac2'
DECLARATION = '                                                  nblk, neig, io_unit, &\n                                                  success\n'
ANCHOR = '      END SELECT\n\n      IF (.NOT. elpa_one_stage) THEN\n'
SETUP = '''      ! Finalize GPU setup when a GPU kernel is used. Ensure ELPA does not flood output with
      ! debug messages by temporarily turning debug off
      CALL elpa_obj%get("debug", debug_value, success)
      CPASSERT(success == ELPA_OK)

      CALL elpa_obj%set("debug", 0, success)
      CPASSERT(success == ELPA_OK)

      success = elpa_obj%setup_gpu()
      CPASSERT(success == ELPA_OK)

      CALL elpa_obj%set("debug", debug_value, success)
      CPASSERT(success == ELPA_OK)

'''

# v2026.2 and the inspected development branch have the same cuSOLVER source.
# Review changed upstream code before applying this synchronization repair.
NCCL_SOURCE_SHA256 = '379cda72ac8c18e18f30ffd019b37ae0e8216572c40f5e6ad2fbceb6decd4ad8'
NCCL_REPAIRED_SHA256 = 'eb22082437dbee127fedd77a8f7e2f7cb05444b507b2a4be29843cc1fa9ceff3'
NCCL_BROADCAST = '  MPI_Bcast(&nccl_id, sizeof(nccl_id), MPI_BYTE, 0, comm);\n'
NCCL_PROGRESS = '''  // Drain outstanding MPI traffic before entering blocking NCCL bootstrap.
  // ScaLAPACK peers can still require MPI progress after this rank returns.
  MPI_Barrier(comm);
'''


def fix_nccl_progress(source, metadata):
    path = Path(source) / 'src/fm/cp_fm_cusolver.c'
    before = path.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    if digest == NCCL_REPAIRED_SHA256:
        applied = False
        after = before
    elif digest == NCCL_SOURCE_SHA256:
        text = before.decode()
        if text.count(NCCL_BROADCAST) != 3:
            raise ValueError('unrecognized CP2K NCCL bootstrap sites')
        after = text.replace(NCCL_BROADCAST, NCCL_BROADCAST + NCCL_PROGRESS).encode()
        if hashlib.sha256(after).hexdigest() != NCCL_REPAIRED_SHA256:
            raise ValueError('CP2K NCCL progress repair differs from reviewed bytes')
        path.write_bytes(after)
        applied = True
    else:
        raise ValueError('unrecognized CP2K cuSOLVER source; review MPI progress repair')
    record = dict(applied=applied, source_file='src/fm/cp_fm_cusolver.c',
                  before_sha256=digest, after_sha256=hashlib.sha256(after).hexdigest(),
                  bootstrap_sites=3, repair='MPI progress barrier before blocking NCCL bootstrap')
    metadata = Path(metadata)
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / 'nccl-mpi-progress-fix.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    return record


def fix(source, metadata):
    path = Path(source) / 'src/fm/cp_fm_elpa.F'
    before = path.read_bytes()
    text = before.decode()
    if 'elpa_obj%setup_gpu()' in text:
        applied = False
    else:
        if text.count(DECLARATION) != 1 or text.count(ANCHOR) != 1:
            raise ValueError('unrecognized CP2K ELPA setup; review upstream repair')
        text = text.replace(DECLARATION, DECLARATION.replace('success\n', 'success, debug_value\n'))
        text = text.replace(ANCHOR, '      END SELECT\n\n' + SETUP + '      IF (.NOT. elpa_one_stage) THEN\n')
        path.write_text(text)
        applied = True
    record = dict(upstream=UPSTREAM, upstream_commit=COMMIT, applied=applied,
                  source_file='src/fm/cp_fm_elpa.F',
                  before_sha256=hashlib.sha256(before).hexdigest(),
                  after_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    metadata = Path(metadata)
    metadata.mkdir(parents=True, exist_ok=True)
    (metadata / 'elpa-gpu-setup-fix.json').write_text(json.dumps(record, sort_keys=True) + '\n')
    return record


if __name__ == '__main__':
    print(json.dumps(fix(*sys.argv[1:])))
    print(json.dumps(fix_nccl_progress(*sys.argv[1:])))
