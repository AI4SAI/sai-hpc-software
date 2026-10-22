#!/usr/bin/env python3
"""Apply inherited OFD locking and byte-preserving explicit read-only opens."""
import difflib
import hashlib
from pathlib import Path
import subprocess
import sys

PRISTINE_SHA256 = 'ab3794d86b12a8227b4a87823349e974df06f39c42971dcf2983ddf57cf82696'
OFD_SHA256 = '2c299a8eb6a26620c629a40a0270543ffb9aec8cfb8abf84613246cab14b63ff'

path = Path(sys.argv[1])
pristine = path.read_text()
if hashlib.sha256(path.read_bytes()).hexdigest() != PRISTINE_SHA256:
    raise SystemExit('unexpected pristine fuse2fs source')
subprocess.run([sys.executable, str(Path(__file__).with_name('patch-fuse2fs-inherited-ofd.py')),
                str(path), PRISTINE_SHA256], check=True)
if hashlib.sha256(path.read_bytes()).hexdigest() != OFD_SHA256:
    raise SystemExit('unexpected inherited OFD adapter result')
source = path.read_text()
old = '\tif (fctx.directio)\n\t\tflags |= EXT2_FLAG_DIRECT_IO;\n'
new = '''\t/* An explicit read-only mount must never reopen its image writable.
\t * FUSE's ro flag alone does not prevent libext2fs metadata updates.
\t * This also makes the inherited OFD lock use a shared read lock.
\t */
\tif (fctx.ro)
\t\tflags &= ~EXT2_FLAG_RW;
\tif (fctx.directio)
\t\tflags |= EXT2_FLAG_DIRECT_IO;
'''
assert source.count(old) == 1
source = source.replace(old, new)
old = '\t/* Make a note in the error log */\n'
new = '''\t/* Read-only diagnostics must not dirty or flush the image superblock. */
\tif (!(fs->flags & EXT2_FLAG_RW)) {
\t\tif (ff->panic_on_error)
\t\t\tabort();
\t\treturn ret;
\t}

\t/* Make a note in the error log */
'''
assert source.count(old) == 1
source = source.replace(old, new)
path.write_text(source)
print(hashlib.sha256(path.read_bytes()).hexdigest(), path)
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(''.join(difflib.unified_diff(
        pristine.splitlines(keepends=True), source.splitlines(keepends=True),
        fromfile='a/misc/fuse2fs.c', tofile='b/misc/fuse2fs.c')))
