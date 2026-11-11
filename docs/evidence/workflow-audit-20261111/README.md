# Workflow audit, November 11

[Run 42880817991](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42880817991)
was dispatched at 2026-11-10 19:00:03 UTC (November 11 03:00:03 Asia/Shanghai)
and ended at 21:58:18 UTC. It executed on `vm-sai-hpc` with controller
`e46af835ddef699b0238d82ac8860fbe56ebdd31`. The relay reported 26 succeeded,
27 skipped unchanged release/prerelease deliveries, and one failed beta task.
This is a real build/publication audit, independent of push/PR unit checks.

[cluster-status.json](cluster-status.json) records the six publication statuses,
the original task paths, DeePMD training results and log hashes, and the observed
manual cleanup state. Five applications selected the new run's generations:
ABACUS, CP2K and QE on all four partitions; LAMMPS and GPUMD on the three V100
partitions. Their generation manifests include readable static build records.

DeePMD development source `3b2555c46ecd` built and passed standalone scientific
checks in jobs 2358149 (4V100, 17m27s), 2358153 (16V100, 27m16s), and
2358158 (8V100V0, 39m59s). The later beta acceptance failed on 16V100 job
2359111, node `16v100n08`. Both se_e2_a and DPA2 failed in statistics
initialization when PyTorch `broadcast_object_list` detected `[0]` versus `[4]`
byte-tensor lengths across ranks. GPU creation and context initialization
passed. The failure occurred before training completed; it is not proof of a
compiler or hardware fault. Its underlying cause remains open.

4V100 job 2359113 and 8V100V0 job 2359115 passed both two-GPU training cases
and restart. Publication retained `deepmd-host-data-20261110-r1`, the previous
accepted generation. No failing candidate was promoted during this audit.

The publication date is explicitly `datetime.now(timezone.utc)` in
`controller/daily_beta.py`; the `20261110` snapshot on today's five publications
is a UTC/local-date discrepancy. The logical scheduler date is November 11.

All 24 scratch files in the reviewed November 10 cleanup plan still exist,
representing 143.60 GiB allocated when the plan was created. The stardust queue
was empty when inspected. No cleanup or SIF retirement was performed. The
current native LAMMPS module still points to the original 30Sep2026 installation;
update1 export/installation remains separate from its accepted SIFs.

Before preparing the help-path follow-up, GitHub had no open repository PRs and
the installed hashes matched all 31 entries from merged PR #88. The VM runner
was online and idle; the intentionally disabled VPS runner was offline.
