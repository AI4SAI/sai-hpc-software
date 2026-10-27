# Installed release versus its original SIF

This dated experiment compares the native CP2K 2026.2, ABACUS 3.11.0-beta11,
LAMMPS 30Sep2026 and DeePMD 3.2.0 installations with the exact SIFs used for
their administrator exports. It does not install anything into `/opt`, extract
applications from SIF, or change public modules. Benchmark inputs, results and
native scratch use the project's storage and billing account. SIF runtime
scratch uses the production launcher's private node memory.

`prepare.py` freezes release identities, accepted scientific inputs, image and
native file checksums, controller code and submission records in a fresh run
directory. It submits a held job, checks its account and working directory,
then releases it. All paths must remain under
`/org/org-open-sai/proj-sai-hpc-software`. Run each software/partition in a
separate directory; preserve unsuccessful attempts.

```bash
python3 -B deployment/release-sif-efficiency/prepare.py \
  /org/org-open-sai/proj-sai-hpc-software/operations/MY-EXPERIMENT/cp2k-16V100 \
  cp2k 16V100

# After sacct reports COMPLETED 0:0:
python3 -B deployment/release-sif-efficiency/run.py verify \
  /org/org-open-sai/proj-sai-hpc-software/operations/MY-EXPERIMENT/cp2k-16V100
```

The supported software arguments are `cp2k`, `abacus`, `lammps`, `deepmd-kit`;
partitions are `16V100`, `4V100`, `8V100V0`. The deployment metadata and tiny
memory bootstrap are pinned to the installations available on October 27.
Updating this experiment to another release requires checking those inputs.
The generated batch scripts are internal experiment controls, not new public
sbatch templates.

Each arm uses one V100 and the same physical CPU affinity: four OpenMP threads
on 16V100/4V100, three on 8V100V0, with single-threaded BLAS. CP2K, ABACUS and
LAMMPS use the site's actual host MPI and mapping script; DeePMD invokes its
Python directly, with `taskset` providing equal placement. SIF execution uses
the production runtime, including `--sharens` for MPI applications and its
private-memory setup. A common observation shim records placement immediately
before the application executes. Probes prove that executable, linked libraries
and selected private files are byte-identical, while the application is on a
different device inside the SIF mount. External application dependencies are
bound selectively; the native installation never hides the SIF's application.

The measurements retain one excluded warmup and three alternating pairs:

| Application | Workload and numerical check | Internal time |
| --- | --- | --- |
| CP2K | Small PBE, 64-water OT, 64-water ELPA; accepted energy/force references and equal SCF iterations | CP2K's total timer, including program I/O |
| ABACUS | Accepted PW, HSE and DeePKS inputs; converged SCF, energy within 1e-5 eV, equal iteration counts | Sum of rounded SCF iteration times |
| LAMMPS | 2,058-atom replicated fixed geometry, PyTorch DeePMD and PLUMED; all forces, energy, virial and PLUMED result | LAMMPS Loop time after in-process warmup |
| DeePMD | Same 2,058-atom PyTorch inference on CUDA; energy, forces and virial | Repeated inference bracketed by CUDA synchronization |

LAMMPS/DeePMD calibrate the native workload to at least five seconds and then
freeze its count for both arms. Calibration is retained but excluded from the
reported ratios. This is a fixed-geometry inference experiment, not a molecular
dynamics trajectory or training throughput measurement. The short ABACUS
fixtures also do not establish large-system throughput.

Whole-process wall time is reported separately from internal timers. Version/
help/import commands measure warm startup. Full image hashing and provenance
probes occur before timed samples, so these are not cold-storage measurements.
The physical `/opt` installation and SIF reside on different backing storage;
the native and SIF routes also use their respective scratch mechanisms. These
are practical deployment comparisons, not an isolated packaging penalty.
Small or negative differences may reflect storage, GPU clocks or shared-node
noise. One rank/GPU does not establish multi-node or DDP scaling.

`report.json` contains all samples, route medians and within-pair differences.
`verify` reparses original numerical outputs, checks workload/placement and
file hashes, and requires successful terminal Slurm accounting with the project
account and frozen work directory. After online verification, a copied archive
can be rechecked with `verify DIRECTORY --offline` using saved accounting.
No thresholds or measurements are silently removed to make a route look faster.
