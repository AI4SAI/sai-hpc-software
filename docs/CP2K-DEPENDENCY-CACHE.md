# CP2K auxiliary dependency build cache

CP2K uses administrator-installed Spack libraries and a separate project cache
for DBCSR, TBLITE with its bundled subprojects, LibXS, private LibXC and wignernj.
The latter cache preserves installed headers, Fortran modules, static/shared
libraries and package metadata before the application runtime SIF is stripped.
Source and build trees are not cached. Physical `/opt` is not modified.

Each accepted entry lives under:

```text
/org/org-open-sai/proj-sai-hpc-software/cache/cp2k-auxiliary/<contract-sha256>/
  payload.tar.gz
  manifest.json
  accepted.json
```

The contract includes the selected upstream dependency declarations and patches,
native compiler flags, partition, compiler/MPI/BLAS/CUDA identities, the verified
shared-Spack provider, ELPA selection and dependency build scripts. It excludes
the CP2K source commit, build date and application installation prefix. Ordinary
CP2K source changes therefore reuse an unchanged dependency set. A dependency or
ABI change creates a new key; this first implementation caches the whole group.

A miss compiles the libraries inside the normal project-backed container and
writes a compressed candidate under that run's `auxiliary-cache` directory.
Only successful CP2K publication, following the existing scientific acceptance,
can promote that candidate. Concurrent publishers serialize by contract key and
preserve the first accepted entry. Corrupted or unaccepted entries fail explicitly.

A hit verifies the complete archive and each member before restoring it into a
fresh build overlay. Installed CMake/pkg-config metadata and environment paths
are relocated to the new CP2K prefix. Static archives, ELF libraries and Fortran
modules retain their bytes. Opaque binary references to the old installation
prefix are rejected rather than patched. Runtime SIF packaging and `/opt/apps`
export retain their existing layout and omit build-only headers/static archives.
The cache itself is not needed to execute the final SIF.

`results/auxiliary-cache.json` and the embedded copy under `share/sai` record the
contract, cache hit/miss, source build, archive hash, lookup/restore time, archive
creation time, individual library build times and CP2K application build time.
The compressed cache stays on project storage and is not uploaded as a CI log.
Release LibXC 7.0.0 remains a site provider; only private LibXC is cached.

Validation currently includes real native static/shared-library consumers after
removing the original installation prefix, metadata relocation, corruption and
ABI mismatch rejection, dependency-change invalidation and publication gating.
Actual CP2K cold/warm build timing and scientific qualification are required
before enabling this change on the daily workflow.
