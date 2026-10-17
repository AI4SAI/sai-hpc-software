# Pinned source and installed CP2K audit

`source-installations.json` records the October 17 read-only review of the
exact release source, checksum-pinned DBCSR source, six older installation
configurations and site modulefiles. It preserves relevant numbered lines and
full-file hashes. No user calculation directories were read.

To repeat on SAI, choose a new output filename in the project home:

```bash
python3 collect-audit.py --output \
  "$HOME/sai-hpc-software/diagnostics/cp2k-release-20261016/source-review-NEW.json"
```

The collector reads files and Git objects without executing installed CP2K,
mounting an image or submitting jobs. It rejects a changed DBCSR archive and
an existing output file. The source revision is deliberately fixed; use a
separate collector/review for a different source identity.

Installation settings establish available capabilities, not runtime library
resolution, actual user demand or a tuning speedup. See
[the source review](../../CP2K-V100-TUNING.md) for conclusions and experiments.
