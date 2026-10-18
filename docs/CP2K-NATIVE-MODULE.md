# CP2K native installation and partition module

The current build-time prefix is already
`/opt/apps/cp2k/<exact-build-directory>/<PARTITION>/`. Physical installation
must preserve this exact absolute prefix, including private dependency paths
and CP2K data: the installed binaries and libraries can contain hardcoded
paths. Extract the complete accepted runtime tree at that prefix and load a
single native module inside the Slurm job. The runtime executes
`cp2k.psmp` directly. The application module selects a partition directory;
its packaged Tcl fragment sets the matching dependencies, library paths and
`CP2K_DATA_DIR`.

The reviewed site module is
`/opt/modules/modulefiles/apps/abacus/develop-git-079fd0c-260724-sm70-auto`.
It loads shared OpenMPI, FFTW, LibXC, SAIBLAS, ELPA and NVIDIA math modules and
adds one fixed application `bin` directory to PATH. Its `abacus` is a symlink
to a single `abacus_basic_gpu` executable. There is no ABACUS partition
selection in this file. The `-auto` dependencies inspect the current CPU:
AVX512_VNNI chooses their AVX512 build, otherwise AVX2 chooses AVX2.
[Read-only site evidence](evidence/cp2k-install-layout-20261018/site-abacus-module-review.json).

For CP2K, use explicit Slurm partition selection instead of copying that fixed
application path. The four independently compiled variants remain separate:

```text
/opt/apps/cp2k/<exact-build-directory>/
  4V100/
  16V100/
  8V100V0/
  DSPRHBM/
```

Each partition contains `bin/cp2k.psmp`, `lib`/`lib64`, `dependencies` (including
private ELPA and the selected Spack runtime), CP2K data and `share/sai` metadata.
Preserve the whole runtime export and its directory structure. Moving only the
executable and top-level libraries loses private dependency paths and data.
Preserving the original absolute prefix also preserves recorded runtime paths.
A shorter public module name, such as `cp2k/2026.2-auto`, is independent of this
immutable installation directory name.

The application CPU variants and dependency ISA selections differ. For example,
8V100V0's CP2K uses native AVX512 while its site MPI/BLAS dependencies use AVX2.
The packaged runtime fragment already records this combination. New private-ELPA
deliveries use their own `dependencies/elpa`; do not copy the reference ABACUS
module's shared `elpa/2026.02.001-2603-gnu` dependency into the new CP2K module.
GPU runtime entries also retain the tested CUDA compatibility library priority.

HCOLL is a runtime dependency of the site's OpenMPI. The physical shared
installation is:

```text
/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll/lib
```

The generated native module already records that directory and the adjacent
`sharp/lib` explicitly. Native installation therefore does not require creating
`/opt/mellanox/hcoll`. The site's MPI `.la` files refer to that legacy absolute
path during linking, and `libmpi.so.40` also retains it as a RUNPATH fallback.
Its actual HCOLL dependency is the SONAME `libhcoll.so.1`, so the recorded real
library directory takes precedence over that RUNPATH at runtime.

The build alias is now restricted to compilation. Export, final SIF checks and
the runtime launcher do not mount it. Newly built private ELPA libraries have
the inherited alias removed with `chrpath` before hashes and manifests are
written; all other loader paths are preserved. Runtime checks reject that alias
in packaged ELF paths and native environment entries and require the real
HCOLL/SHARP directories and actual HPCX HCOLL linkage. `chrpath` is a build tool,
not a native runtime dependency.

Existing accepted artifacts and their frozen controllers remain unchanged.
Their private ELPA also contains the legacy RUNPATH fallback. Job `1841692`
executed the actual 16V100 native export at its exact prefix in a private mount
namespace, using host system libraries and the generated native module, with
the legacy HCOLL directory empty. Version/loader checks and PBE OT, ordinary
diagonalization and forced ELPA all passed. Loader traces show HCOLL and OCOMS
loaded from the real HPCX directory; installed CP2K data were used, and energy
and forces matched the accepted SIF. No application SIF was executed and no
physical `/opt` directory was changed. This establishes the tested native
runtime path without claiming administrator installation or full native
multi-partition acceptance. Follow-up job `1841817` completed `0:0` in 1:24
on `16v100n49,50`: all three cases also passed on two MPI nodes. Each rank
independently checked alias absence and recorded actual HPCX HCOLL loading;
energies differed from the accepted two-node SIF by at most `1.43e-14 Ha`,
with identical printed forces.
[Native execution evidence](evidence/cp2k-install-layout-20261018/hcoll-native-alias-free.json),
[two-node native execution](evidence/cp2k-install-layout-20261018/hcoll-native-multinode-alias-free.json),
[normalization of a diagnostic copy of the real ELPA](evidence/cp2k-install-layout-20261018/real-elpa-normalization.json).

The workflow already packages a complete partition selector at:

```text
<PARTITION>/modulefiles/cp2k/<track>/<build_id>
```

After installing the accepted runtime trees, copy that selector into the site
module directory under the desired public name. It is identical across
partitions of the same build. It sources the selected partition's
`share/sai/native-module.tcl`, requires that partition to be installed, and
supports unload using the saved partition. No new module generator is needed.
The existing generator also checks canonical paths and build consistency.

The four accepted 2026.2 deliveries share source `67b5da876dd6`, but the CPU
HCOLL recovery changed its recipe and therefore its build directory. Keep
both exact directory names: GPU partitions use
`cp2k-release-v2026.2-g67b5da876dd6-r4d454c740309`, while DSPRHBM uses
`cp2k-release-v2026.2-g67b5da876dd6-rfb4adb0598fc`. Do not rename the CPU tree
to match the GPU tree. A single public `cp2k/2026.2-auto` module can explicitly
map the four partitions to these accepted prefixes; a generated selector for
just one build identity does not cover both identities.

For these accepted deliveries, the site selector can be written as follows.
Install each complete `.native` payload at its recorded prefix before making
this module public. This is a deployment example, not a claim that physical
installation has been performed.

```tcl
#%Module1.0
module-whatis "CP2K 2026.2; native, selected by Slurm partition"
set app_home /opt/apps/cp2k
set gpu_build cp2k-release-v2026.2-g67b5da876dd6-r4d454c740309
set cpu_build cp2k-release-v2026.2-g67b5da876dd6-rfb4adb0598fc
set builds [dict create DSPRHBM $cpu_build 4V100 $gpu_build \
    16V100 $gpu_build 8V100V0 $gpu_build]

if {[module-info mode load]} {
    if {![info exists env(SLURM_JOB_PARTITION)]} {
        error "Load CP2K inside the Slurm job or allocation"
    }
    set partition $env(SLURM_JOB_PARTITION)
} elseif {[module-info mode remove]} {
    set partition $env(SAI_CP2K_NATIVE_PARTITION)
} else {
    return
}
if {$partition ni {DSPRHBM 4V100 16V100 8V100V0}} {
    error "Unsupported CP2K partition: $partition"
}
source [file join $app_home [dict get $builds $partition] $partition \
    share sai native-module.tcl]
```

The map selects the accepted build directory for `$SLURM_JOB_PARTITION`.
The surrounding lines let `module show` work on the login node, reject
unsupported partitions, and unload the previously selected variant even if
Slurm variables change.
Do not source `runtime-env.sh` in Tcl: that is a shell file, while the packaged
`native-module.tcl` is the appropriate module interface.

Load the module within the batch script, after the allocation is established:

```bash
module use /opt/modules/modulefiles/apps
module load cp2k/2026.2-auto
mpirun ... cp2k.psmp -i input.inp
```

Keep the allocation's existing MPI rank, GPU mapping and thread choices. This
module selects software and its dependencies; it does not select resources or
set a workload-specific thread count. Native execution should receive a final
loader/version and PBE smoke check after the administrator installs the exact
accepted prefixes. The separate diagnostic SIF plus ELPA bind test does not
establish that physical installation check.
