# CP2K SIGSEGV: inconsistent NFS client cache on 4v100n06

On 2026-10-21, production job **1889136** failed after eight seconds on
`4v100n06`, in `jit_transpose_handle()` at DBCSR `libsmm_acc.cpp:397`.
The pinned source calls `nvrtcCompileProgram()` at this line. The failure occurs
while compiling a GPU transpose kernel, before the user's initial density
construction finishes. The earlier water failure **1882074** has the same
library call and node.

The bytes read from the CUDA toolkit NVRTC file through `4v100n06` differ from
the working reference by
**1,456 bytes**, despite identical size (**106,237,008 bytes**), version
(`12.9.86`) and ELF build ID (`31f50d4d7c1c475a14b8167237b969ac50271fe5`).
The first differing byte is at file offset **10,156,288**. The companion
`libnvrtc-builtins.so.12.9.86` matches the reference. These reads alone do not
establish damage to the shared server's stored file or to a node-local disk.

Follow-up mount inspection shows that `/opt/devtools` is **NFS 4.2 over RDMA**,
exported as `sai-apprepo:/opt/devtools` from `10.100.0.216`. It is not a
node-local installation. `/home` is a separate Lustre filesystem. Earlier
wording calling this a damaged node-local file, and the recommendation to
overwrite that file, are withdrawn: the follow-up isolates an inconsistent
client page-cache read, as detailed below.

## Follow-up: buffered reads disagree with direct reads on the same client

At **15:17:35 China time**, read-only Slurm job **1890009** on `4v100n06`
completed `0:0` and read the toolkit file three times:

| Read | SHA-256 | Result |
| --- | --- | --- |
| Ordinary buffered read | `7903a559640531e1859c53b4d8e0327bd037418fdba3afe04d636dba040ea40a` | Abnormal bytes |
| `dd iflag=direct` (`O_DIRECT`, bypass client page cache) | `c3430e8b6b0d8d3a74f5f839118a85e13e3ad6e4d6203ea1b6a27a0c0b4fe10a` | Expected bytes |
| Second ordinary buffered read | `7903a559640531e1859c53b4d8e0327bd037418fdba3afe04d636dba040ea40a` | Abnormal bytes persist |

All reads returned the complete **106,237,008 bytes** without errors. The
same export, file inode **1738756**, size, mtime and ctime were observed before
and after. The SDK file (inode **6162926**) returns the expected hash in all
three reads on this node. Direct reading did not repair the anomalous cache.

The identical ordinary/direct/ordinary checks on **login-01, 4v100n16,
16v100n01 and 16v100n27**, at 15:13 China time, returned the expected bytes for
both files in every read. These clients all mount the same NFS server/export
and identify the toolkit file as inode **1738756**. Earlier runtime tests on
**4v100n35** also returned the expected toolkit bytes and passed compilation.

The affected client's cached-read path is therefore inconsistent with the
data currently returned by NFS. This is not a normal consequence of shared
storage: each client has its own in-memory file page cache, and this client's
cached contents are wrong. The evidence does **not** establish whether RAM,
kernel/NFS-client behavior, an earlier RDMA transfer, or another event caused
the cache corruption. Direct I/O bypasses the client's page cache, not the
server's own caches; it is not a storage-media health test.

Only `4v100n06` has shown this anomaly among the inspected clients. This is
**not a full-cluster scan** and does not certify other files or unchecked nodes.
Direct server-side SSH inspection was unavailable to the `stardust` account;
the server data conclusion is based on the successful NFS direct reads from
all five checked clients, including `4v100n06` itself.

The [storage-followup/](storage-followup/) JSON records retain mounts, file
identity, timestamps, all hashes and read exit statuses. The read-only helper
is [storage_probe.py](../../../deployment/cp2k-nvrtc-diagnostic/storage_probe.py).

On **2026-10-22 at 00:32 China time**, additional checks on **d9470n02**
(DSPRHBM job **1893772**, `COMPLETED 0:0`) and repeated checks on **login-01,
4v100n16 and 16v100n27** returned the expected bytes on all three read paths
for both files. The export, toolkit inode **1738756**, size and timestamps
still match. These records are in
[recheck-20261022/](storage-followup/recheck-20261022/).
The repeat on `4v100n06` (**1893770**) and the additional 8V100V0 check
(**1893771**) are pending resources in the archived scheduler snapshot;
neither is counted as healthy or as a completed repeat. The previous
`4v100n06` failure remains unresolved until a new read and runtime acceptance
demonstrate recovery.

The [administrator guide](../../../deployment/cp2k-nvrtc-diagnostic/ADMIN-RECOVERY.md)
contains commands to collect the same read-only evidence across all four
CP2K partitions using an account with node access, followed by the maintenance
and validation sequence. This investigation has not scanned every node.

Affected file:

```text
/opt/devtools/nvidia/cuda-12.9.1/targets/x86_64-linux/lib/libnvrtc.so.12.9.86
```

| Copy | SHA-256 |
| --- | --- |
| Abnormal buffered toolkit read on 4v100n06 | `7903a559640531e1859c53b4d8e0327bd037418fdba3afe04d636dba040ea40a` |
| Working toolkit file on login / 4v100n35 | `c3430e8b6b0d8d3a74f5f839118a85e13e3ad6e4d6203ea1b6a27a0c0b4fe10a` |
| Working HPC SDK file on 4v100n06 | `c3430e8b6b0d8d3a74f5f839118a85e13e3ad6e4d6203ea1b6a27a0c0b4fe10a` |
| Matching NVRTC builtins on both nodes | `6507cf33450cfb22970c2c1e7aa8e7c0a33e39d864720277b05f61a92791e2e8` |

The independent Python/ctypes probe compiles the original DBCSR common and
transpose source, uses the same CUDA flags and `compute_70`, and never launches
CP2K or a GPU kernel. It records the resolved library paths and hashes from
`/proc/self/maps`. Loading the anomalous bytes crashes on the first **5 × 5** kernel;
the working file passes **20 compilations** across ten matrix sizes.

| Job | Node / libraries | Independent NVRTC | CP2K result |
| --- | --- | --- | --- |
| 1889229 | 4v100n35, public module | 20/20 passed | Original 343-water reproduction passed; 64-water OT passed with DBCSR GPU enabled and disabled |
| 1889230 | 4v100n06, public module | SIGSEGV / 139 | 343-water and 64-water GPU runs reproduce the same SIGSEGV; 64-water OT passes with `DBCSR_RUN_ON_GPU=0` |
| 1889246 | 4v100n06, normal NVRTC copy in home | 20/20 passed | Original 343-water reproduction and two-water OT both pass with DBCSR GPU enabled; two-water fallback also passes |
| 1889247 | 4v100n35, public module | 20/20 passed | Same 343-water and two-water controls pass |
| 1889255 | 4v100n06, read-only byte comparison | No compilation | Confirms 1,456 differing bytes and intact same-version SDK copy |

The controlled replacement used only a private directory containing NVRTC and
its identical builtins, prepended to `LD_LIBRARY_PATH`. The public CP2K binary,
driver, input and installed `/opt` files were unchanged. This establishes the
anomalous toolkit NVRTC contents seen by this client as the cause of the
reproduced crash. It does not
require a CP2K rebuild, an ELPA change or a change to OT/diagonalization settings.

The 343-water case deliberately stops after three SCF steps and allows
nonconvergence: it is a crash reproduction, not scientific acceptance.
The independent converged ENERGY_FORCE controls give:

| Comparison | Absolute energy difference (Ha) | Maximum force difference (Ha/bohr) |
| --- | ---: | ---: |
| Normal 4v100n35 GPU vs 4v100n06 DBCSR fallback, 64 waters | 1.2733e-11 | 1.1000e-11 |
| Normal 4v100n35 vs 4v100n06 with working NVRTC, two waters, GPU enabled | 3.6238e-13 | 1.0000e-12 |
| 4v100n06 working NVRTC, GPU vs DBCSR fallback, two waters | 3.0553e-13 | 1.0000e-12 |

The user's full CELL_OPT input was not rerun: `stardust` cannot read that
account's calculation directory. The reproduction uses repository water
fixtures, not the user's research coordinates. These small controls establish
the workaround's numerical agreement, not full CELL_OPT acceptance or a
performance result. No broader claim about other nodes' library integrity is made.

For immediate production use, add this among the script's Slurm directives:

```bash
#SBATCH --exclude=4v100n06
```

This retains full GPU acceleration. If the affected node must be used before
repair, the tested fallback is to add this after loading the module and before
`mpirun`:

```bash
export DBCSR_RUN_ON_GPU=0
```

Use integer `0`: DBCSR parses logical environment settings as integers.
This disables DBCSR GPU multiplication/transposes and may reduce performance;
other CP2K GPU components remain enabled. Remove it after repair.

The verified same-version SDK path contains the expected bytes when read by
the affected node, but it is also on the shared NFS export:

```text
/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/cuda/12.9/targets/x86_64-linux/lib/libnvrtc.so.12.9.86
```

Do not overwrite the shared toolkit file to address this client-cache anomaly.
The administrator should preserve the evidence, remove the affected node from
new allocations, and inspect its memory/kernel/NFS/RDMA state. When its existing
jobs can safely be stopped, invalidate/reinitialize the affected client's cache
or mount under the site's maintenance procedure. Then require matching buffered
and direct hashes, independent NVRTC compilation, and GPU CP2K smoke through the
unmodified public module before removing the node exclusion. Checking only the
login node's `/opt` does not detect this failure. No shared file was changed, and
no cache drop, remount, drain or reboot was performed by this investigation.

Raw files are under [raw/](raw/) and retained on the cluster at
`/home/stardust/sai-hpc-software/runtime-tests/cp2k-nvrtc-20261021`.
[summary.json](raw/summary.json) records per-case results, science differences
and scheduler states; [n06-byte-comparison.json](raw/n06-byte-comparison.json)
records library integrity. The frozen diagnostic scripts are in `raw/probe/`.

The early diagnostic wrapper recorded per-case failures while returning zero
after finishing collection; therefore `COMPLETED 0:0` for **1889230** is not a
successful GPU test. Its three failing subprocess exit statuses are preserved.
The maintained wrapper now propagates failures. Initial setup job **1889222**
stopped because an optional `/proc/sys` read was denied; pending **1889223** was
cancelled before the corrected wrapper was submitted. Neither is evidence of a
CP2K or NVRTC failure.
