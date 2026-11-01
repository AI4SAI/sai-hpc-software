# NEPAdapters 1.0.4 release evidence

Source: `MagTheoryLab/NEPAdapters` v1.0.4,
`c934e71444cb4099d63dff98478f02c86373b061`.
LAMMPS ABI: installed stable_30Sep2026,
`8de817dd79bfe4525d5d39246a212d833e6dee07`.
Builds use GCC 14.3, CUDA 12.9.1, Kokkos 5.2.1, C++20 and SM70.

The original accepted LAMMPS overlay is mounted read-only. NEPAdapters
compiles and installs inside a new sparse project-backed overlay, using the
existing optimized, checksum-pinned fuse2fs and explicit synchronization.
Only frontend Kokkos type/communication names are patched for LAMMPS 2026;
upstream double-precision interface assertions remain enabled.

| Partition | SIF build job | Native single/two-GPU job | Native two-node job | Result |
| --- | --- | --- | --- | --- |
| 16V100 | 2099294 | 2099593 | 2099594 | All completed with exit 0; raw numerical, binary and device evidence revalidated. |
| 8V100V0 | 2099298 | 2099597 | 2099598 | All completed with exit 0; raw numerical, binary and device evidence revalidated. |
| 4V100 | 2099850 | 2100464 | 2100465 | All completed with exit 0; raw numerical, binary and device evidence revalidated. |

Acceptance uses the upstream committed eight-atom Pb/Te NEP4 fixture,
translated across domain boundaries so both MPI ranks own interacting atoms.
Both CPU and GPU outputs are compared with the committed labels. Thresholds
are 1e-6 eV for total/per-atom-summed energy, 1e-6 eV/angstrom for each force
component and 5e-6 eV for summed per-atom virial components. NaN, missing atoms,
incorrect GPU execution and changed raw outputs are rejected. Every layout
also finishes 100 finite GPU MD steps. This is correctness evidence, not a
performance benchmark or acceptance of every spin/NEP5 model.

The accepted GPU energy error is approximately 1.91e-7 eV and maximum force
component error 2.76e-7 eV/angstrom. All three partitions pass native multi-node
and single-node two-device cases. Per-rank wrappers select distinct allocated
GPU UUIDs, record the exact LAMMPS executable hash and exec the physical
binary; no container launcher is involved in native acceptance.

LAMMPS 2026 changes `gpu/aware off` plus multiple MPI ranks into legacy
communication, which cannot supply NEPAdapters' newton-off per-atom virial
reverse communication. The accepted multi-rank route uses the site's
CUDA-aware Open MPI and `comm device gpu/aware on`, as recorded in module help.
The first 4V100 attempt 2099369 failed during overlay mounting before compilation;
the fresh retry 2099850 passed. Earlier failed attempts and their raw logs
remain in the project; they are not accepted or relabelled as successful.

SIF identities and all runtime file hashes are in the committed
[release plan](../../../deployment/nepadapters-1.0.4/release.json).
Native evidence and SIF receipts are copied into the public package's
`evidence/` directory, with checksums in that plan. Complete raw cases remain
in the `native` directories recorded there.

The complete three-partition handoff lives at
`/home/stardust/sai-hpc-software/deployment/nepadapters-1.0.4-all-v100` because
`saiuser01` cannot traverse the funded project's ACLs. The package and every
ancestor permit traversal/read access; installation preflight does not read
private build directories. Build and compute scratch stay under
`/org/org-open-sai/proj-sai-hpc-software`.

Read-only installer preflight passed as `saiuser01` for all three partitions.
All three SIF checksums still match the release plan; each image is about
18.56 MiB. Real Lmod
help displays the version, exact supported partitions and launch options;
loading on a login node prints this help and fails before loading dependencies.
The administrator still runs `bash install.sh --install`. Only two `.so` files
and `share/licenses/nepadapters/LICENSE.NEP_CPU` enter each `/opt` prefix.

The final 4V100 evidence comes from `nepadapters-1.0.4-4V100-20261101-a5-native2`,
whose two jobs are recorded above and in the package's native receipt. The
earlier two-partition package remains intact; the complete package retains
its accepted binaries and permits extending only its known module hash.
Administrator installation and subsequent checks against the real `/opt`
module remain separate from the completed SIF/native export acceptance.
