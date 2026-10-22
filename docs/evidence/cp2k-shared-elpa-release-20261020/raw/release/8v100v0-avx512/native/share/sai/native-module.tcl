#%Module1.0
# Native installation; site modules/dependencies remain external.
# Staging is not relocation: deploy at the exact expected prefix below.
module-whatis "cp2k release v2026.2-g67b5da876dd6-raed7aa53ff43; native 8V100V0; expected prefix /opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0; external site dependencies"
if {[module-info mode load] || [module-info mode remove]} {
    if {[module-info mode load]} {
        if {![info exists env(SLURM_JOB_ID)] || ![regexp {^[1-9][0-9]*$} $env(SLURM_JOB_ID)] || ![info exists env(SLURM_JOB_PARTITION)]} {
            error "load this native software module inside a Slurm allocation"
        }
        if {$env(SLURM_JOB_PARTITION) ne "8V100V0"} {error "native installation does not match this Slurm partition"}
        if {[info exists env(SAI_CP2K_NATIVE_PARTITION)] && $env(SAI_CP2K_NATIVE_PARTITION) ne "8V100V0"} {error "another native partition is already loaded"}
        if {[info exists env(SAI_CP2K_PREFIX)] && $env(SAI_CP2K_PREFIX) ne "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0"} {error "another native build is already loaded"}
    } else {
        if {![info exists env(SAI_CP2K_NATIVE_PARTITION)] || $env(SAI_CP2K_NATIVE_PARTITION) ne "8V100V0" || ![info exists env(SAI_CP2K_PREFIX)] || $env(SAI_CP2K_PREFIX) ne "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0"} {error "native unload identity differs from the loaded installation"}
    }
    set sai_native_checked_path "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0"
    if {![file isdirectory $sai_native_checked_path]} {error "missing canonical native installation; deploy at the expected /opt/apps prefix"}
    while {$sai_native_checked_path ne "/"} {
        if {[file type $sai_native_checked_path] eq "link"} {error "native installation path must not contain symlinks"}
        set sai_native_checked_path [file dirname $sai_native_checked_path]
    }
    conflict "cp2k"
    prepend-path MODULEPATH "/opt/modules/modulefiles/devtools"
    if {[llength [info commands depends-on]]} {
        depends-on "openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto"
    } else {
        module load "openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto"
    }
    if {[llength [info commands depends-on]]} {
        depends-on "fftw/3.3.10"
    } else {
        module load "fftw/3.3.10"
    }
    if {[llength [info commands depends-on]]} {
        depends-on "libxc/7.0.0-auto"
    } else {
        module load "libxc/7.0.0-auto"
    }
    if {[llength [info commands depends-on]]} {
        depends-on "saiblas/2603-gnu-auto"
    } else {
        module load "saiblas/2603-gnu-auto"
    }
    if {[llength [info commands depends-on]]} {
        depends-on "cuda/12.9.1"
    } else {
        module load "cuda/12.9.1"
    }
    if {[llength [info commands depends-on]]} {
        depends-on "nvhpc/26.3-gnu-cuda12-tuned"
    } else {
        module load "nvhpc/26.3-gnu-cuda12-tuned"
    }
    if {[llength [info commands depends-on]]} {
        depends-on "nvmplibs/26.7-tmp"
    } else {
        module load "nvmplibs/26.7-tmp"
    }
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/libxs/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/spglib/spglib-2.7.0/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/tblite/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/fftw/fftw-3.3.10/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/libxc/libxc-7.0.0-avx2/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/saiblas/2603-gnu-avx2/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx2/nvidia/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-avx2/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/fftw/fftw-3.3.10/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/libxc/libxc-7.0.0-avx2/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/saiblas/2603-gnu-avx2/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/math_libs/12.9/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/nccl_2.29.3_cuda12.9_sai_v2.29.3-1-sai.2/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/nvshmem/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/sharp/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/cuda-12.9.1/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/mp_libs/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/gcc/14.3.0/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/gcc/14.3.0/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/gcc/14.3.0/libexec"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/spglib/spglib-2.7.0/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/cuda/12.9/compat"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx2/nvidia/lib"
    prepend-path LD_LIBRARY_PATH "/usr/lib/x86_64-linux-gnu/lapack"
    prepend-path LD_LIBRARY_PATH "/usr/lib/x86_64-linux-gnu/blas"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/tiled-mm-2.3.2-f7gzndzouxxc2uvmwgzfa4f3dypvczzz/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/tiled-mm-2.3.2-f7gzndzouxxc2uvmwgzfa4f3dypvczzz/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/spla-1.6.1-z2yhipmv5ulbdeesmn7aygasjeatoju5/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/spla-1.6.1-z2yhipmv5ulbdeesmn7aygasjeatoju5/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/plumed-2.10.1-e3rybix5hx3xnaywsnjne37of7mtn4sq/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/plumed-2.10.1-e3rybix5hx3xnaywsnjne37of7mtn4sq/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/libxsmm-2.0.0-irxk7rpz4roscuefcqo3rddeycnj25b6/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/libxsmm-2.0.0-irxk7rpz4roscuefcqo3rddeycnj25b6/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/libvori-220621-uyxiqsqtzn75sglkxvdsxrcenqpmm4rj/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/libvori-220621-uyxiqsqtzn75sglkxvdsxrcenqpmm4rj/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/libint-2.6.0-r2f3aov4745lee7lpixxbdoynxahf4lc/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/libint-2.6.0-r2f3aov4745lee7lpixxbdoynxahf4lc/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/hdf5-1.14.6-c762spr46tnwvmbfpx6o2723tufh4jlt/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/hdf5-1.14.6-c762spr46tnwvmbfpx6o2723tufh4jlt/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/gcc-runtime-14.3.0-2vmpyoaetw3l5tzfmngiu5c6ruq2glfr/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/gcc-runtime-14.3.0-2vmpyoaetw3l5tzfmngiu5c6ruq2glfr/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/costa-2.3.3-znkbigfp5cbbwu5zn55ptti2nhiynsr3/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/costa-2.3.3-znkbigfp5cbbwu5zn55ptti2nhiynsr3/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/cosma-2.7.0-qx7o5v367h6uydkiiypuva4ly7u3pysc/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/spack/linux-skylake_avx512/cosma-2.7.0-qx7o5v367h6uydkiiypuva4ly7u3pysc/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/tblite/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/dependencies/libxs/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib64"
    setenv CP2K_DATA_DIR "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/share/cp2k/data"
    setenv CP2K_ROOT "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0"
    prepend-path PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/bin"
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib64"]} {prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib64"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib"]} {prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib64"]} {prepend-path LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib64"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib"]} {prepend-path LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/include"]} {prepend-path CPATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/include"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib64/pkgconfig"]} {prepend-path PKG_CONFIG_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib64/pkgconfig"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib/pkgconfig"]} {prepend-path PKG_CONFIG_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/lib/pkgconfig"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/share/man"]} {prepend-path MANPATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0/share/man"}
    prepend-path CMAKE_PREFIX_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0"
    setenv SAI_CP2K_PREFIX "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/8V100V0"
    setenv SAI_CP2K_NATIVE_PARTITION "8V100V0"
}
