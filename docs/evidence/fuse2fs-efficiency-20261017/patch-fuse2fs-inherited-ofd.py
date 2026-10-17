#!/usr/bin/env python3
"""Keep an inherited Apptainer image descriptor protected by an OFD lock.

Normal pathname mounts retain upstream's flock. No synchronization operation
is changed. Fail if the exact upstream source or expected context differs.
"""
import hashlib
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text()
if hashlib.sha256(path.read_bytes()).hexdigest() != sys.argv[2]:
    raise SystemExit('unexpected upstream fuse2fs source')

helper = r'''
/* Apptainer passes an already OFD-locked image as /proc/self/fd/N.  Reuse
 * that open file description's lock; an independent BSD flock conflicts
 * with it on Lustre.  Keep the inherited descriptor open until process exit.
 * Ordinary pathname mounts still take the upstream BSD lock below.
 * This adapter is intentionally restricted to whole, regular-file images.
 */
static int lock_inherited_image(const char *device, int writable)
{
#ifdef F_OFD_SETLK
	const char prefix[] = "/proc/self/fd/";
	struct flock lk = {0};
	struct stat st;
	char *end;
	long fd;
	int mode;

	if (strncmp(device, prefix, sizeof(prefix) - 1))
		return 0;
	if (!isdigit((unsigned char)device[sizeof(prefix) - 1]))
		return -EINVAL;
	errno = 0;
	fd = strtol(device + sizeof(prefix) - 1, &end, 10);
	if (errno || *end || fd < 0 || fd > INT_MAX)
		return -EINVAL;
	if (fstat((int)fd, &st) < 0)
		return -errno;
	if (!S_ISREG(st.st_mode))
		return -EINVAL;
	mode = fcntl((int)fd, F_GETFL);
	if (mode < 0)
		return -errno;
	if (writable && (mode & O_ACCMODE) != O_RDWR)
		return -EBADF;
	lk.l_type = writable ? F_WRLCK : F_RDLCK;
	lk.l_whence = SEEK_SET;
	/* start=0, len=0 protects the entire image, including future growth. */
	if (fcntl((int)fd, F_OFD_SETLK, &lk) < 0)
		return -errno;
	return 1;
#else
	return 0;
#endif
}

'''
old = 'int main(int argc, char *argv[])\n'
assert source.count(old) == 1
source = source.replace(old, helper + old)
source = source.replace('#include <ctype.h>\n', '#include <ctype.h>\n#include <fcntl.h>\n#include <limits.h>\n', 1)
old = '''		unsigned int lock_flags = IO_CHANNEL_FLOCK_TRYLOCK;

		if (global_fs->flags & IO_FLAG_RW)'''
new = '''		unsigned int lock_flags = IO_CHANNEL_FLOCK_TRYLOCK;
		int inherited = fctx.offset == 0 ?
			lock_inherited_image(fctx.device, global_fs->flags & EXT2_FLAG_RW) : 0;

		if (inherited < 0) {
			err = -inherited;
			err_printf(&fctx, "Could not lock inherited image: %s\\n", error_message(err));
			goto out;
		}
		if (inherited)
			goto image_locked;

		if (global_fs->flags & IO_FLAG_RW)'''
assert source.count(old) == 1
source = source.replace(old, new)
old = '\n\tfctx.fs = global_fs;\n'
assert source.count(old) == 1
source = source.replace(old, '\nimage_locked:\n\tfctx.fs = global_fs;\n')
path.write_text(source)
print(hashlib.sha256(path.read_bytes()).hexdigest(), path)
