# Shared-ELPA CP2K administrator handoff

The [deployment package](../../../deployment/cp2k-2026.2-shared-elpa/README.md)
is prepared at `/home/stardust/sai-hpc-software/deployment/cp2k-2026.2-shared-elpa`.
It selects accepted recipe `aed7aa53ff43` from workflow `39365139034`, with 56/56
science calculations, verified SIFs and native exports. Preparation rehashed all
four SIFs and every selected runtime file; the administrator preflight then
passed using the pinned native files and the installed shared ELPA inventories.

| Partition | Regular runtime files | Runtime size |
|---|---:|---:|
| 4V100 | 443 | 477.49 MiB |
| 16V100 | 443 | 474.52 MiB |
| 8V100V0 | 443 | 474.91 MiB |
| DSPRHBM | 442 | 469.15 MiB |

Total payload is 1,988,173,828 bytes (about 1.85 GiB), excluding the small
deployment receipts. The complete original CP2K data are retained, with no
added examples. Shared ELPA stays at its accepted `/opt/devtools` prefixes.
The package pins its installer, public/previous modules, shared dependency lock,
artifact sidecars, native manifests and the exact selected runtime receipts.

The source preflight took about 11 seconds with the filesystem cache warm;
preparation's initial image/file pass took about 64 seconds. Timing depends on
storage cache and load. `--install` includes preflight, so a separate `--check`
is optional. All temporary copying happens beside the final target prefix.

The five new release/partition parent directories received `saiuser01:r-x`
ACLs. The [effective ACL audit](access.json) checked 2,120 source/package paths:
all required read/traverse access passed and none granted this account write
access. Ancestors need traversal only. This is an ACL calculation using the
account's UID/groups, not an impersonated execution. Frozen export contents and
their recorded modes were not changed.

At preparation time the new `/opt/apps/cp2k/...raed7aa53ff43` prefix was absent,
and the public module matched the pinned previous release. **No public install
or module switch has been performed by this preparation.** Administrator install
and subsequent 56-case physical runtime acceptance were pending at handoff.
The administrator subsequently installed it, and all four `stardust` physical
acceptance jobs passed. See the [completed physical runtime acceptance](../cp2k-shared-elpa-native-20261020/README.md)
for all 56 calculations and independent raw-log revalidation.

Evidence: [package hashes and observed public state](preparation.json),
[original preparation log](prepare.log), [administrator preflight](preflight.log).
Four-partition deployment, corruption/unknown-module rejection, idempotence,
shared-dependency checks and old/new module unloading passed 14 local tests.
