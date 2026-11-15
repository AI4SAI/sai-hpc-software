# DeePMD 3.2.0: use the existing SAI NCCL

The installed `deepmd-kit/3.2.0` module selects SAI NCCL through the NVHPC module, but PyTorch's embedded DT_RPATH loads its bundled upstream library. Both libraries report NCCL 2.29.3; the version string alone does not verify the SAI dual-rail patch. Native two-GPU testing reproduced the cross-subnet IB retry failure.

This package adds one setting to `/opt/modules/modulefiles/apps/deepmd-kit/3.2.0`, immediately after loading Open MPI:

```tcl
prepend-path LD_PRELOAD /opt/devtools/nvidia/nccl_2.29.3_cuda12.9_sai_v2.29.3-1-sai.2/lib/libnccl.so.2
```

It keeps the existing two-rail policy. The DeePMD/PyTorch binaries and bundled libraries are not replaced. The tested module preserves an existing preload entry and removes only its own entry on unload. New processes using this loaded module inherit the selection, including torchrun workers. Direct conda activation and the independent daily SIF entry points need their own library selection; changing this native module does not repair them automatically.

## Administrator installation

The prepared package is readable at `/home/stardust/sai-hpc-software/deployment/deepmd-site-nccl-20261115`:

```bash
cd /home/stardust/sai-hpc-software/deployment/deepmd-site-nccl-20261115
bash install.sh --check
bash install.sh --install
```

Default operation and `--check` are read-only. Installation verifies the exact reviewed module and SAI NCCL SHA256, saves the previous module under the installing account's `~/sai-module-backups/deepmd-nccl-20261115/`, and replaces the module atomically. Repeated installation is a no-op. A later administrator edit stops installation rather than being overwritten.

The original file is owned by `entropy`; its parent directory is group-writable by `sai-partner`, which includes `saiuser01`. An authorized group maintainer can perform the atomic replacement, which will then be owned by that maintainer. Running as root preserves the previous owner and group. The installer never changes application-directory ownership or permissions.

For new jobs, load `deepmd-kit/3.2.0` in the usual way. In a shell where it is already loaded, unload and reload it. Already-running processes keep their existing libraries.

To restore the reviewed original module:

```bash
bash install.sh --rollback
```

## Validation

All computations used native `/opt/apps/conda_env/deepmd-kit-3.2.0`, two allocated V100 GPUs, and project storage under `/org/org-open-sai/proj-sai-hpc-software/operations/diagnostics/deepmd-native-nccl-20261115`. No container was used for this validation.

- Slurm 2483437, 16V100: the original module failed forced-IB communication; the candidate module passed length broadcast, all-reduce, and object broadcast. Both retained the complete two-rail HCA list.
- Slurm 2483571, 16V100: both `se_e2_a` and `dpa2` passed training and checkpoint restart with normal transport selection and with IB forced. The SAI patch activation was logged, and module unload preserved a pre-existing preload entry.
- Slurm 2483659, 8V100V0: the same eight training/restart checks and module unload check passed.
- Slurm 2483656, 4V100: submitted; pending partition capacity at the time of preparation. It is not counted as passed.

The first native training diagnostic used a very long TMPDIR and hit PyTorch's Unix-domain socket pathname limit. An isolated shared-tensor probe reproduced that error; a short private `/dev/shm` directory passed. Successful training checks used that short in-memory scratch directory; this diagnostic-path fix is not added to the public module. Persistent inputs, checkpoints and evidence stayed in project storage.

The old `deepmd-kit/3.1.2` module selects another environment with NCCL 2.28.9 and is outside this update. This package does not claim multi-node qualification or fix every independently launched Python environment.

Detailed results and checksums: `../../docs/evidence/deepmd-native-nccl-20261115/REPORT.md`.
