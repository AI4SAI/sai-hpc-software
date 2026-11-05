# ABACUS beta12 administrator handoff

This package promotes the existing accepted
`v3.11.0-beta12@6b24db05dc175d6e1d7894ac380f48118b43fbaa` binaries without a
rebuild. It uses the four release SIFs and unchanged scientific receipts from
the [November 3 acceptance](../prerelease-ecc-recovery-20261103/README.md).
Live November 5 checks confirmed all 19 functional and multinode jobs completed
`0:0`, and the matching native exports remain verified.

The public package is
`/home/stardust/sai-hpc-software/deployment/abacus-3.11.0-beta12`.
Only four compressed images are copied to the administrator-accessible home
directory. Image/publication/manifest hashes are fixed in
[`release.json`](../../../deployment/abacus-3.11.0-beta12/release.json), and the
standard runtime selection in
[`runtime-plan.json`](../../../deployment/abacus-3.11.0-beta12/runtime-plan.json).
The native runtime is 76 regular files and four relative command links across
four partitions, totaling 1,971,846,664 bytes. The module is installed separately.

| Evidence | Result |
| --- | --- |
| [Real SIF stage installation](stage-acceptance.json) | Four partitions installed under the project operation directory; repeat install and full hash/mode/link verification passed |
| [Administrator access](administrator-access.json) | Effective POSIX ACLs permit saiuser01 to read the public package and write both target parent directories; no impersonated execution |
| [Login module help](module-login.json) | Version/source/help printed; allocation-required load rejected without setting the ABACUS prefix |
| [Scientific reference preflight](native-reference-preflight.json) | All beta12 input and original output hashes and energies rechecked |

The project operation directory is:

```text
/org/org-open-sai/proj-sai-hpc-software/operations/abacus-beta12-handoff-20261105
```

It retains the preparation log, reference preparation, staged install script,
full extraction/verification log and resulting software tree. No physical
`/opt` files were changed. The administrator still needs to install beta12;
the subsequent 30 native scientific calculations will verify that final
installation. Those new jobs have not been submitted during package preparation.

Local deployment checks passed 36 tests, including corrupted sources,
preservation of different existing files, pruning of build material, runtime
library retention, matching image/identity acceptance, and public-only preflight
without access to the restricted native export.
