Source fixture: `src/fm/cp_fm_cusolver.c` from CP2K development,
commit `be0de0a77d0ca1bed2183bdd778b7d8816d835f2`, frozen by Daily
45244259821 on 2026-11-23 UTC (2026-11-24 Asia/Shanghai).

https://github.com/cp2k/cp2k/blob/be0de0a77d0ca1bed2183bdd778b7d8816d835f2/src/fm/cp_fm_cusolver.c

Uncompressed SHA-256:
`8953ed28c2dc261c00e55e8d5a51ff9dc26050de28934ae54fc7f102a1386c23`.
The upstream copyright and GPL-2.0-or-later header are retained.

Compared with the reviewed `0761fd1b` fixture, upstream #6611
(`abc7fe34373bc6b8c4f2cf7851f2cae6ebcc6f92`) replaces `assert` with
`cp_assert` and its header. It does not repair MPI progress: `get_context()`
still broadcasts the NCCL unique ID on `ctx->comm` and immediately enters
blocking `ncclCommInitRank`. Insert the same single barrier on `ctx->comm`
after that broadcast. The context-reuse return and solver calls are unchanged.

Reviewed repaired SHA-256:
`8dd2ad5635cdbc4069b499c2fdc69e71eb7e8a012441345104bdedb2deccfaaf`.
