# Fresh CP2K release follow-up, October 17

Workflow [39041714911](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39041714911)
builds v2026.2 source `67b5da876dd6a76b8b021d5a04d1c81ba79a4c50` with frozen
controller `2c7fd3e097cf61c0d0ee6612cb2cc96eebb96677` and recipe
`a280393e353fee8faa1c09209b34c42eb0f55d0a44ad0543bed1ed57101735ec`.

- `cpu-2303.json`: ten-second whole-job cgroup samples; 8V100V0 was running
  the serial Libint generator alongside PLUMED's Python-wrapper compiler.
- `cpu-2312.json`: follow-up ten-second samples; all four targets were compiling
  Libint's generated C++ sources in parallel. Raw process/cgroup samples remain
  at the recorded paths under SAI project home.
- `status-2318.json`: Slurm accounting, result-file presence and log tails at
  23:18 Asia/Shanghai. This is a historical snapshot, not the final result.

| Target | Used logical CPUs at 23:12 | Allocated logical CPUs | Allocation utilization |
|---|---:|---:|---:|
| 16V100 | 7.53 | 8 | 94.1% |
| 4V100 | 7.22 | 8 | 90.3% |
| 8V100V0 | 11.43 | 12 | 95.2% |
| DSPRHBM | 6.62 | 8 | 82.7% |

Here 100% means the complete Slurm CPU allocation, not one logical CPU.
For example, 11.43 logical CPUs corresponds to approximately 1143% under
`top`'s one-core-equals-100% convention. Usage includes filesystem-driver CPU
and short-lived compiler processes. No CPU throttling was recorded in these
intervals; stage samples do not establish whole-build or CP2K runtime speedup.
The sampler uses direct compute-node SSH, never an attached `srun` step.

The user prioritizes conventional PBE, with both OT and diagonalization
coverage. Further direct-generalized eigensolver tuning is tracked in
[issue #9](https://github.com/AI4SAI/sai-hpc-software/issues/9) and does not
block the current build or change its frozen identity. The immediate objective
is a verified new release SIF, followed by the existing acceptance workflow.
