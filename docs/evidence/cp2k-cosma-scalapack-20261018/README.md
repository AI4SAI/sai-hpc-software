# Required COSMA interfaces, October 18

CP2K v2026.2 source `67b5da876dd6a76b8b021d5a04d1c81ba79a4c50` requires
`cosma::cosma_pxgemm_cpp` and `cosma::cosma_prefixed_pxgemm` when COSMA is enabled.
The pinned Spack packages commit `535022224b610cb07dcbf63cef5bd68c6a2aae24`
defaults COSMA's `scalapack` variant to false. Merely finding installed
COSMA/COSTA packages is insufficient.

- [CP2K target check](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/CMakeLists.txt#L716).
- [Spack default and dependent COSTA variant](https://github.com/spack/spack-packages/blob/535022224b610cb07dcbf63cef5bd68c6a2aae24/repos/spack_repo/builtin/packages/cosma/package.py#L50).
- [COSMA target generation](https://github.com/eth-cscs/COSMA/blob/v2.7.0/src/cosma/CMakeLists.txt#L84).
- [Failure snapshot](failure.json): Slurm accounting, log hashes/tails and the
  actual COSMA/COSTA lock nodes for workflow `39041714911`. State is timestamped;
  active allocations in this snapshot may subsequently finish.

Fix `e8c096903b8b79806da47dd280d2de3ccd0c68d9` adds `+scalapack` to the COSMA root
spec for every target. Its dependency constraint selects `costa+scalapack`;
both use the existing standalone ScaLAPACK provider. The source, GCC 14.3.0,
CUDA SM70, and GPU SpLA GEMM offload requirements remain the same.

Validation: nine environment tests passed locally. GitHub
[39047197403](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39047197403)
ran 722 tests in 33.252 seconds: 720 passed, two optional skips. SAI's development
checkout was fast-forwarded to the same commit. The actual release retry is
[39047391019](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39047391019).

Diagnostic `1833202` in `runs/cp2k-cosma-preflight-20261018-b` completed `0:0`
in 43 seconds. It built both required COSMA libraries and completed CP2K's
full configure/generate steps using the production script's arguments and
the diagnostic library locations. GCC 14.3.0, CUDA SM70, cuSOLVERMp, ELPA,
SpLA GEMM offload and the other declared dependencies were resolved. Full
before/after hashes of the read-only failed application image matched.
[Logs, inputs, library hashes, caches and fresh four-target variant checks](cosma-full-configure-passed-20261018.json).

The preceding diagnostic `1833080` failed because the old image no longer
contained the assumed COSTA source directory. The corrected diagnostic
extracts the same versioned, SHA256-checked source archives from the home
cache. That failed probe also preserved the complete image hash.

At 00:08, old DSPRHBM application `1832921` and 8V100V0 application `1833122`
were deliberately cancelled after checking their exact job handles and
confirming both lacked the required interfaces. The corrected workflow was
already running. All files and successful dependency checkpoints remain;
these cancellations do not claim a natural application failure or success.
[Cancellation decision](superseded-cosma-applications-20261018.json).

Diagnostic compile `1833253` ran ahead of the fresh dependency builds and
failed at the release/development TBLITE API mismatch, documented in the
[subsequent repair](../cp2k-tblite-release-20261018/README.md).
These diagnostics do not create a new
release identity, an accepted SIF or a performance result.
