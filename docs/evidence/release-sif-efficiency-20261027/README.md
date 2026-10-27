# Installed release and matching SIF measurements — October 27, 2026

All **12 application/partition jobs completed and passed raw-output revalidation**: four installed releases on 4V100, 16V100 and 8V100V0. The experiment contains 144 measured scientific calculations, 48 excluded scientific warmups, 15 excluded native calibration calculations and 96 startup calls (24 excluded warmups). There is one GPU per job. Numerical checks, executable/private-library identity, CPU placement and project-account terminal accounting passed.

On 16V100, CP2K 64-water OT took 145.285 s natively versus 145.346 s internally through SIF (**+0.04%**); its whole-process median increased by 0.666 s (**+0.45%**). LAMMPS engine time changed by −0.48% and DeePMD inference by −1.11%. These measurements support small steady-state differences for the tested workloads; they do not establish a universal container penalty.

The [experiment scripts and protocol](../../../deployment/release-sif-efficiency/README.md) describe the exact frozen installations, production SIF launcher, native host MPI, equal inputs/affinity and alternating schedule. The physical `/opt` files and SIFs reside on different storage; SIF runtime scratch uses private memory while native scratch uses project storage. The figures compare practical deployment routes. Full SIF hashing/probes precede timing, so no cold-cache claim is made. Whole-process times have approximately 0.05 s polling resolution.

## CP2K beta recovery and the displayed date

The public alias really selected October 25 before recovery; this was not an Lmod cache problem. The October 26 matrix failed science on a GPU with `cudaErrorECCUncorrectable`. Identical-image retry 1958543 passed on healthy nodes, but that external recovery did not resume the failed Actions publication. [Workflow 40434962493](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40434962493) revalidated the original matrix, passed all four public module checks and selected:

```text
cp2k/master-beta-20261027-d43cbde630af-r75ef5179f1e8-b1b8da72091d1
```

[Normal module help](beta/module-help.txt) now shows source `d43cbde630af7800d85df60d3cfa520f3c4d3268`, source version `master-2026-10-26`, and accepted snapshot `20261027`. Source-version dates are the upstream commit date in UTC, not the build or local date. The [selected commit](beta/selected-source.json) was committed October 26 at 06:24:55 UTC.

At the 02:41 UTC inspection, [upstream HEAD](beta/upstream-at-check.json) had advanced to `e90df42b4bd8` at October 26 19:39:57 UTC (October 27 03:39:57 in Beijing). Its source label would also contain October 26. That newer commit is not the recovered accepted artifact. The daily workflow is active with `23 2 * * *`, but no October 27 scheduled run existed at inspection; the [previous three scheduled runs](beta/recent-daily-runs.json) were created after 07:00 UTC. [Publication receipt](beta/publication.json) and [four-partition launch checks](beta/validation.json) retain the exact transition.

## Warm startup

Values below are seconds and exclude the first invocation of each route. The delta is the median of the three paired SIF-minus-native differences, which can differ from subtracting the two route medians. CP2K/ABACUS/LAMMPS include host MPI launch/teardown; DeePMD invokes Python directly.

| Installed release | Partition | Native median | SIF median | Paired delta median |
| --- | --- | ---: | ---: | ---: |
| CP2K 2026.2 | 4V100 | 0.815 | 1.166 | +0.351 |
| CP2K 2026.2 | 16V100 | 1.768 | 2.319 | +0.551 |
| CP2K 2026.2 | 8V100V0 | 1.868 | 2.721 | +0.902 |
| ABACUS 3.11.0-beta11 | 4V100 | 0.415 | 0.815 | +0.401 |
| ABACUS 3.11.0-beta11 | 16V100 | 0.866 | 1.317 | +0.500 |
| ABACUS 3.11.0-beta11 | 8V100V0 | 0.717 | 1.318 | +0.603 |
| LAMMPS 30Sep2026 | 4V100 | 0.615 | 1.116 | +0.501 |
| LAMMPS 30Sep2026 | 16V100 | 1.418 | 2.018 | +0.601 |
| LAMMPS 30Sep2026 | 8V100V0 | 1.519 | 2.471 | +0.952 |
| DeePMD 3.2.0 | 4V100 | 0.032 | 0.264 | +0.232 |
| DeePMD 3.2.0 | 16V100 | 0.065 | 0.265 | +0.200 |
| DeePMD 3.2.0 | 8V100V0 | 0.065 | 0.366 | +0.301 |

## Scientific workload timings

All values are medians in seconds; `Δ` is `100 × (SIF median / native median − 1)`. Positive values mean longer elapsed time through SIF. CP2K’s internal total includes its own I/O. ABACUS uses rounded SCF iteration times. LAMMPS uses its post-warmup Loop time; DeePMD inference uses CUDA-synchronized timing. The LAMMPS/DeePMD case is a 2,058-atom replicated fixed geometry, with all forces, energy and virial checked. It is not an MD trajectory or DDP training throughput test.

| Application / case | Partition | Internal native | Internal SIF | Internal Δ | Wall native | Wall SIF | Wall Δ |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| cp2k / water-gpw | 4V100 | 9.509 | 9.812 | +3.19% | 10.332 | 11.033 | +6.79% |
| cp2k / water-gpw | 16V100 | 14.307 | 14.565 | +1.80% | 16.256 | 16.958 | +4.32% |
| cp2k / water-gpw | 8V100V0 | 21.197 | 21.321 | +0.58% | 23.516 | 24.470 | +4.06% |
| cp2k / water64-gpw | 4V100 | 137.313 | 132.192 | -3.73% | 138.197 | 133.447 | -3.44% |
| cp2k / water64-gpw | 16V100 | 145.285 | 145.346 | +0.04% | 147.325 | 147.990 | +0.45% |
| cp2k / water64-gpw | 8V100V0 | 174.481 | 173.421 | -0.61% | 176.882 | 176.678 | -0.12% |
| cp2k / water64-elpa | 4V100 | 21.759 | 21.902 | +0.66% | 22.697 | 23.148 | +1.99% |
| cp2k / water64-elpa | 16V100 | 30.021 | 29.974 | -0.16% | 32.027 | 32.389 | +1.13% |
| cp2k / water64-elpa | 8V100V0 | 45.441 | 44.504 | -2.06% | 47.571 | 47.118 | -0.95% |
| abacus / pw | 4V100 | 1.810 | 1.790 | -1.10% | 2.867 | 3.268 | +13.97% |
| abacus / pw | 16V100 | 1.950 | 1.960 | +0.51% | 4.272 | 4.773 | +11.73% |
| abacus / pw | 8V100V0 | 2.010 | 2.000 | -0.50% | 4.376 | 5.027 | +14.88% |
| abacus / hse | 4V100 | 0.320 | 0.300 | -6.25% | 1.516 | 1.966 | +29.72% |
| abacus / hse | 16V100 | 0.710 | 0.720 | +1.41% | 3.321 | 3.871 | +16.58% |
| abacus / hse | 8V100V0 | 1.410 | 1.390 | -1.42% | 4.776 | 5.227 | +9.45% |
| abacus / deepks | 4V100 | 0.760 | 0.810 | +6.58% | 1.616 | 2.167 | +34.06% |
| abacus / deepks | 16V100 | 1.300 | 1.390 | +6.92% | 3.421 | 4.022 | +17.56% |
| abacus / deepks | 8V100V0 | 2.230 | 2.320 | +4.04% | 4.478 | 5.328 | +18.97% |
| lammps / deepmd-pt-2058 | 4V100 | 5.765 | 5.756 | -0.15% | 7.473 | 7.974 | +6.70% |
| lammps / deepmd-pt-2058 | 16V100 | 5.636 | 5.609 | -0.48% | 8.882 | 9.481 | +6.75% |
| lammps / deepmd-pt-2058 | 8V100V0 | 5.572 | 5.529 | -0.77% | 10.443 | 11.245 | +7.68% |
| deepmd-kit / deepmd-pt-2058 | 4V100 | 5.869 | 5.883 | +0.22% | 9.225 | 9.226 | +0.01% |
| deepmd-kit / deepmd-pt-2058 | 16V100 | 5.852 | 5.787 | -1.11% | 10.937 | 10.887 | -0.46% |
| deepmd-kit / deepmd-pt-2058 | 8V100V0 | 6.555 | 5.773 | -11.94% | 14.856 | 13.803 | -7.09% |

ABACUS’s small HSE calculations take only 0.3–1.4 s internally; hundredths of a second can become a visible percentage. DeePKS adds 0.05–0.09 s in the median internal timer. Those short fixtures do not establish large-system throughput.

CP2K 4V100 OT includes substantial storage variability. In pair 0, `ot_scf_mini` takes 56.541/56.540 s (native/SIF), while `write_mo_set_to_restart` takes 18.107/3.245 s. The native total varies from 122.390 to 153.158 s across the three measured samples. We retain these observed totals and routine timers; no I/O time is subtracted to manufacture an adjusted comparison.

DeePMD 8V100V0 has an apparent −11.94% internal median difference. Its native samples are 6.555, 6.639 and 5.606 s, versus 5.773, 5.804 and 5.599 s through SIF. The final pair differs by only 0.0066 s. This time variation prevents attributing the median difference to SIF packaging. All three pairs remain in the report.

## Accepted runs and preserved attempts

| Application | Partition | Job | Node | Elapsed |
| --- | --- | --- | --- | --- |
| cp2k | 4V100 | 1967331 | 4v100n08 | 00:22:56 |
| cp2k | 16V100 | 1967324 | 16v100n41 | 00:26:52 |
| cp2k | 8V100V0 | 1967333 | 8v100v0n03 | 00:35:39 |
| abacus | 4V100 | 1967344 | 4v100n23 | 00:01:11 |
| abacus | 16V100 | 1967332 | 16v100n71 | 00:01:59 |
| abacus | 8V100V0 | 1967345 | 8v100v0n08 | 00:03:24 |
| lammps | 4V100 | 1967349 | 4v100n23 | 00:01:47 |
| lammps | 16V100 | 1967330 | 16v100n70 | 00:02:02 |
| lammps | 8V100V0 | 1967347 | 8v100v0n03 | 00:03:45 |
| deepmd-kit | 4V100 | 1967346 | 4v100n24 | 00:01:47 |
| deepmd-kit | 16V100 | 1967329 | 16v100n55 | 00:02:06 |
| deepmd-kit | 8V100V0 | 1967348 | 8v100v0n03 | 00:03:57 |

The [attempt inventory](attempts.json) includes eight unsuccessful harness preparations: four stopped in native MPI initialization because `--export=NIL` omitted the account home, and four were cancelled by Slurm as UID 0 before creating application logs after a selective export attempt. The successful harness restores the actual account home/user with clean module setup and retains project-only native scratch. No successful timed samples were discarded or replaced.

GPUMD is excluded from a same-build comparison. The installed module is `gpumd/5.8-cuda12.9`; [binary checksums](gpumd-identity.json) differ from both retained workflow SIFs, 5.8.1 and 5.9.1. Native version differences cannot be reported as container overhead.

## Evidence and reproduction

[measurements.json](measurements.json) retains every measured value, warmup, calibration, workload/placement record, numerical result/error, release identity and native/SIF file checksum. Its route summaries were recomputed locally; [summary-validation.json](summary-validation.json) describes that limited local check. Complete raw numerical revalidation ran on the cluster against every original output and successful terminal `sacct` record before archiving.

The complete archive contains frozen controllers, inputs, model files, all raw outputs including restart files, probes, reports, accounting and failed attempts. It remains in project storage instead of adding hundreds of megabytes to Git. Its [receipt](archive.json) records:

- Path: `/org/org-open-sai/proj-sai-hpc-software/operations/release-sif-efficiency-20261027/analysis-r1/raw.tar.gz`
- Size: 382,967,293 bytes
- SHA256: `d430071fb4831563cb8c1b7292fc35e7762cac684c7729c33fa61402edc163a6`

Copy and unpack that archive into a fresh evidence directory to independently reparse a run without cluster access:

```bash
python3 -B deployment/release-sif-efficiency/run.py verify /path/to/unpacked/cp2k-16V100-r3 --offline
```

The online verifier additionally queries terminal Slurm accounting. No `/opt` installation or static gateway was modified. The CP2K beta update only rotated the existing home-maintained accepted generation. Full repository CI ran 1,118 tests: 1,116 passed and two optional tests were skipped; the new tests reject missing/duplicate pairs, different workloads and nonfinite timings.
