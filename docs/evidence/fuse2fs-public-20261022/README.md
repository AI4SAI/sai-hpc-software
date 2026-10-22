# Public fuse2fs package: installation preflight and staged qualification

The package in `deployment/fuse2fs-45651efd/` is ready for the `saiuser01`
administrator to install. The four standard payload files total 1,765,892 bytes.
Effective POSIX ACL evaluation confirms source read/traverse and target
directory creation permissions. Both `/opt/devtools` and
`/opt/modules/modulefiles/devtools` grant group `sai-partner` write/search
access; `saiuser01` is a member. No execution as that account was impersonated.

Job **1897998** ran on DSPRHBM `d9470n03`, completed `0:0` in **1m27s**, and
passed all **13** staged checks: the accepted C-version filesystem probe's
12 stages plus an actual mount selected by the standard module's
`APPTAINER_CONFIG_FILE` environment variable. The module also restored the
previous configuration on unload. Explicit fsync, OFD/writer exclusion,
read-only backing descriptors, content hashes, clean fsck/unmount/remount,
0600/0400 read-only images and the nonzero container exit passed. The final
image hash was independently reread after the job completed.

This used a home copy of the same qualified binary and a config/module with
the home test prefix substituted. **The public `/opt` prefix is not installed
or accepted by this test.** The administrator-operated installer and a public
post-install check are provided separately. Cross-account execution, broader
partitions and abrupt-termination recovery remain follow-up work in issue #12.

Seven installer tests passed on the actual home/Lustre filesystem. GitHub
Linux CI at `c391c4e` passed 932 tests (930 passed, two optional skips),
[workflow 39753008196](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39753008196).
Earlier failed attempts are retained: the initial installer used unsupported
Lustre `renameat2(RENAME_NOREPLACE)`; the replacement reserves a new directory
with `mkdir` and atomically renames over its own empty reservation. Job 1897917
exposed the site Lmod initialization's unset-variable behavior under `set -u`.
Job 1897996 used the archived A-version workload instead of the accepted C
version and rejected its known empty overlay-opacity marker. The final check
uses the unchanged C probe/verification rules. The driver bytes did not change.

Raw inputs, command/trace logs, failed images and results remain under
`/home/stardust/sai-hpc-software/diagnostics/fuse2fs-public-20261022/`.
`SHA256.json` pins the final frozen inputs and output files; `staged-result.json`
retains command-level results, image hashes and syscall-trace summaries.
No test or evidence file is included in the public installation.
