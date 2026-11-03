# Failures after the writable-image shutdown repair

Three independent causes explain the investigated failures: an uncorrectable GPU
ECC error on `8v100v0n09`, a DeePMD inventory check that assumes the newer backend
plugin layout, and the selection of ABACUS's historical `DCU` prerelease. These
failures do not indicate a recurrence of forced writable-image teardown.

This investigation changed diagnostic files only. Original failed runs remain
intact. The successful repetitions below are diagnostic evidence; they did not
replace production acceptance receipts or rotate the public ABACUS beta.

## ABACUS: GPU initialization fails on n09

Original development beta-route job `2151812` failed on `8v100v0n09` with
`cudaSetDevice failed! Device ID: 0`. Original release multi-node job `2151551`
used `8v100v0n[08-09]`; rank 1 on n09 exited with code 1 before SCF. The latter
log did not preserve a specific CUDA error code.

Fresh Slurm allocations compared the **same development SIF**, catalog, public
launch mechanism and numerical input. Each allocation first ran a native CUDA
runtime probe, outside ABACUS and outside its container. The probe recorded
allocated GPU identifiers, NVIDIA ECC counters, `cudaSetDevice` and context
initialization results.

| Job | Node(s) | Result |
| --- | --- | --- |
| 2154037 | 8v100v0n09 | Native `cudaSetDevice(0)` returned 214, `uncorrectable ECC error encountered`; ABACUS then reproduced the initialization failure |
| 2154039 | 8v100v0n04 | Both allocated GPUs initialized successfully; the unchanged development SIF passed the beta numerical check |
| 2154172 | 8v100v0n02, 8v100v0n03 | The unchanged release SIF passed the original two-node calculation and numerical threshold |

The affected visible GPU in job 2154037 was
`GPU-5765ddae-c92e-f9e0-6aef-c23b031929f3`, with one volatile uncorrectable ECC
event. The other allocated GPU initialized successfully. GPU indices in this
allocation are namespace-relative; the UUID identifies the observed device.
This proves an active GPU error, not whether its underlying cause is permanent
hardware damage or a recoverable device state.

The development control finished `0:0` in 22 seconds. Its energy error was
`6.366462912410498e-12 eV`. The release control finished `0:0` in 19 seconds,
converged with both GPU ranks, and differed from its reference by
`1.9879735191352665e-08 eV`, within the existing `1e-5 eV` tolerance. SIF, launcher
and input hashes were checked; the programs were not rebuilt. The original
release failure's terse output limits retrospective attribution, but the
failing rank's node, independent ECC reproduction and successful unchanged-image
control support the same node fault as its cause.

The automatic beta recovery did not run because `daily_beta.retry_node` requires
the explicit text `uncorrectable ECC error` (or a specific container-mount
failure). ABACUS emitted only the generic `cudaSetDevice failed` message.
Treating every generic CUDA failure as ECC would hide other faults. A useful
follow-up is to record structured CUDA health evidence in the allocation and
allow the existing bounded node-exclusion retry only for confirmed GPU faults.

An initial release diagnostic, job `2154076`, put MPI scratch below
`diagnostics/`. The launcher correctly rejected that path before the application
ran: `host MPI TMPDIR must stay below the SAI runtime roots`. The fresh retry
2154172 used `runtime-tests/`; both diagnostic records are preserved. The
production verifier's canonical run-identity guard was retained. The successful
diagnostic has its own explicit accounting, hash, input, rank and numerical
checks, and is not a substitute production acceptance receipt.

## DeePMD v3.2.0b0: installed backends use the older library layout

Jobs `2151449`, `2151528` and `2151559` built and installed the prerelease sources
but failed at `deepmd_standalone_check.inventory`:

```text
ValueError: DeePMD backend feature regression: []
```

The checker recognizes only `libdeepmd_backend_{tf,pt,jax}.so` plugin filenames.
The pinned prerelease `2e3117e9e4c4e0225cae5b1b43d50bd4f03811f6` predates that
layout: its `source/api_cc/CMakeLists.txt` links the backends into
`libdeepmd_cc.so`. All three `ENABLE_TENSORFLOW`, `ENABLE_PYTORCH` and `ENABLE_JAX`
options were ON; the configure log lists all three backends.

Read-only `debugfs` inspection of the failed 16V100 image found the installed
1,497,008-byte `libdeepmd_cc.so`, SHA256
`b632e530687bfd22ce1e8591de300cde707e515c2eba4eb4f4edd889a331df10`.
Its exported symbols include `DeepPotTF::init`, `DeepPotPT::init` and
`DeepPotJAX::init`; its dynamic dependencies include the TensorFlow, PyTorch and
CUDA libraries. The image was not mounted writable or reused for acceptance.

Thus the empty plugin list does not establish missing compiled backends. This
is a checker compatibility failure. A repair should recognize the two layouts
and retain actual Python/C-API, GPU and training tests. The library inspection
alone does not establish that the old prerelease passes those runtime tests.
All three failed jobs nevertheless recorded completed image teardown, driver
exit 0 and `forced_stop=false`; their payload exit remained 1.

Pinned source:
[old backend linkage](https://github.com/deepmodeling/deepmd-kit/blob/2e3117e9e4c4e0225cae5b1b43d50bd4f03811f6/source/api_cc/CMakeLists.txt#L8).

## ABACUS prerelease: the channel selects a 2022 checkpoint

The four prerelease jobs `2150457`, `2150501`, `2150503` and `2150513` selected
`DCU`, commit `e07f9d7213f51b72a9f77e7f832855d5d6cf9484`. GitHub labels this as
the prerelease **Checkpoint tag for DCU and GPU**, published April 27, 2022.
It is not a current 2026 prerelease. The source guard stopped each build before
compilation because this tree lacks the reviewed current HSE fixture:

```text
ValueError: expected exactly one reviewed ABACUS HSE benchmark case
```

`resolve_source.resolve` selects the newest publication within the requested
GitHub prerelease flag, independently of the stable release channel. This
explains both the old ABACUS checkpoint and DeePMD's June 6 `v3.2.0b0`, even
though DeePMD's stable `v3.2.0` was published August 19. The API selection is
consistent with the existing implementation; its policy does not distinguish
an active preview from a superseded historical one.

The daily channel policy should be reviewed separately from compilation:
current release and development are the primary delivery goals, while obsolete
prereleases need explicit eligibility rules or manual selection. This
investigation did not silently change channel selection or weaken the HSE gate.

## Evidence

The small JSON files expose the native GPU probes, successful controls, actual
old-library symbols/dependencies and upstream release metadata. `accounting.txt`
includes original failures and every diagnostic allocation.

`raw.tar.gz` preserves 126 text files: original logs and receipts, diagnostic
scripts, probes, MPI rank traces, successful numerical outputs, CMake settings,
symbol listings and pinned upstream source excerpts. `manifest.json` records
SHA256 for every uncompressed archive member and the collection timestamp.
Large original images remain on project storage.
