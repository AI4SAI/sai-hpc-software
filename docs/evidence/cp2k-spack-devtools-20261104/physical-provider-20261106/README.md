# Physical CP2K dependency reuse, November 6

The administrator installed all 39 prepared library variants into their final
`/opt/devtools` prefixes. `install.sh --verify` returned zero and verified every
installed inventory against the accepted dependency SIFs on November 6.
The [installation receipt](installation.json) pins the registry and raw
[verification output](install-verify.log). All four partitions are present,
including the previously missing 16V100 HDF5 prefix.

The PR registry now selects those physical providers. The controller checks
versions, ABI, installed file inventories and native compiler flags, and skips
the Spack build allocation when the provider is valid. Application SIFs retain
the provider receipt and use the read-only physical libraries. DBCSR, TBLITE,
LibXC and other inputs outside the provider contract retain their existing
application build/selection paths.

CP2K master `bc428196533ec1afec9844324d6ed8d98daaa661` now requires TBLITE 0.8.0
and DFT-D4 4.3.1. The complete upstream TBLITE bundle supplies both and their
matching subprojects. The runner verifies and stages the archive before any
compute job; source declarations and archive hashes must match. Release
TBLITE 0.6.0 retains its reviewed gradient patches. None of these changes
requires rebuilding the installed Spack libraries.

Status: physical installation verification passed; standard release and
current-development builds, final SIF checks and scientific acceptance against
physical providers are pending. The earlier copied-SIF application acceptance
is separate and is not used as proof of this new deployment path.
