# ELPA: retain the existing auto module name

The administrator CPU supplement now replaces
`elpa/2026.02.001-2603-gnu-auto` with the tested partition selector. It retains
the same accepted ELPA binaries and canonical installation prefixes. The initial
`-partition` candidate remains historical evidence; it is not a second installed
public module.

| Partition | Job | Node | Selection | Execution |
| --- | --- | --- | --- | --- |
| 16V100 | 1861785 | 16v100n02 | AVX2 / NVIDIA | Physical GPU prefix |
| 4V100 | 1861786 | 4v100n06 | AVX512 / NVIDIA | Physical GPU prefix |
| 8V100V0 | 1861787 | 8v100v0n04 | AVX2 / NVIDIA | Physical GPU prefix |
| DSPRHBM | 1861788 | d9470n02 | AVX512 / CPU | Accepted final CPU SIF |

All four jobs completed `0:0`. Each loaded the candidate under the exact public
`-auto` name, recorded the module file actually loaded, compiled a fresh C
consumer and passed two repeated complex ELPA2 solves with two MPI ranks and
nonblocking collectives. Loader traces prove both ranks initialized the expected
ELPA library. All four unloaded successfully after the current Slurm partition
variable was changed. The three V100 jobs additionally loaded the original auto
module, replaced its file with the candidate in the test directory, and unloaded
the original environment successfully. No physical module was replaced by these
tests. CPU correctness and the unchanged SIF identity remain covered by the
[24-case build acceptance](../elpa-cpu-20261019/README.md).

An initial set of checks (`1861766`–`1861769`) loaded the existing system module
because the test's two `module use` arguments gave the site path priority.
The V100 scripts stopped on the missing new backend marker; DSPRHBM attempted
the old NVIDIA prefix. The corrected test prepends its candidate directory last
and verifies `_LMFILES_`. The failed job records and frozen controls are preserved
in `initial-module-path-failure/`; passing results are in `partition-module/`.

[Independent revalidation](partition-module/revalidation.json) checks the original
CPU SIF, all CPU numerical cases, Slurm completion, the new module hash and name,
both-rank loader evidence and legacy unload markers. The module SHA256 is
`99cd8949523e65450f924be076304058b395c5c20d9840b82cb54561cbea0a4f`;
the only replaceable prior module is
`cce432ff7bcbb6f6001baa79f4620c53ed26419dff0dcc2d78076018b0bbcbf6`.

The refreshed package at
`/home/stardust/sai-hpc-software/deployment/elpa-2026.02.001-cpu` passed read-only
preflight against both existing GPU manifests and the 12.81 MiB CPU payload.
Installation first verifies/installs the CPU prefix, preserves the known prior
module in a hidden file beside it, then atomically replaces the public auto
module. Unknown module content is refused. A repeated installation verifies the
new module and leaves it in place. The previous administrator handoff is retained
under the development checkout's `.work/elpa-cpu-handoff-before-auto-20261019-205337`.

Eighteen focused local ELPA tests passed, including legacy session unloading,
unknown-content refusal, backup/idempotence and preservation of the original
module when replacement fails. Shell syntax and Python compilation checks also
passed. The administrator still needs to run the installation, followed by
physical CPU runtime acceptance. These checks do not measure large-system
performance or multi-node scaling.
