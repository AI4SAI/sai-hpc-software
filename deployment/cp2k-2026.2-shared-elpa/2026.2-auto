#%Module1.0
proc ModulesHelp {} {
    puts stderr "CP2K 2026.2 with shared ELPA; load inside a Slurm allocation."
    puts stderr "Run cp2k.psmp; CP2K_DATA_DIR selects the original packaged data."
}
module-whatis "CP2K 2026.2; shared ELPA; native MPI/OpenMP; Slurm partition selection"

set build cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43
if {[module-info mode load]} {
    if {![info exists env(SLURM_JOB_PARTITION)]} {
        error "Load cp2k/2026.2-auto inside a Slurm job or allocation"
    }
    set partition $env(SLURM_JOB_PARTITION)
    set prefix [file join /opt/apps/cp2k $build $partition]
} elseif {[module-info mode remove]} {
    if {![info exists env(SAI_CP2K_NATIVE_PARTITION)] || ![info exists env(SAI_CP2K_PREFIX)]} {
        error "Missing the loaded CP2K identity"
    }
    set partition $env(SAI_CP2K_NATIVE_PARTITION)
    set prefix $env(SAI_CP2K_PREFIX)
    # Shells that loaded the previous release can still unload after this upgrade.
    set old cp2k-release-v2026.2-g67b5da876dd6-r4d454c740309
    if {$partition eq "DSPRHBM"} {set old cp2k-release-v2026.2-g67b5da876dd6-rfb4adb0598fc}
    if {$prefix ne [file join /opt/apps/cp2k $build $partition] &&
        $prefix ne [file join /opt/apps/cp2k $old $partition]} {
        error "Unexpected loaded CP2K prefix"
    }
} else {
    return
}
if {$partition ni {4V100 16V100 8V100V0 DSPRHBM}} {
    error "Unsupported CP2K partition: $partition"
}
set fragment [file join $prefix share sai native-module.tcl]
if {![file isfile $fragment] || [file type $fragment] eq "link"} {
    error "Missing native CP2K installation: $fragment"
}
source $fragment
