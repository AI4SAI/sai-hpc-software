# Frozen GPUMD / DeePMD API regression fixtures

Daily [45244259821](https://github.com/AI4SAI/sai-hpc-software/actions/runs/45244259821),
relay job 137936815559, ran on November 23 UTC / November 24 Asia/Shanghai.
These files reproduce that delivery's API mismatch without CUDA or a cluster.
They are validation samples, not a hardcoded dependency selection for future runs.

`gpumd_dp_91287ce2.cu.gz` contains the complete, unchanged upstream
[`src/force/dp.cu`](https://github.com/brucefan1983/GPUMD/blob/91287ce2993c211408f0229fafee3a9447e4ff16/src/force/dp.cu)
at `91287ce2993c211408f0229fafee3a9447e4ff16`.
Uncompressed SHA-256:
`5eba9c72b039efd8976edc7d74de1054adae2644f2d6c608968e21ff4ec6e8b6`.
The test extracts the actual canonical call. This source uses index-width
selection, int64 source ordering, `has_long_range()`, coordinates/cell/halo
arguments, and rejects partially periodic long-range models. Its copyright
and LGPL-3.0-or-later notice are preserved.

The header archives contain the installed `include/deepmd` directory, including
generated headers and original copyright/license notices. They were collected
read-only from the actual site environment and accepted SIF; this is stronger
evidence of the selected build interface than a current upstream checkout.

| Archive | Actual provenance | `DeepPot.h` SHA-256 |
| --- | --- | --- |
| `deepmd_headers_v3_2_0.tar.gz` | `/opt/apps/conda_env/deepmd-kit-3.2.0`, C API 30; header matches upstream tag `v3.2.0`, commit `8cfd46e37448feca361ff3427a6c2b514ce5a2bb` | `55782503945a685ad1438c79f9c69e34d952c114a9593afbc37b139badee714a` |
| `deepmd_headers_ed859426.tar.gz` | Accepted same-Daily 4V100 standalone delivery, C API 31, commit `ed859426333b462512c86cd496f9a194379a515f` | `24ca9dc84bbcaf0cb9743c3a0857bb2bdc5507e6111465254ce98ebf9f6e1e36` |

Upstream interfaces:
[v3.2.0 DeepPot.h](https://github.com/deepmodeling/deepmd-kit/blob/8cfd46e37448feca361ff3427a6c2b514ce5a2bb/source/api_cc/include/DeepPot.h),
[ed859426 DeepPot.h](https://github.com/deepmodeling/deepmd-kit/blob/ed859426333b462512c86cd496f9a194379a515f/source/api_cc/include/DeepPot.h).
The installed v3.2.0 generated version header does not record a Git SHA;
the commit above identifies the matching tag, not an inferred build receipt.

`deepmd_native_entry_ed859426.json` preserves the accepted manifest's exact
`entry` object (JSON formatting normalized). It records the real Python/plugin
and GCC 14 shared-library paths. GPUMD still selects GCC 13.3 for compilation.
The source manifest SHA-256 is
`d39ed59e408eb49ad65f5e5554c21ee5824aff49cdf9c4dc9ce191c3503f28e5`;
its SIF SHA-256 is
`f1fe9834b248f624dc49a55ef17409dc61c135a9b2b941fc5fe07166d1a91a88`.
The SIF is the recorded
`experimental/deepmd-standalone/runs/dp-45244259821-1-development-4v100-avx512-ed859426333b/candidate.sif`
under the SAI project. Its source version is `master-2026-11-18`.

The archives use deterministic gzip metadata. SHA-256 values:

```text
bd5aca89020d2dbc733bec7aca109391786447bce82fc12e2a9517215f81a57d  gpumd_dp_91287ce2.cu.gz
0bc8c00971e2d788e686d226d1d73e172c68ce14cd8e83f8946f055aa051216d  deepmd_headers_v3_2_0.tar.gz
b8f524cdb25c39d546b330964aa38195791ff40247c632907608014b45de1f1e  deepmd_headers_ed859426.tar.gz
76d367ac0fa65bf0fa947729b63b9fc616f1c3dfc19bf28fee61bdb39d3c8ef1  deepmd_native_entry_ed859426.json
```

The checked-in tests compile the real GPUMD call against both header sets and
exercise native export with synthetic library bytes; those bytes are clearly
identified in the test and do not establish executable ABI compatibility.
Separate local evidence for HPC-013 links the production probe against the
actual accepted `libdeepmd_cc.so` / `libdeepmd_c.so` and their collected CUDA
plugin libraries. No GPU inference or native installation was performed.
