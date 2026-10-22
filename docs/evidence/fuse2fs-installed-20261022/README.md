# Public fuse2fs installation acceptance

`saiuser01` installed the four standard files under
`/opt/devtools/fuse2fs/1.47.4-sai1/` and renamed only the module file to
`/opt/modules/modulefiles/devtools/fuse2fs/1.47.4-sai`. Its bytes, internal
prefix and checksums match the qualified package. The public module name is
therefore `fuse2fs/1.47.4-sai`; the installation and help text identify build
revision `1.47.4-sai1`. The repository's installer/checker now uses this actual
module name without rewriting any `/opt` files.

Job **1900053** ran as `stardust` on DSPRHBM `d9470n03`, completed `0:0` in
**1m34s**, and passed all **13** checks against the installed public binary
and module. This includes real driver selection through the module's config,
environment restoration on unload, write/rename/compile/fsync, filesystem
checks, byte-preserving read-only mounts, competing-writer rejection, and
lock release. The final image SHA256 was independently reread. Seven installer
tests passed. `installed-files.json` records exact contents, modes and the
administrator ownership; `verify.log` includes the version and complete loader
resolution. No test files or receipts were installed under `/opt`.

The additional stock → public → public → stock comparison in job **1900121**
completed on the same partition in **1m54s**. Its 32-unit `-j8` compile phase
took **23.072 / 10.480 seconds** with the bundled driver and
**0.851 / 0.874 seconds** with the public driver. The ratio of the two medians
is **19.44**. All four 161-file manifests match, and filesystem/remount checks
passed. This is a small filesystem-heavy compile experiment with two samples
per driver, not a full CP2K/LAMMPS build-speed claim. The public helper was
selected directly for profiling; module selection was verified separately by
the 13-case public-install check.

Raw inputs, traces and images remain in home under
`diagnostics/fuse2fs-installed-20261022/` and
`diagnostics/fuse2fs-installed-benchmark-20261022/`. The evidence here does not
establish portability to every filesystem or abrupt-failure recovery behavior.
