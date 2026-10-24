# SIF Lmod / host-MPI runtime acceptance — 2026-10-24

All **17 application/partition entries passed** through the new independent
Lmod modules. Original accepted SIF bytes and source revisions are unchanged.
The new entry points were tested against those fixed images; these checks do
not claim that later upstream commits were built or establish a new general
performance baseline.

| Partition | Job | Nodes × MPI ranks/node | State | Checks |
| --- | --- | --- | --- | --- |
| DSPRHBM | 1919703 | 1 × 2 | COMPLETED 0:0, 1m06s | CP2K, ABACUS |
| 16V100 | 1919704 | 2 × 2 | COMPLETED 0:0, 1m16s | All five programs |
| 8V100V0 | 1919705 | 1 × 2 | COMPLETED 0:0, 2m34s | All five programs |
| 4V100 | 1919706 | 1 × 2 | COMPLETED 0:0, 46s | All five programs |

GPU checks used one allocated V100 per node. MPI applies to CP2K, ABACUS and
LAMMPS; DeePMD inference and GPUMD run directly on the first node. Every job
uses account `proj-sai-hpc-software` and a project working directory. No
diagnostic `srun`, physical `/tmp`, writable application overlay, application
rebuild or physical `/opt` installation was involved.

The final runtime SHA256 is
`49c575bd293fddf094e4f736fb33c3d0a0522b174e53554e43c89999deaa1759`;
the tested project catalog SHA256 is
`e4f09d656668fd547856819f60fb8a30d2d519db5033f945d71f3b4e11ca6671`.
The catalog pins each source, recipe, exact image, image checksum, installation
prefix, explicit site dependency bind and archived acceptance record.

The checks exercise PBE/OT CP2K energy and forces, ABACUS PW SCF energy,
LAMMPS GPU pair calculations with energy/pressure/all force components,
DeePMD PyTorch GPU inference with energy/forces/virial, and GPUMD static energy
and forces. Maximum CP2K deviations from the already accepted final-SIF runs
are 5.41e-13 Ha and 1.01e-12 Ha/Bohr. Maximum ABACUS energy deviation is
1.61e-8 eV. LAMMPS compares 775 observables per partition; maximum deviation is
8.89e-16. DeePMD reports zero energy error, force/virial errors below 5.56e-17,
and 17,083,392 allocated CUDA bytes. GPUMD agrees with its retained reference.
These are short launch-route checks; full original feature/science acceptance
remains in each application's campaign evidence.

The MPI commands invoke host `mpirun` and preserve its exact partition-specific
Open MPI prefix. Rank traces prove the expected nodes/rank IDs, image hashes,
CPU affinities, GPU environment and container argv. Images are hashed exactly
once per job/node; subsequent ranks and repeated version calls reuse the
locked check. The final MPI logs contain no UCX permission failures or shared
memory allocation fallback errors.

Three earlier failures are retained:

- `1919669`: the first test used `--export=NIL`, leaving host MPI without its
  required user environment. It failed before any container rank ran. The
  final script uses `--export=ALL` while resetting the library/build environment.
- `1919680`: same-node DSPRHBM ranks had separate unprivileged user namespaces.
  UCX POSIX access to another process's file descriptor was denied, and Open
  MPI attempted its shared CUDA memory pool on Lustre. CP2K converged, but the
  checker rejected the fallback/abort advisory; the allocation remains failed.
- `1919696`: `--sharens` removed the UCX error. The shared-memory base-directory
  setting alone was insufficient because Open MPI's relocation switch defaults
  to zero. The final runtime sets `shmem_mmap_relocate_backing_file=1` as well,
  placing communication pools in `/dev/shm` RAM. Disk scratch remains in the
  project. The original failed log is preserved.

Apptainer documents the same user-namespace issue and the parent-PID constraint
of [`--sharens`](https://apptainer.org/docs/user/1.4/mpi.html#using-sharens-mode).
The Bash/Python entry points use `exec` to preserve that parent PID. The existing
site Apptainer 1.4.4 supplies this mode; no new Apptainer/fuse2fs deployment was
required for the read-only SIF runtime.

`SHA256.json` binds **272 archived files**, including all four final raw runs,
inputs, numeric outputs, rank traces, tested runtime/catalog/check scripts,
Slurm accounting and the three earlier failure logs. The first direct
submission did not have a `submission.json`; its actual job and failure are
retained in accounting and the original logs. The local replay checks every
archived byte, rechecks input hashes, reparses numerical results and rank
selection, and requires successful final accounting. It reproduces the
cluster `verification.json` records exactly:

```bash
python3 -B deployment/sif-development-20261024/collect.py
```

See [`local-validation.json`](local-validation.json) for the replay result and
the [usage/admin guide](../../../deployment/sif-development-20261024/README.md)
for deployment. The administrator-readable handoff contains copied, rehashed
images; its paths/catalog differ from the private project catalog by design.
Public installation copies only images and required runtime/module files.
Verification code and scientific fixtures stay out of `/opt`.
