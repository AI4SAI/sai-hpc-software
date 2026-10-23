# Independent DeePMD development acceptance

DeePMD-kit `master` at `558f56962775098b8b554e341c3b37d2f86e6aeb`
has build-verified, scientifically accepted candidate SIFs on **4V100,
16V100 and 8V100V0**. All three retain the same pinned source and build recipe.
These candidates have not been published or installed natively in `/opt`.
Their build and acceptance do not require a new LAMMPS build.

| Partition | Original build job | Independent science job | Current result |
| --- | --- | --- | --- |
| 4V100 | 1912999, 9m13s | 1913313, 28s, `4v100n34` | Final SIF and all scientific checks accepted |
| 16V100 | 1913001, 15m09s | 1913315, 50s, `16v100n09` | Final SIF and all scientific checks accepted |
| 8V100V0 | 1913003, 18m52s | 1913472, export + science, 3m31s, `8v100v0n04` | Recovered final SIF and all scientific checks accepted |

The original build allocations remain `FAILED`: 4V100 and 16V100 failed only
in the additional Nsight profiling step after the final SIF had passed its
binary and installation checks; 8V100V0 failed to mount its completed overlay
when entering export. The separate recovery/science allocations above are
`COMPLETED / 0:0`. Original requests, scripts, logs, controller snapshots and
SIF identities were preserved and independently checked before acceptance.

## What passed

The standalone build enables TensorFlow, PyTorch, JAX, the Python and C/C++
interfaces, CUDA SM70 and i-PI. The final SIF inventory retains the three
installed baseline backend plugins; 27 ELF files pass dynamic-library,
runtime-reference, symlink and installation-manifest checks. GCC 14.3 and
CUDA 12.9 use 12 parallel compile workers. Container affinity checks found
16 logical CPUs on 4V100/16V100 and 12 on the original 8V100V0 allocation.

Each scientific allocation evaluates the upstream serialized six-atom water
model through **TF/PT/JAX × Python/C API** for both the installed DeePMD 3.2.0
baseline and the new candidate. Every implementation has one warmup and three
repeated measurements: 48 unprofiled records per partition. Energy, all 18
force components and all nine virial components pass the unchanged upstream
reference tolerances. The saved fixture records the original model/reference
hashes and the converted input hashes. Local replay of the copied records
reproduces the complete acceptance result and checks each record against that
fixture.

An additional candidate PyTorch C-API inference is profiled separately.
Nsight records **1,486 CUDA kernel events** on each accepted partition,
including Volta DGEMM kernels. This establishes actual GPU execution. The
installed TensorFlow/JAX frameworks run on CPU with the allocated GPUs still
visible. This is an inference correctness check; it makes no training,
multi-GPU-scaling or application-performance claim.

The development checkout reports package version `0.1.dev1+g558f56962`.
This is the SCM-derived version of the pinned shallow `master` source; no
release tag or generated version from an older installation was substituted.
The delivery source identity retains the complete upstream commit and tree
`933e8d47c1666867f7754b9e3a0f8e19bbe609a1`.

## Build and continuation provenance

The compiled recipe is
`e04065e624bdf113dbcc32e713510472a1416bec81e664571ce84121024d54ea`, from
frozen controller commit `76869d13905944871c98c24860471883e4ed9aa2`.
For 4V100 and 16V100, the independent acceptance controller is
`d8b4c2fe338dbc8626bb78ffbebbf69bda572029`, fingerprint
`d02fe0936aba91b2af81e17ba5c842546416c8052290f7a04098230a6718a298`.
It validates the original build through its original frozen controller,
including exact rendered job scripts, immutable SIF SHA256, original binary
verification marker, and Slurm project account and working directory.
Acceptance does not relabel the SIF with the newer controller's recipe.

The profiling failure was `Cannot create CUDA config file`. The continuation
binds its private project runtime directory at container `/tmp`; all physical
scratch remains in the project tree. The same bind is now part of new builds.
It also selects each implementation's backend plugin directory explicitly.
No candidate recompilation was needed for the two accepted images.

For 8V100V0, original job `1913003` failed with
`fuse2fs failed to mount ... in 10s` after compilation and installation had
completed. `recover-8v100v0.py`, committed as
`e29f05a67f890b612f7035120d0cd0abf503afc2`, copied the completed overlay into
a separate project run with independent storage. It verified the complete
32 GiB logical contents before using a byte-identical frozen build controller
to export and verify the SIF. The same `d8b4c2` scientific checker then passed
all six interface/backend groups and the actual GPU-execution check. The
source/copy overlay SHA256 is:

```
166a3b361b39a302905d245b39a7ed458ea3958079736a067aa97be4f2860ece
```

A second complete read after recovery produced the same hash for the original
overlay. Its failed allocation and evidence remain unchanged.

The recovered SIF is 193,781,760 bytes and mode `0444`, under
`runs/dp-recover-8v100v0-20261023-r1/candidate.sif` in the project root below.
Its SHA256 is
`2fa0de48cc6716ec7431cd94fe891817cec33ea400b39e7ce6d575477c5b15d8`.

An earlier attempt on each partition stopped before compilation because the
baseline library inventory rejected normal SONAME symlinks. The inventory
now hashes the resolved regular file while recording the symlink resolution.
Those attempts (`1912965`, `1912967`, `1912969`) remain preserved.

All new runs, controllers, source caches and scratch are under:

```
/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone
```

Every accepted allocation uses account `proj-sai-hpc-software` and an explicit
project working directory. Private runtime/cache directories have inherited
ACLs removed and mode `0700`. The qualified FUSE helper is an independent
project copy with SHA256
`46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10`.

## Evidence and replay

- `delivery.json` lists the actual source/recipe identities, final SIF paths,
  hashes, jobs and acceptance/publication state.
- `4V100-` and `16V100-` files retain exact acceptance requests and job scripts,
  full scientific results, original build verification, CPU affinity, and
  SHA256/size records for the remote raw files.
- `8V100V0-` files retain the independent overlay recovery request, original
  failure, frozen controller/input hashes, allocation, final status and
  original-overlay integrity checks. `8V100V0-raw-files.json` records hashes,
  final binary verification and CPU affinity.
- `raw/4V100`, `raw/16V100` and `raw/8V100V0` contain all numerical records,
  source fixture manifests, Nsight kernel CSVs and original reports. They
  were checksum-verified after download and reparsed locally; model/reference
  hashes also match the pinned upstream Git objects.

To revalidate original provenance, the immutable SIF and raw science on the
cluster:

```bash
root=/org/org-open-sai/proj-sai-hpc-software
control=$root/experimental/deepmd-standalone/controller/d8b4c2fe338dbc8626bb78ffbebbf69bda572029/controller
for target in 4v100-avx512 16v100-avx2; do
  SAI_SOFTWARE_ROOT=$root PYTHONDONTWRITEBYTECODE=1 \
    python3 "$control/deepmd_acceptance.py" status "dp-science-20261023-$target-558f569-c"
done
```

The 8V100V0 recovery keeps its own frozen helper and status record:

```bash
run=$root/experimental/deepmd-standalone/runs/dp-recover-8v100v0-20261023-r1
helper=$root/experimental/deepmd-standalone/controller/deepmd-recovery-20261023/evidence/recover-8v100v0.py
python3 -B "$helper" status "$run" --verify-source
```

The independent reusable workflow is `.github/workflows/deepmd.yml`.
`daily.yml` invokes it separately from LAMMPS. It resolves the selected source
once across GPU targets, builds in project-backed container overlays and
requires the same final-SIF Python/C-API and measured GPU checks. Publication
and physical native installation remain separate steps.
