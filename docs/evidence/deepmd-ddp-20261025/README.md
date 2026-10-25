# DeePMD development DDP repair — 2026-10-25

The published `deepmd-kit/master-beta-later` now supports the accepted single-node,
two-GPU PyTorch training route on **16V100, 4V100 and 8V100V0**. Both `se_e2_a`
and DPA2 passed eight training steps, checkpoint save, and a new-process restart
through step 12. Each rank used its own CUDA device, produced finite gradients,
updated the model, and ended with exactly the same parameter hash as its peer.
`find_unused_parameters=False` remains in effect for these single-task runs.

The upstream source is `558f56962775098b8b554e341c3b37d2f86e6aeb`, with the
workflow's explicit [compression-table patch](../../../controller/deepmd_ddp_patch.py).
These inference-only ParameterLists were created as trainable parameters, and
DPA2's outer descriptor also re-enabled their gradients. The patch freezes only
`compress_info` and `compress_data`, after model assembly and before DDP and the
optimizer are constructed. Parameter names, objects, state-dict layout and
optimizer parameter ordering remain intact. Other empty parameters and normal
network weights are not affected. Versions which explicitly enable unused-parameter
handling, including the accepted 3.2.0 release, are left byte-for-byte unchanged
to preserve exact release version discovery. The final patch policy was checked
against both pinned upstream sources in [source-policy.json](validation/source-policy.json).
The installed trainer hash is checked against
the recorded patched source before a build is accepted.

The three new SIFs use build recipe `7d8ce739621511bc42402db94e6b04aceae5577800a34c27bee009e06dbc2568`, corresponding to workflow commit
`fb5013e`. Their installed trainer SHA256 is
`58d336517b6e9e217de9484ae5ab29aff836d97e78ec0376f3d812cfc44f5fa3`.
Original public images and frozen build controllers were preserved. Later
workflow changes correct the acceptance environment and evidence checks; the
application source patch and these immutable SIFs are unchanged between the
independent science and public-runtime checks.

| Partition | Build allocation | Final independent science | SIF module acceptance |
| --- | --- | --- | --- |
| 16V100 | 1930513 | 1930668, completed 0:0 | 1930729, completed 0:0 |
| 4V100 | 1930515 | 1930666, completed 0:0 | 1930730, completed 0:0 |
| 8V100V0 | 1930517 | 1930702, completed 0:0 | 1930731, completed 0:0 |

The original build allocations passed their immutable SIF build gate, then
failed before training because the first acceptance runner read Slurm variables
removed by `--cleanenv`. Those jobs retain their failed status. The corrected
runner uses the workflow's allocation attestations, selects loopback for the
isolated container's rendezvous, and recognizes PyTorch's
`cuda:nccl,cpu:gloo` backend configuration. It completes all six existing
TF/PT/JAX Python/C-API inference groups, measured CUDA execution, and the new
DDP training/restart gates against the exact original SIF hashes. Independent
acceptance hashes bind the inputs, logs, rank reports, checkpoints and normal
`model.ckpt.pt` convenience link to that allocation.

Earlier diagnostic attempts are retained in the accounting and raw logs:
1930491/1930493/1930495 were cancelled before build completion to correct
checkpoint-link hashing; 1930607 was cancelled after an isolated-network DNS
failure; 1930632 completed eight correct two-GPU steps but failed an overly
narrow backend-name assertion. None is presented as completed acceptance.

The module-route jobs additionally passed single-GPU training/restart for both
descriptors, restart from **pre-fix** single-GPU checkpoints, and reference GPU
inference. A freshly trained `se_e2_a` checkpoint was frozen and compressed;
compressed versus uncompressed energy error was zero, with maximum force error
`1.08e-14` and virial error `8.83e-15`. Automatic Triton caches remained inside
private node memory. These six-atom fixtures establish working training,
serialization and numerical consistency; they are not a production-accuracy
or scaling benchmark. Multi-node and more-than-two-GPU training have not been
accepted. TensorFlow and JAX remain CPU-only, with no Horovod.

Publication selects generation `20261025-deepmd-ddp-r2` and fixed module
`deepmd-kit/master-beta-20261025-558f56962775-r75ef5179f1e8`.
The runtime remains `75ef5179f1e8`; its ldconfig and memory-cache fixes are retained.
The new runtime bundle and three SIF copies are under
`/home/stardust/sai-hpc-software/sif-beta/runtimes/20261025-deepmd-ddp-7d8ce7396215`.
Its catalog SHA256 is
`0a01d01d5680b188f570d5b65cae382ecbbfe3b5c3188deec0d8df9da2c93edd`.
Only the DeePMD home alias rotated; the existing static `/opt` gateway selects
it without an administrator reinstall. The other four application aliases were
checked before and after publication. No application tree is extracted at launch.

In an existing job allocated two GPUs, the public command is:

```bash
module load deepmd-kit/master-beta-later
deepmd-python -m torch.distributed.run --standalone --nproc_per_node=2 -m deepmd --pt train input.json
```

Public job **1930769** completed this exact command with DPA2 through the installed
`/opt` module gateway, wrote a nonempty checkpoint, and produced eight finite
learning-curve rows. It used no observation hook or custom training entrypoint.
The administrator preflight and the live module help were also rechecked.

Raw text, numerical records, rank reports, deployment metadata and full
accounting are in [raw](raw); [SHA256.json](SHA256.json) covers the collected
files. Binary models, checkpoints, NumPy data and Nsight reports remain in
`/org/org-open-sai/proj-sai-hpc-software`, with paths and hashes in
[retained-artifacts.json](raw/retained-artifacts.json). All new build and test
allocations used account `proj-sai-hpc-software`; physical host `/tmp` was not
used for cluster scratch. Acceptance scripts and fixtures are repository/project
evidence, outside the public software payload.

The final focused suite covers 34 DeePMD tests and four release-version tests;
the beta-module suite also passed (9). The broader
local run executed 1,087 tests with 14 skips and two unrelated CP2K errors because
`cmake` is unavailable in this workstation environment; its full log is retained.
The corresponding GitHub validation jobs, which have CMake, passed.
