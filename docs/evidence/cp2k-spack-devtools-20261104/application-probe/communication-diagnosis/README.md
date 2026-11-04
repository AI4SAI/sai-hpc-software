# 4V100 ScaLAPACK / NCCL progress investigation

The repeated two-rank stall is a wait across MPI-based ScaLAPACK and NCCL
bootstrap. A timed in-process stack sample found rank 1 in
`pdsygst -> pdtrsm -> PB_CInV -> BI_Srecv -> PMPI_Recv`, while rank 0 had already
entered `cp_fm_diag_cusolver -> pncclCommInitRank -> accept`. Rank 0 therefore
stopped driving MPI while its peer still needed to complete the preceding
ScaLAPACK operation.

An isolated interposition experiment added `MPI_Barrier` after CP2K's 128-byte
NCCL-ID broadcast. It allowed MPI progress until both ranks reached bootstrap.
The previously stalled unchanged 64-water GPW input completed in **238.64 s**,
including converged SCF and forces. Its differences from the accepted CPU result
were `8.185452315956354e-12 Ha` in energy and
`1.1999999952050366e-11 Ha/Bohr` in force components, below the existing tolerances.
The CP2K binary was unchanged and GPU acceleration remained enabled.

| Diagnostic job | Change | Actual calculation outcome |
| --- | --- | --- |
| 2200711 | C MPI call trace | Stalled; 180 s diagnostic limit |
| 2200711 | Explicitly disable HCOLL | Stalled; 600 s limit |
| 2200836 | Start ranks under GDB | CP2K never started: `ptrace` denied |
| 2200836 | Select OpenMPI OB1 | Stalled; 600 s limit |
| 2201054 | Timed in-process stack sample | Captured the distinct waits; 120 s limit |
| 2201054 | Disable NCCL CUDA memory allocation paths | Stalled; 600 s limit |
| 2201302 | MPI barrier before NCCL bootstrap | Completed; numerical comparison passed |

HCOLL and UCC were already disabled in the actual configuration. The sampled
default transport was also OB1. Neither changing these settings nor changing
NCCL allocation paths resolved the stall. No ECC or filesystem failure was
established. Failed GDB attachment/startup attempts are retained; their launcher
exit status is not evidence that CP2K ran.

All four *diagnostic drivers* completed `0:0`, because they deliberately record
bounded failing cases and then close cleanly. This is not four successful
scientific verifications. Every diagnostic used independent copies of the clean
original application overlay and dependency SIF. The source/copy checksums
matched, and all read-only managed lifecycles ended without forced termination.

## Source repair and completed rebuild

The inspected v2026.2 and development sources have identical
`src/fm/cp_fm_cusolver.c` bytes:
`379cda72ac8c18e18f30ffd019b37ae0e8216572c40f5e6ad2fbceb6decd4ad8`.
The repair adds the same synchronization to all three real/complex NCCL
bootstrap sites; repaired SHA-256 is
`eb22082437dbee127fedd77a8f7e2f7cb05444b507b2a4be29843cc1fa9ceff3`.

Commit `ab56139` applies this repair only to the reviewed original or already
repaired file, records exact before/after hashes and rejects changed upstream
code for review. Four focused source-repair tests passed, including the actual
compressed upstream source, repeated application and rejection without mutation.
No upstream PR or cluster library change is part of this investigation.

The interposition experiment establishes the repair mechanism; it is not an
accepted rebuilt artifact. Subsequent job **2201497**, run
`cp2k-devtools-app-20261105-n-4V100`, compiled the actual patched CP2K source against
another copied dependency SIF and passed all 14 scientific samples. Its
recipe is `57163d7ef63677e304aece8d15d518ca84bbe976164bb82cd0c4169c1c6b22b3`.
It completed `0:0` in 27m31s on 4v100n18, with the previously stalled case
finishing in 237.49 s. The separate [rebuild evidence](../rebuilt-4v100/README.md)
verifies the source repair, changed core-library hash, full numerical checks and
clean teardown. This diagnostic archive retains its original collection state.

[summary.json](summary.json), [raw.tar.gz](raw.tar.gz) and
[archive.json](archive.json) preserve 137 manifest-verified raw records, including
the exact diagnostic source/scripts, stack samples, failed controls, input
hashes, numerical comparison, accounting and preservation receipts.
