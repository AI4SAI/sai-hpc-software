Source fixture: `src/fm/cp_fm_cusolver.c` from CP2K development,
commit `0761fd1b052d62503997faf4d66c0f8b9cec03b7`.

https://github.com/cp2k/cp2k/blob/0761fd1b052d62503997faf4d66c0f8b9cec03b7/src/fm/cp_fm_cusolver.c

Uncompressed SHA-256:
`9f68fe666f1f1c9da411c8bd6dd7810dd8572c39c486efbe4afb4a3c15e14b31`.
The upstream copyright and GPL-2.0-or-later header are retained in the fixture.

This source includes upstream PRs #6465 (shared execution context) and #6466
(partial generalized eigenpair transfer). NCCL now initializes once per BLACS
context, with `ctx->comm`, instead of separately in three solver routines. The
MPI progress repair inserts one barrier between the unique-ID broadcast and
`ncclCommInitRank`, without modifying the context-reuse path or solver calls.
Reviewed repaired SHA-256:
`6fa681c45d5dce92eeb33a7047a851d5bcc5146133e4e50d48804a30f1fa7b40`.
