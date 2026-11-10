# CPU QE installed HDF5 qualification and storage audit

CPU QE source `61569eb480231b649c47f631df9c5c83537df461` was rebuilt in a fresh
container overlay using the already installed provider:

```text
/opt/apps/qe/dependencies/hdf5/1.14.6-nvhpc26.3-ompi5.0.10-f90/DSPRHBM
```

Its full inventory, NVHPC compiler executable hashes and OpenMPI wrapper hashes
matched the accepted provider record. Generated QE links selected exactly the
provider's static `libhdf5.a`, `libhdf5_fortran.a` and `libhdf5_f90cstub.a`.
The provider was mounted read-only for compilation and omitted from final SIF
verification/runtime binds. No HDF5 source compilation occurred and `/opt` was
not modified. The application SIF carries the standard HDF5 license, not another
copy of the dependency development tree.

| Stage | Job | Node | Elapsed | Result |
| --- | --- | --- | --- | --- |
| Build, pack, final SIF checks | 2339519 | d9470n05 | 13m37s | COMPLETED 0:0 |
| Host MPI / SIF science | 2339831 | d9470n03 | 24s | COMPLETED 0:0 |

All 67 regular executables pass loader/feature checks; `manypw.x` remains the
standard relative command alias. No loader output contains an unresolved
library or a dynamic HDF5 dependency. The final SIF checksum is
`5f773e0139d13a40689f4667305a64374ebd6c7144b329a4202465878bc03e86`.
The build recipe checksum is
`6cb216321385c44d3d0980fa40ae54a05c326627fa6f1799c76a8cf362c1936f`.

The one- and two-rank Si SCF energies both equal -15.84452726 Ry, with zero
forces to printed precision. The phonon checks pass at 2.590577 and
510.149663 cm^-1 (each triply degenerate), and the integrated density is
7.9999948251504 electrons. This is representative CPU acceptance on one node,
not a GPU, multi-node or performance comparison. The original application
source and recipe-independent numerical gates were retained.

The [compressed qualification receipt](qe-qualification.json.gz) records the
requests, accounting, artifact/science metadata and embedded provider/link/
loader receipts. Full raw build/science files remain under project storage:

```text
runs/qe-site-hdf5-20261110-r1-dsprhbm/
runtime-tests/qe-site-hdf5-20261110-r1-dsprhbm-science/
operations/qe-site-hdf5-20261110-r1/
```

The additional publication command in this independent qualification harness was
rejected with `trusted runtime launcher is missing`: its frozen controller was
placed under `operations`, while publication requires `PROJECT/controller`.
That failed receipt is retained. The build and scientific jobs passed, but this
SIF is unpublished and does not replace the public beta. Production `ci.py`
already deploys its frozen controller under the required directory; no
publication guard was weakened to accommodate the test harness.

## Historical storage review

The [read-only storage plan](storage-plan.json.gz) records allocated blocks and
host inode counts, eligible files, skipped cases, and proposed beta retention.
Its snapshot contains the extra in-progress QE qualification overlay; that
overlay and its duplicate Squashfs were removed by the existing successful
build cleanup. The earlier 447-overlay inventory was about 2.0 TiB allocated.

Twelve successful November 8 MD builds contribute 24 eligible files totaling
143.60 GiB. The cluster `cleanup.sh --check` verified unchanged requests/status,
retained SIF hashes, scratch file identities and COMPLETED/0:0 accounting.
No deletion was executed. Sixty standard beta bundles contain 54.19 GiB of SIF
copies; twenty older generations (11.25 GiB) are proposed only for further
reference review. Five legacy generations have external/shared layouts and
are explicitly excluded from automatic selection. Other historical SIF stores
and administrator handoffs are not included in that beta subtotal.

See [storage policy](../../STORAGE-RETENTION.md) for the guarded manual script,
future successful-build cleanup, and the distinction between rotating an alias
and actually reclaiming disk space.

Validation: GitHub PR #83 ran 1,341 unit/integration tests with two environment
skips, plus shell syntax checks. The installed-provider and cleanup negative
tests reject altered libraries/modules/tools, wrong link providers, changed
scratch/SIF files, redirects, duplicate paths and live jobs at deletion time.
