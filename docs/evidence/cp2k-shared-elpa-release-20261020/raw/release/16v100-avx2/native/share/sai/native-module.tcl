#%Module1.0
# Native installation; site modules/dependencies remain external.
# Staging is not relocation: deploy at the exact expected prefix below.
module-whatis "cp2k release v2026.2-g67b5da876dd6-raed7aa53ff43; native 16V100; expected prefix /opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100; external site dependencies"
if {[module-info mode load] || [module-info mode remove]} {
    if {[module-info mode load]} {
        if {![info exists env(SLURM_JOB_ID)] || ![regexp {^[1-9][0-9]*$} $env(SLURM_JOB_ID)] || ![info exists env(SLURM_JOB_PARTITION)]} {
            error "load this native software module inside a Slurm allocation"
        }
        if {$env(SLURM_JOB_PARTITION) ne "16V100"} {error "native installation does not match this Slurm partition"}
        if {[info exists env(SAI_CP2K_NATIVE_PARTITION)] && $env(SAI_CP2K_NATIVE_PARTITION) ne "16V100"} {error "another native partition is already loaded"}
        if {[info exists env(SAI_CP2K_PREFIX)] && $env(SAI_CP2K_PREFIX) ne "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100"} {error "another native build is already loaded"}
    } else {
        if {![info exists env(SAI_CP2K_NATIVE_PARTITION)] || $env(SAI_CP2K_NATIVE_PARTITION) ne "16V100" || ![info exists env(SAI_CP2K_PREFIX)] || $env(SAI_CP2K_PREFIX) ne "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100"} {error "native unload identity differs from the loaded installation"}
    }
    set sai_native_checked_path "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100"
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
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/libxs/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/spglib/spglib-2.7.0/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/tblite/lib64"
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
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/tiled-mm-2.3.2-wqmiz7l6rdmi3pfr3ra5oubt4tauqtem/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/tiled-mm-2.3.2-wqmiz7l6rdmi3pfr3ra5oubt4tauqtem/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/spla-1.6.1-ubrai56trgmuevwm3thzgn6ky6nmgqkh/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/spla-1.6.1-ubrai56trgmuevwm3thzgn6ky6nmgqkh/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/plumed-2.10.1-np37xjhlxj3qmkfebxzpsypu734ersmo/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/plumed-2.10.1-np37xjhlxj3qmkfebxzpsypu734ersmo/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/libxsmm-2.0.0-62iurh6z3ykiyux6lygqelq5c4aqwttv/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/libxsmm-2.0.0-62iurh6z3ykiyux6lygqelq5c4aqwttv/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/libvori-220621-ox6apfkqq5mdwwkq5upxminesamus7al/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/libvori-220621-ox6apfkqq5mdwwkq5upxminesamus7al/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/libint-2.6.0-ufynbugam3r7u7zghgfuumkuodesgugo/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/libint-2.6.0-ufynbugam3r7u7zghgfuumkuodesgugo/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/hdf5-1.14.6-oatczg6z5jwpt7tsfkayczc5vkcqm7oi/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/hdf5-1.14.6-oatczg6z5jwpt7tsfkayczc5vkcqm7oi/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/gcc-runtime-14.3.0-gpfmwc6iyo4ntxrmm4szhzf5shg7shpi/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/gcc-runtime-14.3.0-gpfmwc6iyo4ntxrmm4szhzf5shg7shpi/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/costa-2.3.3-mei6tamap43oogvx64e6pnv5wd4zt3gr/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/costa-2.3.3-mei6tamap43oogvx64e6pnv5wd4zt3gr/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/cosma-2.7.0-5mo4n2dvf2cbmcfhqswalnp4rvuzh54o/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/spack/linux-zen3/cosma-2.7.0-5mo4n2dvf2cbmcfhqswalnp4rvuzh54o/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/tblite/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/dependencies/libxs/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib64"
    setenv CP2K_DATA_DIR "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/share/cp2k/data"
    setenv CP2K_ROOT "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100"
    prepend-path PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/bin"
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib64"]} {prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib64"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib"]} {prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib64"]} {prepend-path LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib64"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib"]} {prepend-path LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/include"]} {prepend-path CPATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/include"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib64/pkgconfig"]} {prepend-path PKG_CONFIG_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib64/pkgconfig"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib/pkgconfig"]} {prepend-path PKG_CONFIG_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/lib/pkgconfig"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/share/man"]} {prepend-path MANPATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100/share/man"}
    prepend-path CMAKE_PREFIX_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100"
    setenv SAI_CP2K_PREFIX "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/16V100"
    setenv SAI_CP2K_NATIVE_PARTITION "16V100"
}
