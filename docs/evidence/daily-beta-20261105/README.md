# November 5 development beta completion

All six development applications resolved by daily workflow `42003384294` have
accepted SIFs and published November 5 beta aliases. The read-only observation at
**2026-11-05 13:13 UTC** checked 21 partition deliveries, their unchanged original
acceptance-manifest hashes, public catalog hashes, module aliases, runtime-check
receipts and Slurm accounting. Every beta check is `COMPLETED 0:0` under account
`proj-sai-hpc-software` and has a passing numerical/runtime verification record.

These are the source revisions resolved for that workflow, not a claim that
upstream HEAD cannot have advanced since resolution. The snapshot verifies
publication and existing acceptance; it does not rerun science or benchmark
all supported application features.

| Application | Branch | Accepted source | Partitions | Rolling module |
| --- | --- | --- | --- | --- |
| ABACUS | develop | `3c1dddac0667` | 4 | `abacus/develop-beta-later` |
| CP2K | master | `37370f362787` | 4 | `cp2k/master-beta-later` |
| QE | develop | `61569eb48023` | 4 | `qe/develop-beta-later` |
| LAMMPS | develop | `80e27a711793` | 3 | `lammps/develop-beta-later` |
| DeePMD-kit | master | `bf5c88560e79` | 3 | `deepmd-kit/master-beta-later` |
| GPUMD | master | `ddc6e2d2c2f1` | 3 | `gpumd/master-beta-later` |

All applications cover 16V100, 4V100 and 8V100V0. ABACUS, CP2K and QE also cover
DSPRHBM. [`publication.json`](publication.json) contains the exact fixed module
names, full source SHAs, SIF paths/digests, evidence paths/digests, 21 beta job
IDs, nodes, elapsed times and publication records. [`collect.py`](collect.py)
is the read-only collector, intended for the SAI login node. It expects these
particular November 5 aliases to remain selected; a later rotation should make
it fail rather than silently report a different day's results.

## Workflow outcomes and repairs

The [original daily workflow](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42003384294)
finished with **failure** because it retains the earlier ABACUS and QE errors.
This is not an all-green original run. Its CP2K beta publication completed
successfully, as did LAMMPS, DeePMD-kit and GPUMD. The independent
[ABACUS recovery](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42028012790)
and [QE recovery](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42026314156)
both finished successfully. [`workflows.json`](workflows.json) preserves the
observed workflow/job conclusions and controller revisions.

The MPI stream-capture repairs are merged in PRs
[#66](https://github.com/AI4SAI/sai-hpc-software/pull/66) and
[#67](https://github.com/AI4SAI/sai-hpc-software/pull/67). They preserve independent
rank output before verification; no accepted SIF was recompiled and no numerical,
thread, GPU or performance threshold was relaxed. See the
[ABACUS evidence](../abacus-rank-output-20261105/README.md) and
[QE evidence](../qe-20261105/RANK-OUTPUT.md).

CP2K's final 8V100V0 science job `2218672` completed all 14 cases and passed
verification. Native export/publication finished at 12:52 UTC; the delivery
step then returned evidence to GitHub before beta publication started at
13:05 UTC. The four beta checks `2219013`, `2219015`, `2219017` and `2219019`
passed and selected
`master-beta-20261105-37370f362787-r4d85319d4295-b3ac36adef9e9`.
The source's `master-2026-11-04` label is distinct from the November 5 build and
accepted beta snapshot date.

## QE administrator handoff

The home-managed QE alias and four accepted SIF routes are ready. At observation
time the other five public gateways existed, but this file was still absent:

```text
/opt/modules/modulefiles/apps/qe/develop-beta-later
```

The prepared administrator package passed its live `--check` verification of
the selected module, runtime, images and launchers. The administrator can add
the stable forwarding module with:

```bash
bash /home/stardust/sai-hpc-software/deployment/sif-beta/install.sh --install /opt/modules/modulefiles/apps qe
module --ignore_cache avail qe
```

The gateway sources
`/home/stardust/sai-hpc-software/modulefiles/apps/qe/develop-beta-later` and does
not need daily replacement. This operation publishes the beta entry point;
the already accepted native QE 7.6 release installation is a separate delivery.

`dist.x` remains outside the supported command set. Its old/new MPI cleanup
failure is deferred: normal QE calculations do not invoke it. Only external
automation explicitly depending on that geometry utility needs an alternative
or a separate repair. See the [feature audit](../qe-20261105/feature-audit.md).

## CPU diagnosis follow-up

The [ABACUS CPU diagnosis](../abacus-cpu-io-diagnosis-20261105/README.md), merged
in [PR #68](https://github.com/AI4SAI/sai-hpc-software/pull/68), found multi-second
file operations overlapping MPI waits while affinity and numerical results
were correct. Concurrent DeePKS diagnostic-file writes add unnecessary shared
access. The storage/client/network source of the latency remains unproven;
no performance fix or release binary change is claimed.
