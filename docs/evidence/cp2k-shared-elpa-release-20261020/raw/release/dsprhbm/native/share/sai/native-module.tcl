#%Module1.0
# Native installation; site modules/dependencies remain external.
# Staging is not relocation: deploy at the exact expected prefix below.
module-whatis "cp2k release v2026.2-g67b5da876dd6-raed7aa53ff43; native DSPRHBM; expected prefix /opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM; external site dependencies"
if {[module-info mode load] || [module-info mode remove]} {
    if {[module-info mode load]} {
        if {![info exists env(SLURM_JOB_ID)] || ![regexp {^[1-9][0-9]*$} $env(SLURM_JOB_ID)] || ![info exists env(SLURM_JOB_PARTITION)]} {
            error "load this native software module inside a Slurm allocation"
        }
        if {$env(SLURM_JOB_PARTITION) ne "DSPRHBM"} {error "native installation does not match this Slurm partition"}
        if {[info exists env(SAI_CP2K_NATIVE_PARTITION)] && $env(SAI_CP2K_NATIVE_PARTITION) ne "DSPRHBM"} {error "another native partition is already loaded"}
        if {[info exists env(SAI_CP2K_PREFIX)] && $env(SAI_CP2K_PREFIX) ne "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM"} {error "another native build is already loaded"}
    } else {
        if {![info exists env(SAI_CP2K_NATIVE_PARTITION)] || $env(SAI_CP2K_NATIVE_PARTITION) ne "DSPRHBM" || ![info exists env(SAI_CP2K_PREFIX)] || $env(SAI_CP2K_PREFIX) ne "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM"} {error "native unload identity differs from the loaded installation"}
    }
    set sai_native_checked_path "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM"
    if {![file isdirectory $sai_native_checked_path]} {error "missing canonical native installation; deploy at the expected /opt/apps prefix"}
    while {$sai_native_checked_path ne "/"} {
        if {[file type $sai_native_checked_path] eq "link"} {error "native installation path must not contain symlinks"}
        set sai_native_checked_path [file dirname $sai_native_checked_path]
    }
    conflict "cp2k"
    prepend-path MODULEPATH "/opt/modules/modulefiles/devtools"
    if {[llength [info commands depends-on]]} {
        depends-on "nvhpc/26.3-gnu-cuda12-tuned"
    } else {
        module load "nvhpc/26.3-gnu-cuda12-tuned"
    }
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
        depends-on "gcc/14.3.0"
    } else {
        module load "gcc/14.3.0"
    }
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/libxs/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/spglib/spglib-2.7.0/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/tblite/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/fftw/fftw-3.3.10/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/libxc/libxc-7.0.0-avx512/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/saiblas/2603-gnu-avx512/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/math_libs/12.9/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/nccl_2.29.3_cuda12.9_sai_v2.29.3-1-sai.2/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/nvshmem/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/sharp/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-avx512/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/fftw/fftw-3.3.10/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/libxc/libxc-7.0.0-avx512/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/saiblas/2603-gnu-avx512/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/nvidia/cuda-12.9.1/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/gcc/14.3.0/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/gcc/14.3.0/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/gcc/14.3.0/libexec"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/spglib/spglib-2.7.0/lib"
    prepend-path LD_LIBRARY_PATH "/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu/lib"
    prepend-path LD_LIBRARY_PATH "/usr/lib/x86_64-linux-gnu/lapack"
    prepend-path LD_LIBRARY_PATH "/usr/lib/x86_64-linux-gnu/blas"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/spla-1.6.1-uxed4dw7umencfxxq4gyzacu6r65lbkz/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/spla-1.6.1-uxed4dw7umencfxxq4gyzacu6r65lbkz/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/plumed-2.10.1-qwwau7jv5yf5i3ok3c24ysm4ibpxpllg/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/plumed-2.10.1-qwwau7jv5yf5i3ok3c24ysm4ibpxpllg/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/libxsmm-2.0.0-l5muhjnzczl5peiem2fwiixbqjnzm33l/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/libxsmm-2.0.0-l5muhjnzczl5peiem2fwiixbqjnzm33l/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/libvori-220621-tfxodmxhdmcxuxdc456sablcstwrxpii/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/libvori-220621-tfxodmxhdmcxuxdc456sablcstwrxpii/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/libint-2.6.0-4dtgfsz4ycma7uwhbp5mbe6g3aqsqwmb/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/libint-2.6.0-4dtgfsz4ycma7uwhbp5mbe6g3aqsqwmb/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/hdf5-1.14.6-nzurbzn7nvb3lu6p33d5wu7aq5ezqydb/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/hdf5-1.14.6-nzurbzn7nvb3lu6p33d5wu7aq5ezqydb/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/gcc-runtime-14.3.0-hkx3nzpcza3hnke7x3dzgo2znpnqclp4/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/gcc-runtime-14.3.0-hkx3nzpcza3hnke7x3dzgo2znpnqclp4/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/costa-2.3.3-atwoealp6a2oujemkdh23i7qgfho3l7x/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/costa-2.3.3-atwoealp6a2oujemkdh23i7qgfho3l7x/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/cosma-2.7.0-goq7bmlsruxt5lhhemq4ytm366mnfw5l/lib64"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/spack/linux-sapphirerapids/cosma-2.7.0-goq7bmlsruxt5lhhemq4ytm366mnfw5l/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/tblite/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/dependencies/libxs/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib"
    prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib64"
    setenv CP2K_DATA_DIR "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/share/cp2k/data"
    setenv CP2K_ROOT "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM"
    prepend-path PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/bin"
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib64"]} {prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib64"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib"]} {prepend-path LD_LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib64"]} {prepend-path LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib64"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib"]} {prepend-path LIBRARY_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/include"]} {prepend-path CPATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/include"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib64/pkgconfig"]} {prepend-path PKG_CONFIG_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib64/pkgconfig"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib/pkgconfig"]} {prepend-path PKG_CONFIG_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/lib/pkgconfig"}
    if {[file isdirectory "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/share/man"]} {prepend-path MANPATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM/share/man"}
    prepend-path CMAKE_PREFIX_PATH "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM"
    setenv SAI_CP2K_PREFIX "/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/DSPRHBM"
    setenv SAI_CP2K_NATIVE_PARTITION "DSPRHBM"
}
