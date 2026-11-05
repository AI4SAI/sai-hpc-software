# QE installed-feature audit

This audit compares the seven existing QE 7.3/7.5 installations with the four
installed QE 7.6 targets. It uses the old `make.inc` and build scripts, the new
image's CMake configuration, actual ELF symbols and executable inventories.
The [machine-readable record](feature-audit.json) retains the paths, hashes,
inputs, raw-log locations and accounting, including failed diagnostic batches.

The current QE 7.5 installation's 60 commands are all present among the new
67 ELF programs. No enabled numerical feature in that baseline was found to
have been disabled by the new build configuration. The older 7.3 installation
has additional legacy entry points; their limitations are recorded below.

| Capability | Existing site installation | QE 7.6 result |
| --- | --- | --- |
| MPI, OpenMP, FFTW, ScaLAPACK | Enabled | Retained; host-MPI native calculations passed |
| V100 acceleration | GPU builds include SM70 and several newer architectures | SM70 retained; actual CUDA kernels verified on all three V100 partitions |
| Parallel HDF5 | Enabled in 7.5 GPU builds, absent from old CPU build | Enabled on GPU and CPU; SCF/PH/PP read/write path passed |
| D3, TS, MBD | Implemented in old programs | Implemented and exercised by real calculations on all four targets |
| ESM and RISM | Present in old programs | Present in new symbols/tools; these solvation models were not independently exercised in this audit |
| OSCDFT | Old tools exist, but the old PW executable lacks its implementation | Enabled in the new build and present in the PW executable |
| Wannier interface | `pw2wannier90.x` present; no standalone `wannier90.x` in the audited QE prefixes | Interface retained |
| EPW, GIPAW, CPV, TDDFPT, HP, GWW executables | Absent from the audited installed command suites | Outside this `pwall` build profile; no previously installed command removed |
| Libxc, ELPA, Environ | Not enabled in the audited old installations | Not enabled; this is not an omitted previously enabled option |
| ScaLAPACK QRCP | Old programs lack `pzgeqpf`; serial QRCP is present | Same configuration and serial QRCP support |
| Large-array support | Enabled by old compiler flags | Retained by upstream NVFortran CMake flags |

The seven additional executables relative to the current 7.5 suite are
`dos_sp.x`, `ef.x`, `fd.x`, `fd_ef.x`, `fd_ifc.x`, `fermi_int_0.x` and
`fermi_int_1.x`. The CPU compiler and MPI stack changed from Intel/Intel MPI
to NVHPC/OpenMPI; this audit does not establish performance equivalence.

## Representative dispersion calculations

D3, TS and MBD use the pinned QE 7.6 `test-suite/pw_vdw` inputs and numerical
references. The absolute limits come from its `[PW]` test profile: `8e-4 Ry`
for energy and `1e-3 Ry/bohr` for each force component. D3 uses a pseudopotential
matching the upstream reference MD5. The official TS/MBD pseudopotentials now
use UPF2 and have different bytes from the historical references, so the audit
also compares old 7.5 and new 7.6 with identical current inputs on the same node.
It does not attribute numerical differences solely to pseudopotential file
serialization; the exact current bytes are pinned in the evidence.

All twelve new-version cases pass their numerical checks. The clean acceptance
jobs are `2212789` (16V100), `2212790` (4V100), `2212791` (8V100V0) and
`2213181` (DSPRHBM), all `COMPLETED 0:0`. GPU logs include actual CUDA kernels.
The same-node 7.5/7.6 comparisons preserve these differences:

| Case | GPU energy difference (Ry) | GPU maximum force-component difference (Ry/bohr) | CPU energy difference (Ry) | CPU maximum force-component difference (Ry/bohr) |
| --- | --- | --- | --- | --- |
| D3 | 0 | 0 | 0 | 0 |
| TS | 9e-8 | 2.4935e-4 | 9.141e-5 | 1.168e-4 |
| MBD | 1e-8 | 2e-8 | 1e-8 | 2e-8 |

These differences satisfy the pinned upstream limits; TS is not bitwise
identical. The tests are representative feature checks, not a performance
comparison or an exhaustive validation of every installed program.

The first diagnostic batches failed because the checker expected an obsolete
TS output label or the old CPU reference lacked its compiler runtime modules.
Their failures remain in the accounting. CPU job `2213060` completed its six
old/new dispersion calculations but failed on a later, separate `dist.x`
diagnostic; it is not substituted for the clean CPU acceptance job above.

## Legacy command differences

QE 7.3 publishes `dist.x` and `manypw.x` as links to `pw.x`, but their names
select different code paths. They are not interchangeable labels. The new
program still contains both implementations; the current site 7.5 installation
already omits both links.

`dist.x` prints the expected distance/angle information, then fails during MPI
cleanup. A same-node, same-input comparison in job `2213158` reproduces exit 5
and `MPI_ERR_COMM` in both old 7.3 and new 7.6. The dry-run path returns before
the pool/band communicators are initialized; cleanup then attempts to free the
default communicator value. This pre-existing legacy failure is not a missing
compile option. The public supported command set therefore excludes `dist.x`.

Repair is deferred and does not block the release or development beta. In the
pinned 7.6 source, the sole call to `run_dist()` is the
[explicit `dist.x` command dispatch](https://github.com/QEF/q-e/blob/9f93ddec427d2b9a45bb72d828c6d324f62fcabd/PW/src/run_pwscf.f90#L121).
Ordinary `pw.x`, `ph.x` and `neb.x` calculations do not depend on this utility;
no invocation was found in the audited upstream tests/examples or our workflow.
External automation explicitly invoking `dist.x` would need an alternative
geometry tool or a separate repair. Personal downstream scripts were not audited.

`manypw.x` is the multi-image entry point. Its packaging and runtime completion
are handled separately from the unchanged scientific executables, preserving
the invocation name and independent input/output directories for each image.
Project-local links to the actual installed programs passed on all four
partitions: jobs `2213567` (DSPRHBM), `2213568` (16V100), `2213569` (4V100)
and `2213570` (8V100V0) all completed with `0:0`. Each used two MPI ranks and
`-ni 2`; both silicon images converged to `-15.84452726 Ry` with separate
`tmp_0` and `tmp_1` output. GPU runs launched kernels on devices 0 and 1.
The global `JOB DONE.` marker belongs to image zero, while each image must
independently converge. These checks did not add links to physical `/opt`.

The 7.3 installation also includes `pwgui`, a Tcl/Tk input editor. It is absent
from both the existing 7.5 installation and this batch-oriented 7.6 package.
It is a GUI packaging difference, not a disabled numerical library.
