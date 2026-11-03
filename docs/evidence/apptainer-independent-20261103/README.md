# Independent Apptainer deployment, 2026-11-03

The `saiuser01` account can create `/opt/devtools/apptainer-sai`, but cannot
modify `/opt/devtools/apptainer/1.4.4`. The deployment now installs the full
runtime into `/opt/devtools/apptainer-sai/1.4.4`, preserving the original.
The new `apptainer/1.4.4-sai` module is a separate activation step.

The payload is the existing site distribution with only the already qualified
starter substituted. CLI SHA256 is
`2ba9ae17c087a0d8a6bbfeaae0b12775bd8724f51d5452c41e951ea853a8e5da`;
starter SHA256 is
`80cf5306207ea473a4b412a617e8594accebf9bc05413f5cb1f9f8cde7e2b529`.
The complete archive is 94,231,744 bytes, with 197,120,017 bytes in regular files
and 262 inventory entries. The deployment manifest pins the archive, inventory,
source and patch. No binary is committed to Git.

Installation was exercised in a new project directory. `apptainer buildcfg`
confirmed relocation of BINDIR, LIBEXECDIR, SYSCONFDIR, APPTAINER_CONF_FILE,
SESSIONDIR and PLUGIN_ROOTDIR, with APPTAINER_SUID_INSTALL=0. The new-prefix
qualification used the packaged CLI and starter from that directory.

Slurm job **2144865**, DSPRHBM `d9470n01`, completed **0:0**, **4:06**. Through
the existing permitted Apptainer's private namespace, all four cases passed:
two 512 MiB overlays, a sparse 32 GiB overlay and an injected 2-second fsync
delay. Each case explicitly synchronized, exited without forced driver
termination, passed offline `e2fsck -fn`, and verified every payload byte/name
on a separate read-only remount. `relocated-qualification.json`, raw job output,
script, accounting and checksum receipt are retained here. Large overlays and
traces remain under
`/org/org-open-sai/proj-sai-hpc-software/diagnostics/apptainer-independent-20261103`.

**Native public-path readiness is still blocked.** The direct relocated launch
on the same node returned `Operation not permitted` (see `native-check.json`).
The cluster restricts unprivileged user namespaces and currently permits only
the original Apptainer 1.2.4 and 1.4.4 executable paths. The supplied additional
AppArmor profile passed `apparmor_parser --skip-kernel-load --skip-cache` with
exit 0; it was **not loaded**. A system administrator must deploy it on relevant
nodes before native verification and module activation. Private namespace
qualification does not establish that this policy change has happened.

The installer prevents module activation after a failed native launch. It
refuses different existing payloads/modules and preserves the old runtime.
The guarded build controllers select the independent module once activated,
validate CLI/helper hashes and record the actual runtime identity. They retain
cleanup-failure rejection and immutable failed-overlay evidence. No full
scientific rebuild is claimed for this deployment change.
