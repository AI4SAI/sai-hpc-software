# DeePMD host data access, 2026-11-10

The application launcher previously exposed only its current working directory.
A training configuration referring to a sibling dataset could therefore fail
even though the invoking user could read that dataset on the host. The common
runtime now binds `/home` and `/org` read/write at their original paths, with
the caller's existing UID, groups and filesystem permissions. It retains the
read-only SIF application, host MPI and private memory scratch.

This is a runtime update of the November 10 DeePMD master build, commit
`3b2555c46ecdbd2fcc8ddc029d09195a4e3c0f3a`. The commit also matched upstream
master when checked. No application or dependency was rebuilt. Original build
and scientific receipts were revalidated for all three partitions, including
TF/PT/JAX Python and C-API inference and two-GPU PyTorch training/restart.

The new checks place the model and fixture in an adjacent `our-datasets`
directory and start the application in `train-from-scratch-alone/mini`.
Training uses a relative dataset path and validation uses an absolute path
outside the working directory. Checks also read an existing `/home` file,
follow a symlink into the shared dataset, and write an output outside the
working directory. The original acceptance inputs and SIFs remain unchanged.

| Partition | Original build | Original beta acceptance | New runtime check |
| --- | --- | --- | --- |
| 16V100 | 2336455, passed | 2337413, passed | 2347071, passed in 58 s |
| 4V100 | 2336453, passed | 2337415, passed | 2346954, queued at preparation |
| 8V100V0 | 2336462, passed | 2337417, passed | 2347008, queued at preparation |

The 16V100 check passed GPU inference, `se_e2_a` and DPA2 two-GPU training
through step 8, and restart through step 12. The two CUDA ranks had finite
gradients and identical final parameters. Inference maximum absolute errors
were 0 for energy, 1.74e-17 for forces and 5.56e-17 for virial. This is a small
functional test, not a training throughput or multi-node scaling result.

Initial test preparations omitted training output directories; one retry also
had an incorrect relative fixture path after adding a directory level. These
test-only mistakes were corrected without modifying the application or runtime.
Their logs and initial job records remain in the operation directory.

Local validation: 28 SIF/module tests and 23 daily-beta tests passed.

## Durable completion

All checks, raw output, controller snapshot and publication receipts live at:

```text
/org/org-open-sai/proj-sai-hpc-software/operations/deepmd-host-data-20261110/
  jobs.json
  source.json
  bundle/
  finish.py
  finish-inputs.json
  finish.sbatch
  completion-job.json
  publication.json              # written after completion or supersession
```

Job `2347230` is a bounded one-CPU, 1 GiB, five-minute completion job with
`afterok` dependencies on all three checks. It revalidates job accounting,
input hashes, numerical evidence and data-path checks before publication.
Failure of a check prevents publication. A newer beta selection also prevents
this handoff from overwriting that selection.

After successful completion it selects generation
`deepmd-host-data-20261110-r1`. The small new runtime bundle reuses the existing
public SIF paths; it does not create additional application SIF copies. The
previous bundle must remain available because its images are still referenced.
Existing loaded generations retain their old launcher; new jobs load
`deepmd-kit/master-beta-later` through the unchanged `/opt` dispatcher.

Until `publication.json` reports `published`, the public alias has not switched.
The common code change applies to future beta publication of every application;
this immediate handoff is for DeePMD only.
