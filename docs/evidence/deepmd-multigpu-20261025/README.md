# DeePMD multi-GPU training — 2026-10-25

The workflow's native `/opt` DeePMD 3.2.0 release and the site's existing
`deepmd-kit/3.2.0` environment pass actual two-GPU PyTorch training on all three
V100 partitions. The published development SIF at `558f56962775` fails the same
training cases on all three partitions. Inference acceptance does not imply
multi-GPU training acceptance.

Subsequent [runtime `75ef5179f1e8`](../deepmd-ldconfig-20261025/README.md) fixes
ldconfig discovery and automatic Triton cache placement. DPA2 single-GPU
training/restart now passes on all targets; DPA2 DDP reaches the same independent
unused-parameter defect as `se_e2_a`. The table and raw records below describe
the earlier runtime `a4c6a24c229f`; multi-GPU SIF training remains unaccepted.

| Installation | 16V100 | 4V100 | 8V100V0 |
| --- | --- | --- | --- |
| Workflow native release, `8cfd46e37448`, `se_e2_a` + DPA2 | Pass, 1929966 | Pass, 1929967 | Pass, 1929968 |
| Existing Conda 3.2.0, `se_e2_a` + DPA2 | Pass, 1929966 | Pass, 1929967 | Pass, 1929968 |
| Published beta SIF, `558f56962775`, `se_e2_a` + DPA2 | Fail, 1929948 | Fail, 1929949 | Fail, 1929950 |

Each passing case trains for eight optimizer steps, saves `model.ckpt-8.pt`,
then starts a new torchrun session with `--restart model.ckpt-8.pt` and continues
to step twelve. Both ranks use CUDA, produce finite gradients, update their
parameters, and finish with identical parameter hashes. Learning curves remain
finite and both checkpoints are nonempty. The records contain the checkpoint
hashes; checkpoint binaries remain in the project test directories.

There are twelve passing installation/partition/descriptor combinations, each
with a training and a restart session. Validation RMSE on this small synthetic
fixture drops from 1.08 to 0.900 for `se_e2_a`, and from 0.213 to 0.0836 for DPA2.
These checks establish functional distributed training and checkpoint recovery.
They do not measure scaling, fit a production potential, or validate multi-node,
four-or-more-GPU, mixed-precision, FSDP/ZeRO, or every descriptor configuration.

## Which installation is loaded

`deepmd-kit/3.2.0` loads `/opt/apps/conda_env/deepmd-kit-3.2.0`. It does **not**
select the workflow's native release. The native release has these prefixes:

```text
/opt/apps/deepmd-kit/deepmd-kit-release-v3.2.0-g8cfd46e37448-r296ed3baa11b-s4abbad547102/{16V100,4V100}
/opt/apps/deepmd-kit/deepmd-kit-release-v3.2.0-g8cfd46e37448-r795dbd80e2a4-s4abbad547102/8V100V0
```

The existing public `lammps/30Sep2026-deepmd320-auto` module selects those
prefixes and their accepted Python/library environment. This audit uses that
module for the native release tests; no LAMMPS process participates in training.
The independent beta module selects the development SIF and its private-memory
runtime `a4c6a24c229f`. `raw/site-record.json` archives the actual module text,
SIF catalog entries, runtime hashes and Slurm accounting.

Inside an allocation with two GPUs, the tested existing 3.2.0 environment can
be launched using the ordinary site GPU job template and:

```bash
module load deepmd-kit/3.2.0
"$DEEPMD_PYTHON" -m torch.distributed.run --standalone --nproc_per_node=2 \
    -m deepmd --pt train input.json
```

To select the workflow's installed release through its currently available
module, load `lammps/30Sep2026-deepmd320-auto` instead and use `python -m
torch.distributed.run ...`; its `python` is the partition-specific DeePMD venv.
Do not load both module selections together. PyTorch DDP uses one worker per
GPU; `mpirun dp --pt train` alone does not establish this training group.
No new public sbatch template is installed.

## Why the current SIF fails

The same failures occur on all three partitions using the published module:

- `se_e2_a`: step two fails with unfinished DDP reduction. Parameter indices
  8–11 are empty `compress_info`/`compress_data` placeholders marked trainable.
  The development trainer uses `find_unused_parameters=self.multi_task`, which
  is false for this single-task case. Both installed 3.2.0 trainers use `True`.
- DPA2: Triton's CUDA library discovery invokes `/sbin/ldconfig`, which is absent
  in this slim SIF. Both native installations provide it and complete training.

The audit only observes DDP and gradients; it does not change either trainer,
set DDP compatibility options, disable kernels, or patch a public installation.
A future SIF candidate needs a source fix for the unused compression parameters
and the runtime tools needed by Triton, followed by the same training/restart
checks on each partition. Adding `ldconfig` alone is not an accepted fix until
actual DPA2 training passes.

## Framework capabilities

Job `1930009` probes all three installations with both allocated V100s visible:

| Framework | Actual installed capability |
| --- | --- |
| PyTorch 2.13.0+cu126 | CUDA 12.6; both V100s visible; native DDP training passes |
| TensorFlow 2.21.0 | `is_cuda_build=false`; no GPU devices |
| JAX 0.11.1 | CPU device only |
| Horovod | Absent |

Consequently, **TensorFlow multi-GPU training is unavailable in all three**.
Installing Horovod alone would not supply the missing TensorFlow CUDA backend.
The site's module description mentioning TF/PT/JAX CUDA is broader than these
observed capabilities. No GPU was hidden or disabled for this probe.
`inventory.json` reports metadata for the distribution name `tensorflow` as
null because the installed distribution is `tensorflow_cpu`; the actual
imported TensorFlow version/build information is in `raw/frameworks`.

## Evidence and reproduction

All jobs use account `proj-sai-hpc-software`, one node and two GPUs, with no
`--cpus-per-task` override. New inputs, outputs and native scratch are under
`/org/org-open-sai/proj-sai-hpc-software`; SIF scratch uses its automatic private
tmpfs. The current public SIFs and `/opt` installations are read-only inputs.

- `raw/control`: final observer, fixture generator, input sources and job drivers.
- `raw/native`: final twelve passing cases, rank observations, inputs and logs.
- `raw/sif`: six failed cases through the actual published SIF module.
- `raw/frameworks`: actual TF/PyTorch/JAX/Horovod probes.
- `raw/site-record.json`: exact public module selection and Slurm accounting.
- `raw/harness-r1`, `raw/harness-r2`, and the r3 native logs retained remotely:
  preliminary observer corrections. DeePMD clears gradients before exit, restart
  curves include the loaded checkpoint step, and SIF scripts must be in the bound
  working directory. Final observers account for those behaviors. These
  preliminary harness failures are not classified as application failures.

Remote native evidence is in `runtime-tests/deepmd-multigpu-20261025-r4`; SIF
evidence is in `runtime-tests/deepmd-multigpu-20261025-r3`, under the project root.
Jobs `1929966`/`1929967`/`1929968` completed in 1m55s/47s/2m01s respectively.
The audit driver records failing application exit codes and continues with the
next installation, so Slurm `COMPLETED` alone is not an acceptance signal.
Use the case reports and raw logs. `SHA256.json` covers every archived raw file.
Run `python3 docs/evidence/deepmd-multigpu-20261025/verify.py` to recheck the
archived curves, rank observations, image identities, framework probes and hashes.
