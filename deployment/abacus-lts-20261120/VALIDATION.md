# Validation on 2026-11-20

The final administrator package passes `bash install.sh --check`. All four SIFs match the pinned hashes; the raw scientific logs, identities and successful Slurm accounting agree. The full installation was exercised at `/org/org-open-sai/proj-sai-hpc-software/operations/abacus-lts-deploy-20261120/stage`, including extraction, per-file hashes and modes, directory publication and module publication. Every partition contains 16 regular files. The physical `/opt` deployment has not been performed by the maintainer.

Seven targeted installer tests passed. They cover the fixed four-partition identities, changed module detection, different branch/commit/recipe/prefix rejection, unsafe inventory paths, omitted development interfaces, changed/extra installed files and symlink ancestors. Python parsing, shell syntax and `git diff --check` also passed.

On the login node, real Lmod displayed the complete `whatis` and help information without loading dependencies. A load attempt displayed help and failed with the expected request to use a Slurm allocation.

## Staged runtime checks

Host MPI executes the trimmed staged files at their original `/opt` paths in an isolated mount namespace. Only the minimal base SIF is used; no application executable or library is read from an application SIF. The public module is loaded, unloaded and loaded again for each probe. Versions and library resolution are checked before PW, HSE and DeePKS; GPU targets additionally run cuSOLVERMp. This is one MPI rank per check, not multi-node or performance acceptance.

| Partition | Job | Outcome |
| --- | --- | --- |
| DSPRHBM | 2634250 | Completed, 3/3 scientific cases passed; version and ldd passed |
| 16V100 | 2634251 | Completed, 4/4 scientific cases passed; version and ldd passed |
| 8V100V0 | 2634252 | Completed, 4/4 scientific cases passed; version and ldd passed |
| 4V100 | 2634314 | Queued; original SIF build and 4/4 scientific checks already passed |

Completed raw results are under `/org/org-open-sai/proj-sai-hpc-software/operations/abacus-lts-deploy-20261120/checks-20261120-091425/<PARTITION>/results/verification.json`. The pending 4V100 check is under `checks-20261120-091821/4V100/results/verification.json` in the same operation directory.

After the three completed checks, the module's static documentation was extended to include CPU SIMD information. The module runtime body, beginning with its help/whatis return guard, remained byte-identical (SHA-256 `0d93ac7f0df4ac36ff6b4f79e0c613aab5f478a14a0bd1e19b933bd24012bf71`). The tested earlier package metadata is preserved under `validated-package-before-simd-description` in the operation directory and in Git commit `82b371fbf99f48a8ba69c9cb62d371c215d0bb5f`. The final documentation passed real Lmod checks; the final package passed preflight and staged file verification. The queued 4V100 job uses the final package metadata.
