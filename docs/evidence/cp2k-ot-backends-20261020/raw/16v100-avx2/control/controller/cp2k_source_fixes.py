#!/usr/bin/env python3
"""Apply the upstream ELPA GPU setup repair to the release that predates it."""
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
