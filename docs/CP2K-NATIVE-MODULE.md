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

For a manually maintained site selector, the same selection can be expressed
as follows. Replace `EXACT_BUILD_DIRECTORY` with the directory from the accepted
delivery identity; the production workflow's generated selector remains the
preferred ready-to-install file.

```tcl
#%Module1.0
module-whatis "CP2K 2026.2; native, selected by Slurm partition"
set app_home /opt/apps/cp2k/EXACT_BUILD_DIRECTORY

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
source [file join $app_home $partition share sai native-module.tcl]
```

The selection itself is simply `<base>/$SLURM_JOB_PARTITION`. The surrounding
lines let `module show` work on the login node, reject unsupported partitions,
and unload the previously selected variant even if Slurm variables change.
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
