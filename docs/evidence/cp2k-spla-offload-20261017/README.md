# SpLA GPU offload and application follow-up

`results.json` records real job `1831559` on 4V100: SpLA 1.6.1, GCC 14.3.0,
CUDA 12.9.1, V100-SXM2-32GB. The probe uses host buffers, a transposed DGEMM,
alpha/beta accumulation, full CPU/GPU comparison and independent scalar checks.
One warmup precedes three alternating CPU/GPU repetitions at each size and
thread count. Interposition forwards unmodified calls to the real cuBLAS
function and counts successes; it does not substitute the math kernel.

All numerical checks passed. The 64 case takes the CPU threshold path; larger
cases actually called cuBLAS. Against four CPU threads, 1024/2048-square cases
were 1.32x/2.48x faster, including transfers; 256 was about 3.3x slower.
This is a dependency microbenchmark, **not CP2K application acceptance**.

`job-private-readonly-failed.sbatch` is the exact first probe `1831524`, which
failed on the private driver's inherited read-only FD. `job.sbatch` is the
second probe `1831559`, using stock only for read-only dependency access and
the private driver for 40 fresh-overlay CMake OpenMP cleanup reproductions.
Those 40 tests and read-only fsck passed; the original application failures
were not reproduced.

**Do not use either historical probe to assert image integrity.** The second
probe's post-run dependency image hash differs from its original seal. The
original seal remains unchanged and must be rejected. Both drivers' FUSE
read-only mode can still open the backing image writable. The numerical output
is retained, together with the separate integrity failure and original logs.
No historical seal is regenerated to conceal this change.

Raw paths are under `~/sai-hpc-software/runs/cp2k-spla-probe-20261017-{a,b}`.
`production-before.json` is the earlier 21:35 snapshot. `collect.py` collects
read-only logs, fixed source excerpts, hashes and the later integrity result;
it writes a new evidence file only under project home diagnostics.

Local validation on the workstation's Windows-mounted tree encountered mode
semantics and missing CMake. Focused checks are therefore run on the SAI home
filesystem, with full CI on Linux. This is separate from the numerical probe.

`validation.json` records successful GitHub CI at `f35bf85`: 710 passed,
two optional skips, plus 26 focused SAI checks. `container-focused.log`
adds 37 passing SAI tests for the repaired driver wrapper, workflow lifecycle
and continuation/recovery behavior. These tests use temporary directories
under project home.

`production-after.json` retains the intermediate production snapshot;
`production-container-fixes.json` records the later 22:16 state, when all
four dependency jobs had succeeded and all four application jobs had failed.
No accepted CP2K application artifact is implied by the dependency probe.

The real installed dependency config probe is now complete. Job `1832078`
(4V100, `4v100n03`, 10:13, exit `0:0`) resolves COSMA's COSTA/tiled-mm chain
and SPLA with the new receipt-derived prefixes and GCC 14.3.0. The corrected
private driver mounted the diagnostic image read-only; complete image hashes
before/after match. It also matches the pre-mount digest from failed probe C,
confirming no further image change across that earlier access. C's fixture
used lowercase `spla`; CP2K upstream uses uppercase `SPLA`, corrected in D.
This does not restore the invalid original release seal or validate CP2K math.
`config-probe-results.json` retains both attempts, exact inputs and logs;
`collect-config-probes.py` gathers them without mounting images.

`container-validation.json` records successful CI at `2c7fd3e`: 719 passed,
two optional skips. It distinguishes 37 focused checks on SAI home from the
16 preparation/seal checks on the local workstation. Real filesystem probes
`1831878` and `1832137` ran independently on SAI, under project home.

`fresh-release-start.json` is the verified 22:45:52 submission snapshot for
workflow `39041714911` and controller `2c7fd3e`: 16V100 `1832260`, 4V100
`1832262`, and DSPRHBM `1832265` are RUNNING; 8V100V0 `1832264` is PENDING.
The 16V100 log has reached dependency concretization; the other running jobs
are preparing their images. `collect-fresh-release.py` validates source,
recipe, controller and submitted-script identity while reading accounting
and logs. It does not modify jobs, mount images, or claim CP2K acceptance.
