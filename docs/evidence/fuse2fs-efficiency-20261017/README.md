# SAI home-image efficiency experiment

These are diagnostic scripts and evidence, not the production controller.
See [the report](../../BUILD-IO-EFFICIENCY.md) for results and limits.

All absolute paths in the scripts refer to retained, site-specific experiment
directories under `/home/stardust/sai-hpc-software/diagnostics/`. They are not
templates to run unchanged: the benchmark rejects existing case directories.
Use fresh experiment paths to repeat it. Do not modify the recorded inputs of
an existing Slurm job or mount any active CP2K image.

- `build-fuse2fs-candidate.sh` built pinned upstream `45651efd` in an overlay,
  using checksum-verified source and Ubuntu FUSE headers. `fuse2fs-efficiency.sbatch`
  records its original allocation; its option-test runner remains in the home
  evidence directory. Direct I/O timed out in that original experiment.
- `patch-fuse2fs-inherited-ofd.py` checks the exact upstream source digest and
  applies the adapter shown in `fuse2fs-inherited-ofd.patch`.
- `job.sbatch` copies the completed driver-build image into a new experiment,
  rebuilds with `rebuild-candidate.sh`, then runs the corrected comparison.
  `gpu-job.sbatch` records the independent 16V100 allocation using identical
  driver bytes, with the site's required GPU/CPU ratio.
- `run-fuse2fs-benchmark.py`, `fuse2fs-workload.py`, and
  `summarize-fuse2fs-benchmark.py` are the exact corrected probe inputs. They
  preserve private overlays, syscall traces, explicit-sync results, file
  manifests, filesystem checks, clean-remount checks and competing-writer
  rejection. Every case retains its original output and identity.

The build image, source archives, binaries and full syscall logs stay in home;
the repository retains their checksums and compact results. Sources, temporary
files, compiler outputs and installation trees were inside the home-backed
images. Only the candidate driver and diagnostic metadata were exported.
Neither production controllers nor the four running CP2K builds were changed.
