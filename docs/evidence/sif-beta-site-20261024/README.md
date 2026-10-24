# Site templates and versioned beta links — 2026-10-24

The live generation is `/home/stardust/sai-hpc-software/sif-beta/generations/20261024-r2`.
All five home entries are actual relative symlinks, for example
`cp2k/master-beta-later -> master-beta-20261024-cfe5e5d87aaf`.
The `/opt` files are static three-line Tcl gateways to those home links.
Real Lmod 9.2.4 checks passed for login rejection with full help, selection,
rotation, unload and rollback through both direct home links and fixed gateways.
Publication rechecked all 17 accepted SIFs; runtime SHA256 remains
`49c575bd293fddf094e4f736fb33c3d0a0522b174e53554e43c89999deaa1759`.
The deployed templates match the successfully exercised templates byte for byte.
`raw/publication-check.json` also confirms that the new `/opt` gateways were
not installed by this work. The administrator installer passed `--check` and
installation to a private module root.

`/opt/sbatch_examples` resolves to `/opt/sai/examples`. The actual site scripts
and affinity helpers are archived in `raw/site`. GPU MPI templates now source
`/opt/sai/runtime/mps_mapping.d/${SLURM_JOB_PARTITION}.bash` and use `$MAP_OPT`.
The published templates omit export-all, strict-shell setup, environment dumps,
manual `ppr` binding and extra MPI launcher variables. Internal evidence drivers
still use strict error handling and tracing; they are not public templates.
LAMMPS keeps the accepted GPU package and uses the allocated GPU count.
The CPU templates use `slot:pe` on DSPRHBM instead of assuming the AMD CCD
topology in the site's D9654 example.

| Job | Resources | Result |
| --- | --- | --- |
| 1921985 | 16V100, 1 GPU | Probe: CUDA and NCCL available, Horovod absent. |
| 1922027 | 16V100, 2 ranks, 2 GPUs | CP2K, ABACUS and LAMMPS numerical checks passed with site mapping `ppr:2:node:pe=4`; 41 seconds. |
| 1922029 | 16V100, 1 GPU | Actual generated GPUMD template passed energy/forces; 2 seconds. |
| 1922061 | DSPRHBM, 2 ranks, 2 CPUs/rank | Actual generated CP2K/ABACUS CPU templates passed numerical checks; 56 seconds. |
| 1922119 | 16V100, 1 GPU | Actual generated DeePMD template trained `se_e2_a` for two steps with finite losses and a saved checkpoint; 16 seconds. |

All jobs use account `proj-sai-hpc-software`. Inputs/results are under
`/org/org-open-sai/proj-sai-hpc-software/runtime-tests/sif-beta-site-20261024-*`;
the final templates set `TMPDIR="$HOME/.sai-sif-$SLURM_JOB_ID"`. No physical
`/tmp` disk is used for these cases or SIF scratch. The runtime binds home-backed
scratch at the container's `/tmp`; MPI shared memory continues to use RAM.
The final science reports record CP2K energy deltas below `2e-13` Ha, ABACUS
energy deltas below `8e-9` eV, and zero LAMMPS/GPUMD differences for these cases.
These are correctness checks, not performance benchmarks. The existing
[17-entry SIF acceptance](../sif-lmod-20261024/README.md) still covers the broader
partition matrix and two-node host MPI on the unchanged image/runtime bytes.

## Why the launchers differ

`raw/source-audit.json` records pinned upstream URLs, file hashes and excerpts.
DeePMD single-GPU training uses `dp`; PyTorch distributed training uses
torchrun/DDP/NCCL, while TensorFlow distributed training requires Horovod and
can use host MPI. The accepted Python environment has no Horovod. Simply using
host `mpirun dp --pt train` does not establish a PyTorch DDP group. The experimental
checks launched torchrun inside the SIF through `deepmd-python`, ensuring the
rank variables are created inside its clean environment.

GPUMD's pinned main has no MPI initialization; the extracted ELF has no MPI/PMPI
imports (`raw/probe/gpumd-imports.json`). PLUMED's indirect dependencies do not
make GPUMD an MPI-rank decomposition program. GPUMD can automatically use several
visible GPUs for NEP within one process. The public template is single-GPU;
multi-GPU NEP additionally requires a sufficiently large simulation cell.

## Failed checks retained and training limitations

- **1922011**: the MPI cases completed, then the tiny GPUMD fixture detected two
  visible GPUs and failed its minimum cell-size check. The corrected GPUMD job
  requests the one GPU specified by its public template. No GPU was hidden to
  force CPU execution.
- **1922012**: two-GPU NCCL all-reduce passed on `cuda:0` and `cuda:1`, each
  obtaining 3 from the sum of 1 and 2. Training failed in `torch_shm_manager`
  with `Invalid argument` when the case-based temporary path exceeded Unix
  socket length limits. Switching to the short home path removed this failure.
- **1922028**: with the short home path, `se_e2_a` DDP completed the first step,
  then failed at step two with unfinished reduction/unused parameters. The
  actual model parameter inventory from 1922090 identifies indices 8–11 as
  `compress_info.0/1` and `compress_data.0/1`, all empty and `requires_grad=True`.
  The pinned trainer sets `find_unused_parameters=self.multi_task`; these
  placeholders contradict its assumption that a single-task step uses every
  parameter. The same `se_e2_a` input passed two steps in single-GPU job 1922119.
- **1922090**: the official pinned DPA2-small descriptor was also tried, with a
  small fitting network and two-step fixture. Training reached Triton's CUDA
  discovery, which failed because `/sbin/ldconfig` is absent in the slim SIF.
  It did not pass training. Its NCCL probe succeeded independently.

No multi-GPU training template is published. The successful inference checks
and NCCL probe do not override these failures. The 16-second single-GPU training
check establishes launch and checkpoint behavior, not trained-potential accuracy.
The raw failed job scripts, inputs, errors and accounting remain in `raw/failed`.

Follow-up requirements before accepting multi-GPU training:

1. Fix or adopt an upstream fix for empty compression parameters in single-task
   DDP, then run multiple `se_e2_a` training steps on each supported partition.
2. Provide and validate the runtime tools/libraries needed by Triton/JIT in a new
   SIF/runtime generation; test DPA2 training, rather than assuming `ldconfig`
   is the only missing requirement.
3. Explicitly decide whether to package Horovod for TensorFlow distributed
   training, then test the host MPI path if it is included.

The current site helper starts MPS only for more than one rank/GPU. These templates
default to one rank/GPU; host MPS pipes are not exposed through the current SIF's
isolated `/tmp`, so that MPS path is not accepted here.

`SHA256.json` covers every archived raw file. Local verification passed all
17 SIF tests. The complete repository test suite is run by the PR's CI.
