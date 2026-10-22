# Versioned public fuse2fs package qualification

The public package/module name is `fuse2fs/1.47.4-sai1`, with administrator
scripts in `deployment/fuse2fs-1.47.4-sai1/`. The source commit and accepted
binary are unchanged from the earlier commit-named package. `sai1` identifies
the post-v1.47.4 upstream I/O changes plus the local OFD/read-only patches;
this is not an unmodified upstream release. `versions.json` confirms that
the existing Apptainer 1.4.4 already bundles fuse2fs 1.47.3.

DSPRHBM job **1899786** on `d9470n03` completed `0:0` in **1m13s** and passed
all **13** filesystem/module checks. The renamed module selected the intended
helper via `APPTAINER_CONFIG_FILE` and restored the environment on unload.
The image hash was independently reread after completion. Seven installer
tests passed on home/Lustre, and the source/target ACL preflight for
`saiuser01` passed. The four standard public files total **1,765,884 bytes**.

The implementation tested is commit `81f03c450e05dac275f3c42eea086786e75efa6d`.
`SHA256.json` inventories 98 frozen input/output files under
`/home/stardust/sai-hpc-software/diagnostics/fuse2fs-versioned-20261022/`.
Raw images, traces and logs remain there; no test materials are installed in
the public prefix.

This is **pre-install qualification in home**, not acceptance of an installed
`/opt/devtools/fuse2fs/1.47.4-sai1/`. The administrator must still install it
and the actual public module must pass the supplied post-install check.
Cross-account execution and abrupt-failure recovery remain tracked in
[issue #12](https://github.com/AI4SAI/sai-hpc-software/issues/12). The earlier
[qualification and failed attempts](../fuse2fs-public-20261022/README.md)
retain their historical names and evidence.
