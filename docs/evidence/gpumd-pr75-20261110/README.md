# GPUMD NEP-format and beta-route qualification

GPUMD commit `1ce6ae2ab2c77a9a155412b44f4f4badc594fd69` removed the legacy
MD model reader. PR #75 converts the fixed candidate model with that source's
upstream converter while keeping the installed baseline's original encoding,
energy/force gold arrays and acceptance tolerances. GPUMD overlay operations
also use the existing managed driver.

[Workflow 42438886026](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42438886026)
at controller `d57757f9b8bb0f11d2070cf6a2b446e5bb2ad8d5` already passed all
three builds and their complete scientific acceptance on November 8. The
final beta step rejected `invalid beta source plan`: the test explicitly
selected the fixed source SHA instead of `master`. This publication guard is
preserved; these pinned-source images were not rotated into the public beta.

| Partition | Original science job | Additional SIF/Lmod check | Energy / force error |
| --- | --- | --- | --- |
| 16V100 | 2297073 | 2329882 | 0 / 0 |
| 4V100 | 2297080 | 2329884 | 0 / 0 |
| 8V100V0 | 2297157 | 2329886 | 0 / 0 |

The November 10 checks reused those exact immutable images. Their original
scientific acceptance was revalidated with each build's frozen controller.
The isolated module bundle used the production SIF launcher, automatic memory
namespace and GPU-health checks on each partition. All three jobs completed
successfully and `beta_check.verify` independently rechecked their results.
The reported errors are against the fixed numerical fixture, not an exhaustive
scientific or performance qualification.

The beta checker now reads the model selected by the accepted SIF's manifest.
It first requires the original model hash to match the beta fixture, then reads
and checks the converted model's bytes if present. Older images retain their
legacy model. The fixed geometry, gold results and tolerances are unchanged.
Image identity, both model hashes and the selected image path are saved in the
runtime request; modified model bytes fail the existing fixture checks.

- Legacy model SHA-256: `3fcf12dc7dbc9bdd834e250662a429198fcfb3ed4393e943f685b17ff960bf54`.
- Converted model SHA-256: `79c5ab0d4e731d35f0407b93d457459b136838e6cdd735858536feff8fc6d7f0`.
- Runtime evidence: `/org/org-open-sai/proj-sai-hpc-software/runtime-tests/gpumd-pr75-beta-20261110-r2`.
- [Qualification](qualification.json) records controller hashes and all three runtime reports.
- [Original acceptance](original-acceptance.json) records the earlier requests, image identities and scientific evidence.
- [Raw runtime archive](runtime.raw.tar.gz) contains requests, scripts, numerical outputs, traces and module/catalog files, without SIF images.
- [CI log](validation-9561754.log.gz): 1,298 tests passed, 2 skipped, at code commit `95617548b8eddfd377b4ad145d5810943b49f7a2`.

Four additional tests use real Squashfs images to check modern/legacy selection,
unchanged gold data, foreign model/build rejection and image/content hashes.
The initial isolated test setup used a hyphenated snapshot date and was rejected
before submitting any Slurm job. Its script and failure log are preserved under
`setup-failure-r1/` in the raw archive; the corrected setup used `20261110`.
This follow-up compiled no application and changed no public module pointer.
