# Module feature description validation, 2026-11-10

The owner audit found **34 readable files owned by saiuser01**, comprising 31
public modulefiles and three hidden backups. The configured MODULEPATH covers
apps, devtools, compiler, mathlib and mpi under `/opt/modules/modulefiles`.
No `/opt/apps/modulefiles` or `/opt/modulefiles` tree exists.

`owner-audit.json` records ownership selection, hashes, direct metadata presence
and classifications. The six beta gateways inherit documentation; their lack of
a direct whatis line was not treated as a missing program identity. BerkeleyGW
and Multiwfn had no whatis. Several other descriptions omitted optional features.

The `entropy`-owned `/opt/modules/modulefiles/apps/pwmat` directory is unreadable
to `stardust`; possible saiuser01-owned files inside remain unverified. The audit
script reports incomplete traversal with exit code 2. This is the only inventory
access gap; it must not be reported as a fully exhaustive audit of hidden contents.

## Candidate validation

`lmod-preview.json` contains 62 real Lmod commands: whatis and help for all 31
public entries, on the cluster login node with no allocation. Every command
exited zero and displayed Features. Fixed modules show version-specific summaries
and local records or inspection commands. Beta gateways add missing metadata to
old immutable generations; new generated modules suppress this compatibility
fallback. No sealed generation or public beta link was edited.

The final package was previewed from
`/org/org-open-sai/proj-sai-hpc-software/deployment/module-features`.
`new-generation-preview.json` separately verifies that a generation providing
its own feature documentation suppresses the legacy gateway fallback, without
duplicated or stale feature lines (two real Lmod fixture checks).

`owner-preflight.log` records successful read-only validation against all 31
actual installed targets, their owner, exact old hashes and advertised local
files. `runtime-unchanged.json` records preservation of the original runtime
bodies, excluding help/whatis declarations. Application paths, dependencies,
partition selection, environment variables and unload logic were not altered.

`DESCRIPTIONS.md` provides the review table for the 25 fixed modules. The six
beta modules additionally retain current source identities and scientific
acceptance scope from their generation. Feature evidence came from the configured
workflow recipes/accepted inventories, installed ABACUS abacus-info.txt,
BerkeleyGW arch.mk and executable list, ELPA configured-options headers,
SCTK/SSCHA install records, Python requirements.lock files and module dependencies.
The old July ABACUS does not acquire the new builds' LibRI/LibTorch/NEP claims.

Local validation passed 133 existing tests from test_sif_modules, test_sif_beta,
test_native_module, test_module_publication, test_lammps_deployment,
test_qe_deployment, test_abacus_beta12_deployment, test_cp2k_site_deploy and
test_daily_beta. A first attempt with fixtures on the Windows-backed workspace
failed POSIX permission assertions; running the fixtures on the local Linux
filesystem passed all 133 tests. No cluster application build used host /tmp.

The updater was also exercised against 31 synthetic installation targets:
original backups, exact copies, idempotence, modified-target rejection and
symlink rejection passed. This was not an installation to /opt. No application
rebuild or numerical benchmark was needed for these documentation changes.

## Initial narrower preview

`preflight.log` and `whatis-preview.log` retain the earlier four-release/six-beta
preview. The owner audit and 31-module candidate validation above supersede its
scope. These receipts are candidate checks, not evidence of an administrator
installing the new descriptions.
