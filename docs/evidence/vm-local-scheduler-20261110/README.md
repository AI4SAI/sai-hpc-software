# VM local daily scheduler, 2026-11-10

The VM now owns the trigger clock using the enabled user timer
`sai-hpc-scheduler.timer`. The daily workflow becomes dispatch-only when this
change reaches main. New daily work is due at 03:00 Asia/Shanghai; a check every
five minutes reconciles delayed requests and interrupted relay observations.
The existing Windows boot/login task and Linux user lingering also start this
timer. A physical Windows reboot has not been performed.

GitHub cron had created neither the expected November 10 03:00 daily run nor
the intervening hourly recovery runs by the morning inspection. The workflow
was active and the runner online. This establishes an absent trigger, not a
GitHub-internal root cause. The full manual catch-up
[42729730793](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42729730793)
started at 08:37:36 local time and remains the day's generation. Installing the
timer did not interrupt it or request another full build.

## Live checks

- `systemd-analyze --user verify` accepted both installed units, and calendar
  evaluation produced five-minute ticks in `Asia/Shanghai`.
- The installed script successfully read the real workflow and recent runs.
- A transient service called the actual dispatch API from the VM at
  09:39:48–09:39:51, using the same script and credential as the timer.
  It exited successfully in 2.614 seconds, consumed 207 ms CPU and peaked at
  **16.6 MiB**, with no swap under a 64 MiB limit.
- That request created
  [smoke run 42737369348](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42737369348).
  At installation it was pending behind the active full build. HTTP acceptance
  and a queued run are not a completed runner/SSH smoke or scientific acceptance.
- The timer was enabled at 09:41:29. Its first real invocation returned
  `SCHEDULE busy`, exit 0, at 09:41:31. Peak cgroup memory was 15,360,000 bytes
  (14.65 MiB), and the next calendar tick was 09:45:00. The timer itself has no
  resident Python process.
- The calendar fired on its own at 09:45:00 and finished successfully at
  09:45:03, again detecting active work. That invocation peaked at 22,659,072
  bytes (21.61 MiB). [timer-state.json](timer-state.json) records the enabled
  timer, automatic invocation, memory limits, journal and installed file hashes;
  all three deployed files match the committed sources.
- A probe under the runner's actual Landlock sandbox could neither open the
  scheduler token for reading nor open its trusted Python script for writing.
  Both live permission checks returned `PermissionError`; no credential value
  was read or logged by the probe.

The operator's existing GitHub authorization was provisioned owner-only outside
the Actions tree. A repository-specific token with Actions read/write can
replace it at the documented path. Scheduler code and units are also outside
the job sandbox so a job cannot change the program that consumes this token.

## Regression coverage

Eight scheduler tests cover the local 03:00 boundary, queued/running work,
unfinished plan recovery, manual full-build recognition, partial/smoke
exclusion, permanent failure handling, lost HTTP replies, same-date retries,
and credential-file permissions. Seventeen relay tests include duplicate-date
reuse before source resolution and reject partial scheduled requests. The
existing 23 beta tests also passed locally.

The logical date is checked again under the relay's lock. A repeated dispatch
reuses the original frozen plan, including a failed plan, rather than creating
new Slurm jobs. Manual dispatch without a logical date is still available for
an intentional retry. An API outage may delay a dispatch; this change removes
dependence on GitHub cron timing, not on GitHub API availability.
