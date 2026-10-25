# Daily CP2K cuSOLVERMp tester

The existing daily CP2K development build on `16v100-avx2` now runs a small
cuSOLVERMp regression suite after candidate scientific acceptance and before
publication. It uses the same full candidate SIF delivered to users. A wrong
result, skipped case, missing required solver call or invalid execution evidence
blocks publication. Other targets and release tracks keep their existing gates.

The build recipe retains MPI/MPI_F08, CUDA, DBCSR, FFTW3, LibXC, Libint2,
ELPA, COSMA, SpLA, LibXS/LibXSMM, PLUMED, spglib, libvori/libbqb, parallel HDF5,
DFTD4 and tblite. GPU support includes cuSOLVERMp with NCCL and SpLA offload.
The tester checks the existing feature contract and the development flags
`libxc_gpu`, `libwignernj`, `s_dftd3` and `openblas` present in the accepted
development build. This is the current broad profile, not a claim that every
optional upstream library is enabled.

Each run stages `do_regtest.py`, its matcher modules, and the selected inputs
from the candidate's exact CP2K Git commit in the existing source cache.
Upstream Python executes inside the SIF. The host only reads source files and
independently checks output values, provenance and solver timers.

| Upstream directory | Selected inputs | Required coverage |
| --- | --- | --- |
| `QS/regtest-cusolver` | `H2O-6.inp`, `Si8-generalized.inp`, `Si8-generalized-complex.inp` | Small-matrix fallback; real and complex generalized cuSOLVERMp |
| `QS/regtest-elpa` | `cplx_kp.inp`, `tailblock_hang.inp`, `H13-tailblock.inp` | Ordinary complex fallback; OT FULL_ALL eigensolve; 65-dimensional real eigensolve |
| `QS/regtest-elpa-2` | `H2O-elpa-redistribute.inp` | Small-matrix fallback |
| `QS/regtest-elpa-qr` | `si128-elpa.inp`, `si128-elpa-qr.inp` | Real cuSOLVERMp with block sizes 32 and 64 |

The three native cuSOLVER inputs are unchanged. The six adapted ELPA inputs
only select `PREFERRED_DIAG_LIBRARY CUSOLVER` and remove ELPA-specific options.
Scientific settings, matchers, reference values and tolerances are preserved.
The QR-named input covers its generic block size here; it does not test ELPA QR
when run through cuSOLVERMp. `tailblock_hang.inp` is an upstream completion-only
test. Some upstream cases intentionally limit SCF iterations and allow failure
to converge; the tester preserves that design.

Six inputs must contain positive `cp_fm_diag_cusolver`,
`cp_fm_general_cusolver` or `cp_cfm_general_cusolver` calls as appropriate.
The other three currently exercise ScaLAPACK fallback and are not counted as
cuSOLVERMp eigensolve coverage. The solver-selection banner alone cannot pass
the backend check.

One Slurm job requests one `16V100` node, four GPUs and 16 tasks in `rush-gpu`,
with a 20-minute limit and **no memory request**. The official harness runs one
test directory at a time, using four MPI ranks and four OpenMP threads per rank,
with a 150-second per-input timeout. All ranks run inside one Apptainer container,
using isolated local MPI launch. Each rank selects one allocated GPU UUID; its
recorded affinity must span four distinct physical cores, with no overlap across
ranks. The complete test tree is bound into the container so upstream relative
coordinate paths continue to work.

`controller/ci.py` submits and monitors the regression as `<build-run>-cusolvermp`.
Continuation checks reuse its recorded Slurm handle. A timeout or ambiguous
submission must be inspected rather than silently resubmitted. The image
sidecar receives `cp2k_regtest` proof only after successful accounting and raw
evidence verification. Publication and accepted-cache lookup reverify that proof.

The existing GitHub Actions result artifact includes `cp2k-cusolvermp/` with:

- `report.txt`: CP2K dashboard-compatible `CommitSHA`, `Summary` and `Status`.
- `regtest.log` and `regtesting/TEST-*/`: full upstream report and executed inputs,
  outputs and rank records.
- `upstream/`, `tests/` and `adaptations.diff`: original and adapted source files.
- `request.json`, `job.sbatch`, `job.id`, allocation/MPI logs, `status.json` and
  `evidence.json`: source, recipe, SIF hashes, Slurm outcome and verified results.

This branch produces the report and CI artifacts. A stable public `report_url`
and registration in CP2K's `tools/dashboard/dashboard.conf` are separate from
the runner integration. Build failures before regression submission remain
visible through the existing build workflow logs.

The container runner was validated on 2026-10-25 in job `1937927` on `16v100n80`:
four V100s, four MPI ranks with four OpenMP threads each, 9/9 cases passed,
six required cuSOLVERMp paths verified, and maximum numerical relative error
`1.35e-15`. Slurm recorded `COMPLETED / 0:0` in 2m23s. The trial used the existing
immutable SIF built from `cfe5e5d87aaf302953f14eb43587bef2411a8fb2`; it validated
the new runner without rebuilding or attaching a new publication proof to that
older image. Daily CI uses a freshly built candidate under the new recipe.
