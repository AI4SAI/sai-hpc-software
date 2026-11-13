# Recover interrupted VM source preparation

The 2026-11-13 daily generation resolved LAMMPS `develop` to
`da42d3d222491045f1a9b787903b7159147b325d`. Its 4V100 source fetch failed with
GnuTLS receive error -110, and its 16V100 fetch timed out connecting to
`github.com:443`. Neither failure had submitted a Slurm build. The 8V100V0
build and its separate 14-case GPU scientific acceptance succeeded.

Daily run: <https://github.com/AI4SAI/sai-hpc-software/actions/runs/43285602562>.

## Network investigation

The WSL relay used the Windows DNS tunnel, without a GitHub hosts override,
Git proxy, TLS-verification override or proxy environment. During paired
probes, `20.247.166.64` timed out while `20.6.7.17` completed a verified HTTPS
request in 0.79 seconds. A process-local Git address override returned the
expected LAMMPS commit in 1.87 seconds. That override was diagnostic only.

Authenticated DNS-over-TLS also returned the affected address, which later
became reachable. These observations establish intermittent TCP/TLS
reachability, influenced by the selected GitHub frontend; they do not
establish DNS poisoning. Three subsequent ordinary Git probes succeeded in
1.17, 1.10 and 1.03 seconds. Git also succeeded inside the runner's Landlock
sandbox.

A temporary GitHub-only DNS-over-TLS route used the already-running
systemd-resolved service. New WSL sessions regenerated the resolver file
despite the edited WSL setting. The experiment was rolled back: original
WSL and resolver files restored, temporary service/drop-in removed, and link
DNS reverted. No permanent IP pin, DNS daemon, proxy or memory increase was
introduced. The runner retains its 2 GiB service limit.

## Recovery and permanent changes

Only the two unsubmitted LAMMPS tasks and their blocked beta task were reset.
The source, controller `6ff0fd7a28e6a0c0dcc2c0f42fd334f1585c7c1a`, original
run identity and successful tasks stayed frozen. Complete cluster source
caches for LAMMPS and its accepted DeePMD dependency were verified.

Attempt 2 exposed a second failure: the legacy LAMMPS upload tried to rewrite
the read-only controller files left before the original network failure.
Those two unsubmitted snapshots were archived with file hashes and an
operator recovery receipt. Attempt 3 recreates the snapshots from the same
frozen controller and reuses the verified source caches. No successful build
or installed software was changed.

Future generations use these changes:

- Reuse complete, non-shallow local Git objects for the frozen commit.
- Retry classified transient source-fetch errors three times, then defer
  the relay with exit 76. Invalid refs, authentication, certificate and
  integrity errors remain failures. Fetch logs have bounded memory use.
- Retry the initial controller checkout without changing its GitHub SHA.
- Stage LAMMPS controller files and verify every hash before committing;
  an interrupted preparation reuses matching read-only files and rejects
  differing content.

Regression coverage includes real Git reuse with an unavailable origin,
interrupted fetches, exact commit retention, recoverable versus permanent
errors, and repeated LAMMPS upload without replacing existing file inodes.
The complete repository suite passed: 1374 tests in 63.2 seconds, with two
optional local-source-cache tests skipped in GitHub CI. All seven workflow
validation jobs passed for commit `968494b218a8614aaf6426b2a61c0ddb3f80ac42`.

## Live recovery results

Both recovered controller snapshots matched all 58 archived file hashes.
The 4V100 build (2418042) completed in 7m17s and the 16V100 build (2418138)
in 8m39s, both with exit 0:0. Their SIFs passed the existing executable,
package/style and runtime-path checks. The relay service peaked at 379 MiB
during preparation and continued to use its original 2 GiB limit.

The 16V100 scientific job 2418489 completed on two nodes in 1m26s: all
14 cases passed, with 71 packages and 2657 styles. The original 8V100V0
scientific job 2414615 also passed all 14 cases. The 4V100 scientific job
2418479 was queued at the time of this record; its requested budget was
reduced from 40 to 15 minutes for backfill eligibility, with all inputs,
nodes, GPUs and gates unchanged. Beta publication remains gated on that
result and the subsequent Lmod/runtime acceptance. The linked daily run
and retained task receipts provide the continuing status.
