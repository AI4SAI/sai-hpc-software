# DBCSR / NVRTC diagnostic

This probe isolates a SIGSEGV inside `nvrtcCompileProgram()` from CP2K input,
MPI, SCF and GPU kernel execution. It uses the pinned DBCSR transpose source,
compiles ten block sizes twice for V100, and records loaded library paths and
SHA-256 hashes. It uses Python's standard library and the installed NVRTC.

The [2026-10-21 investigation](../../docs/evidence/cp2k-nvrtc-node-20261021/README.md)
identified a damaged CUDA toolkit NVRTC file on `4v100n06`. The same-version
HPC SDK copy and the toolkit copy on `4v100n35` are intact. Public installation
repair remains an administrator action.

Inside a Slurm allocation, after loading `cp2k/2026.2-auto`:

```bash
base=/home/stardust/sai-hpc-software/runtime-tests/cp2k-nvrtc-20261021
python3 -B deployment/cp2k-nvrtc-diagnostic/nvrtc_probe.py \
  --dbcsr-source "$base/probe/dbcsr-source"
```

`--library /absolute/path/to/libnvrtc.so.12` can select an alternate library
for the standalone probe. This does not change the CP2K runtime environment.
All compiler scratch/cache directories should use home storage.

For an isolated CP2K comparison, `job.sbatch BASE [LIBRARY_DIRECTORY]` expects
these already prepared files in a new home-backed `BASE`:

- `probe/nvrtc_probe.py` and the DBCSR common/transpose headers below
  `probe/dbcsr-source/src/acc/libsmm_acc/kernels/`.
- `water343-original.inp`: the preserved three-step water reproduction.
- `water2-gpu.inp` and `water2-noacc.inp`: identical `water_input()` fixtures from
  `controller/cp2k_benchmark.py`, using the installed CP2K basis/potential data.

The archived inputs and frozen versions are in the investigation's `raw/`
directory. Copy them to a fresh run directory; do not change a submitted run.
The optional library directory is prepended only in the diagnostic job.
The script records every process exit code, continues collecting after a
failure, and finally returns nonzero if a probe or CP2K invocation failed.
The three-step reproduction intentionally permits SCF nonconvergence; do not
count it as scientific acceptance. Compare the fully converged OT energies
and forces separately. This is not a performance benchmark.
