# CPU dependency export recovery

Job **2193082** completed `0:0` on **d9470n18** in **10m46s**. It reused the
libraries from original job **2170274**, which remains `FAILED 74:0` on
d9470n01. No dependencies were recompiled and no physical `/opt` files were
installed by this workflow.

The original writable phase had completed cleanly: payload, unmount and driver
return codes were zero, no forced stop occurred, and teardown took 21.977s.
Its subsequent `work.ext3:ro` export bypassed managed teardown because the
adapter previously selected only writable overlays. Apptainer reported a
fuse2fs exit timeout even though `cat` returned zero; the existing failure
guard correctly returned 74. That partial export was not used as the recovery
artifact.

Commit `6f405fa6616a0ddb711b987a5cc671fbc0bb48ac` covers both overlay modes.
Read-only mode is retained on the backing fuse2fs mount and the inner directory
overlay, and is checked in the lifecycle receipt. The runtime and pinned
fuse2fs binary are unchanged. A fresh recovery allocation:

1. Validated the exact original failure, controller and clean writable receipts.
2. Copied the completed overlay to its own run, sealed it to mode 0400 and
   checked SHA-256 against the independently recorded source digest.
3. Ran read-only `e2fsck -fn` successfully.
4. Confirmed a write attempt fails with `EROFS`, then streamed a new Squashfs
   through the managed read-only mount and waited for natural driver exit.
5. Created a new SIF and passed all library inventory, linkage and consumer tests.
6. Checked the overlay SHA-256 again and recorded original and new evidence.

The accepted read-only lifecycle receipt reports all return codes zero,
`forced_stop=false`, `readonly=true`, and teardown of **0.008475s**. This healthy
node did not require a long shutdown; the code and unit tests preserve the
120-second bounded wait and reject any forced termination.

The overlay digest before and after export is:

```text
64ec6a9eace8b0a1d8e46cd1db813a5f6a5ab9714ea18e7f99f00383ada44646
```

The accepted CPU SIF is **128,663,552 bytes**, SHA-256:

```text
cb4123b2e9ef42057dfd922f1ef6433e3dd1dc83d029f0896465d2a1cc50d0ea
```

Acceptance covered **51 dynamic ELF files**, a **two-rank MPI/Fortran HDF5
numerical roundtrip**, compilation against the installed Libint Fortran module,
PLUMED verification and exact package inventories. The GPU SIFs have one
additional Tiled-MM library. These are dependency checks, not CP2K scientific
acceptance or an application performance benchmark.

The combined handoff contains four SIFs, 39 package variants and 48 files. Its
read-only administrator preflight passed. The home handoff and all its
ancestors permit administrator access; ACL evidence is included. Existing
matching `/opt` prefixes are verified and skipped when the administrator installs.

The compressed files in this directory retain the original export failure,
fresh request/script/logs, lifecycle receipts, HDF5 output, ELF linkage records,
filesystem check and handoff preparation/preflight. Their uncompressed and
compressed SHA-256 values are recorded in [full-handoff.json](../full-handoff.json).
The implementation's full GitHub CI passed 1,204 tests with two skips.

Cluster run and preparation operation:

```text
/org/org-open-sai/proj-sai-hpc-software/runs/spack-devtools-20261104-e-DSPRHBM
/org/org-open-sai/proj-sai-hpc-software/operations/cp2k-spack-devtools-20261104-reexport-e
```
