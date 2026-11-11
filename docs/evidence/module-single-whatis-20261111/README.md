# One whatis entry per public module

The new owner audit found the same 31 public modulefiles owned by `saiuser01`
across `/opt/modules/modulefiles/{apps,devtools,compiler,mathlib,mpi}`. All 31
installed files matched the preceding static-documentation update. Three hidden
backup files are excluded. The `pwmat` directory is still inaccessible to
`stardust`; its contents are not included in the coverage claim.

The administrator package now combines version/partition information and
`Features:` into one whatis entry. It still accepts the original modulefiles and
both preceding documentation revisions. Its location is:

```text
/home/stardust/sai-hpc-software/deployment/module-features
```

All 25 fixed modules retain byte-for-byte identical help and runtime content
after removing their whatis declarations. The six beta gateways collect the
selected generation's metadata and emit it once. They support historical
generations with one or two entries and new generations with a single entry.
The original Tcl command is restored even when the sourced module raises an
error. Sealed generations and their hashes are unchanged.

The SIF renderer, native fragment/selector renderers, catalog selector, and
LAMMPS release preparation script now generate one entry. Existing fixed release
module sources and their module hashes match the administrator package.

Validation:

- Real login-node Lmod: 62 whatis/help checks passed; every module has exactly
  one whatis entry containing Features and a readable static index in help.
- 134 focused controller/deployment tests passed, including historical/new beta
  metadata, source-error propagation and command restoration.
- The administrative preflight verified candidates, current installed hashes,
  owners, and static-record hashes before the package was handed off.

The local test run uses umask 022, matching CI; the first run under inherited
umask 077 hit existing fixture permission assumptions in native/QE deployment
tests. No installation permission rule was relaxed.

`lmod-preview.json` records the complete outputs. This is a prepared module
update; `/opt` changes remain the administrator's installation step.
