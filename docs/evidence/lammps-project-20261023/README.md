# LAMMPS development: all three V100 targets accepted

LAMMPS `develop` at `a0e2bd6efb3d67a29c0c4e4de3b19556ffdb9d5b` now has a
verified candidate SIF and 14 passing single/two-node GPU scientific cases on
4V100, 16V100 and 8V100V0. Each target retains the exact accepted installed
DeePMD-kit 3.2.0 dependency; this does not require a new DeePMD build. These
development candidates have not been published or installed natively in `/opt`.

| Partition | Build/export job | Scientific job | Result |
| --- | --- | --- | --- |
| 4V100 | 1896871, previously accepted | 1897165 | Existing SIF and all raw evidence reread successfully |
| 16V100 | 1896356, previously accepted | 1896820 | Existing SIF and all raw evidence reread successfully |
| 8V100V0 | 1912932, export recovery, 3m21s | 1912959, 2m58s | New recovered SIF and 14 scientific cases accepted |

All three retain 71 packages and 2,497 styles. The scientific checks cover the
TF/PT/JAX interfaces with PLUMED, GPU and Kokkos LJ, GPU and Kokkos Coulomb/PPPM
with cuFFT, and agreement between one and two compute nodes. Rank traces verify
the candidate executable, CUDA visibility, CPU masks, site MPI and installed
DeePMD libraries. Numerical tolerances are unchanged from the accepted release.
This evidence makes no timing or DeePMD training claim.

## Recovery provenance

Original job `1896876` completed compilation and feature parity but failed with
`fuse2fs failed to mount ... in 10s` when entering the export phase. Its request,
controller, logs, status and overlay remain unchanged, and its Slurm state is
still `FAILED`, exit `255:0`.

`recover.py` copied that overlay into a new project run with independent storage,
verified the complete 32 GiB logical contents against the original, and used a
byte-identical copy of the frozen controller to export and verify the SIF. The
overlay SHA256 before copying and after the recovery is:

```
895e9b9dbba611b47460a1f108ccb0a206211655c980fba2dacf4b5d659593b9
```

The compiled recipe remains
`c01d748b9e7b4a18aa72e606a88ed03fc2ff308e47735ef3da90c464cea283b2`, from
controller commit `f028ef9d9fea4119091b5f4803517a51bf6960c5`. The recovery tools
are commit `2108c116462e471cafd013421798275c5ac5b592`; project dispatch changes
are commit `835077cb9aee2c8ad2236bde64215d57aeb752d7`. Recovery did not rebuild
or relabel the original compilation as successful.

The recovered SIF is 264,589,312 bytes, mode `0444`, at:

```
/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-lammps/runs/lmp-recover-8v100v0-20261023-r1/candidate.sif
```

Its SHA256 is
`1161186a8e20b953e51f251317949358a6b79022676743f2c9766463d38f7182`.
The full scientific report SHA256 is
`c6f0a6833063f2dcce4545a6213a5e2afcad43b9496589901a505d77d239ad1c`.

Both new jobs used account `proj-sai-hpc-software` and an explicit project
working directory. All new run/controller/input/scratch paths are in the
project tree. Runtime/cache directories have their inherited ACLs removed and
mode `0700`; no physical cluster `/tmp` was used. The pinned FUSE helper is an
independent project copy with SHA256
`46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10`.

## Evidence and replay

- `delivery.json`: the three actual source/recipe identities, candidate paths,
  hashes, jobs, external DeePMD dependencies and acceptance flags.
- `recovery-request.json`, `8V100V0-build-completed.json`,
  `source-rechecked.json`: original failure, frozen controller hashes, overlay
  copy hash, export verification and a second full read of the original overlay.
- `allocations.json`: Slurm account/work-directory records, SIF size/mode and
  hashes of the raw job scripts, logs and allocation records.
- `scientific-request.json`, `8V100V0-scientific-acceptance.json` and
  `8V100V0-scientific-acceptance-summary.json`: pinned inputs, complete case
  results, rank traces, raw-file hashes and independent replay result.
- `4V100-reused-acceptance-summary.json` and
  `16V100-reused-acceptance-summary.json`: fresh read-only replays of the prior
  accepted artifacts. Neither target was rebuilt or submitted again.

`acceptance_base.py` is a byte-for-byte copy of the previously accepted
`../lammps-independent-20261022/acceptance.py`. The adapter only handles the
separate recovery status and the project paths/account; the scientific checks
and tolerances remain in that original verifier. The five actual runtime
control files were compared with the committed local files after execution.

To reread the final 8V100V0 evidence on the cluster:

```bash
science=/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-lammps/runtime-tests/lmp-recover-8v100v0-20261023-r1-science-20261023T143327Z
python3 -B "$science/control/acceptance.py" verify "$science"
```
