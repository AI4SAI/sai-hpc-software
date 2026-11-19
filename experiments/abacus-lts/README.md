# ABACUS LTS build pilot

This independent recipe builds an exact commit from upstream `LTS` on DSPRHBM, 16V100, 4V100 and 8V100V0. It reuses the main workflow's file-backed overlay, pinned fuse2fs driver, explicit writeback, offline dependency archives, final-prefix installation and SIF packaging. All computation and writable storage remain under `/org/org-open-sai/proj-sai-hpc-software`.

LTS currently reports `v3.10.1`; its actual identity is the branch commit, recorded separately. It uses `USE_OPENMP`, `USE_ELPA`, `ENABLE_DEEPKS`, the older test layout, and `--version`. Its cuSOLVERMp implementation needs CAL, so GPU builds explicitly select the preinstalled NVHPC 25.7 CUDA 12.9 math libraries while retaining the workflow's site MPI, compiler and ELPA. The newer NCCL/cuBLASMp interfaces and NEP support from develop are not present in LTS.

The enabled profile includes MPI, OpenMP, PW/LCAO, ELPA, LibXC, LibRI/LibComm for HSE, DeePKS with LibTorch 2.1.2, RapidJSON and CUDA/cuSOLVERMp on GPU partitions. Inputs for PW, HSE, DeePKS and cuSOLVERMp checks are packaged for subsequent scientific acceptance. A successful build checks the executable version, actual CMake options and resolved dynamic libraries; scientific correctness is a separate result.

`submit.py` runs on the SAI login host with a staged copy of this repository and a pinned source archive in `cache/abacus-dependencies`. The archive must have been checked against the Git blob identities from the exact upstream commit tree; the resulting receipt and archive SHA-256 enter the frozen recipe fingerprint. For example:

```sh
python3 experiments/abacus-lts/submit.py \
  --source-sha 0d967f1f1bc39c16e3bf435e5f02281c72daaf05 \
  --version LTS-2026-11-16 --controller-sha CONTROLLER_COMMIT --source-record source.json \
  --run-id UNIQUE_RUN_ID --target 16v100-avx2
```

Each run receives immutable controller files and a recipe checksum. Use the staged `software_controller.py monitor RUN_ID` with `SAI_SOFTWARE_ROOT` set to the project root to record its build result. Do not call `publish`: the normal develop acceptance contract deliberately remains unchanged. This experiment neither rotates `develop-beta-later` nor installs files under the physical `/opt` tree.
