# Public driver selection in generated build jobs

The generated ABACUS and GPUMD batch scripts now explicitly load
`apptainer/1.4.4 fuse2fs/1.47.4-sai`. Their `--export=NIL` allocations do not
inherit a module selected only in the submission shell. CP2K retains its
three explicit pinned-driver calls for build, export, and overlay extraction;
DeePMD/LAMMPS already use the same wrapper in their separate controllers.

On the cluster login node, the actual module command extracted from each
generated script was loaded in a fresh shell. `selection.json` records the
selected public configuration/binary for ABACUS and GPUMD, the CP2K wrapper
count, successful environment restoration on purge, and source/script hashes.
All generated scripts passed `bash -n`. Seven existing controller/image-driver
regression tests passed on the cluster's Linux home filesystem.

This checks job generation and module selection. Actual public filesystem
mounting, fsync, read-only hashes, and writer exclusion were already verified
by the [13-case installed acceptance](../fuse2fs-installed-20261022/README.md).
It does not claim a new ABACUS or GPUMD scientific build or a new speedup
measurement. The public driver, config and `/opt` module files were not changed.

The frozen controller/test snapshot, generated scripts and results are retained
under `/home/stardust/sai-hpc-software/diagnostics/fuse2fs-workflow-selection-20261022/`.
All test scratch is inside that home tree.
