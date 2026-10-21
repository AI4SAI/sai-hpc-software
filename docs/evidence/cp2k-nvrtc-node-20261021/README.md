# CP2K SIGSEGV: damaged NVRTC library on 4v100n06

On 2026-10-21, production job **1889136** failed after eight seconds on
`4v100n06`, in `jit_transpose_handle()` at DBCSR `libsmm_acc.cpp:397`.
The pinned source calls `nvrtcCompileProgram()` at this line. The failure occurs
while compiling a GPU transpose kernel, before the user's initial density
construction finishes. The earlier water failure **1882074** has the same
library call and node.

The node's CUDA toolkit NVRTC file differs from the working installation by
**1,456 bytes**, despite identical size (**106,237,008 bytes**), version
(`12.9.86`) and ELF build ID (`31f50d4d7c1c475a14b8167237b969ac50271fe5`).
The first differing byte is at file offset **10,156,288**. The companion
`libnvrtc-builtins.so.12.9.86` matches the reference. The cause of the file
corruption itself has not been established.

Affected file:

```text
/opt/devtools/nvidia/cuda-12.9.1/targets/x86_64-linux/lib/libnvrtc.so.12.9.86
```

| Copy | SHA-256 |
| --- | --- |
| Broken toolkit file on 4v100n06 | `7903a559640531e1859c53b4d8e0327bd037418fdba3afe04d636dba040ea40a` |
| Working toolkit file on login / 4v100n35 | `c3430e8b6b0d8d3a74f5f839118a85e13e3ad6e4d6203ea1b6a27a0c0b4fe10a` |
| Working HPC SDK file on 4v100n06 | `c3430e8b6b0d8d3a74f5f839118a85e13e3ad6e4d6203ea1b6a27a0c0b4fe10a` |
| Matching NVRTC builtins on both nodes | `6507cf33450cfb22970c2c1e7aa8e7c0a33e39d864720277b05f61a92791e2e8` |

The independent Python/ctypes probe compiles the original DBCSR common and
transpose source, uses the same CUDA flags and `compute_70`, and never launches
CP2K or a GPU kernel. It records the resolved library paths and hashes from
`/proc/self/maps`. The broken file crashes on the first **5 × 5** kernel;
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
faulty toolkit NVRTC file as the cause of the reproduced crash. It does not
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

Administrator repair should restore only the damaged node-local NVRTC file
from the verified same-version SDK copy:

```text
/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/cuda/12.9/targets/x86_64-linux/lib/libnvrtc.so.12.9.86
```

Check source and destination hashes on the affected compute node, preserve the
damaged file for investigation, and replace it when no process is using it.
Then rerun the independent probe and GPU CP2K smoke through the unmodified
public module before removing the node exclusion. Checking only the login
node's `/opt` does not detect this failure. No public repair was performed.

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
