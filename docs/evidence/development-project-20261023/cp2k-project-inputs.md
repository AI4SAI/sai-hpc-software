# CP2K project input prerequisites

New CP2K jobs use `/org/org-open-sai/proj-sai-hpc-software`. The dependency stage
requires the verified minimal base and source-only Spack bootstrap under that
project's `containers/base` and `cache/spack` directories. The application stage
also binds these two project directories:

| Directory | Required archives |
| --- | --- |
| `cache/cp2k-dependencies` | `libxs-1.0.0.tar.gz` |
| `cache/cp2k-probe` | Pinned `dbcsr-latest.tar.gz`, `fypp-3.2-py3-none-any.whl`, and seven TBLITE 0.7 dependency archives |

The exact names and SHA256 values are recorded in
[`cp2k-application-inputs.json`](cp2k-application-inputs.json), which also records
the independent copies made from the existing home cache on 2026-10-23. These
are source archives only. No installed dependency or old run was relabelled,
and the unused 189 MB `cp2k-latest.tar.gz` was not copied. The selected source's
LibXC, TBLITE release, wignernj and ELPA update archives use the CI input archive
cache recorded separately in [`cache-seed-receipt.json`](cache-seed-receipt.json).

Run this read-only preflight from a repository checkout on the cluster before
using a newly provisioned project. It checks the exact application input bytes
and rejects redirected paths or hard links:

```bash
PYTHONPATH=controller python3 - <<'PY'
import json
from pathlib import Path
from input_archive_cache import verified

project = Path('/org/org-open-sai/proj-sai-hpc-software')
receipt = Path('docs/evidence/development-project-20261023/cp2k-application-inputs.json')
for archive in json.loads(receipt.read_text())['archives']:
    path = project / archive['relative_path']
    if not verified(path, archive['sha256']):
        raise SystemExit('Missing CP2K input: ' + str(path))
    print('VERIFIED', path)
PY
```

The existing `cache/spack/bootstrap/spack-source-v2.ext3` is a source-only seed,
not a binary dependency cache. Previously sealed dependency overlays use an
older absolute CP2K install prefix; they cannot be substituted for a new
application identity by copying or rewriting a receipt. Their prior native
dependency build times were approximately 37 minutes on 4V100 and 67–75 minutes
on the other three partitions.
