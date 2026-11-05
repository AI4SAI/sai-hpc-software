# ABACUS daily acceptance: preserve each MPI rank's output

The November 5 daily workflow built all four development partitions at
`3c1dddac06678e1b37752c9752c316f8713f499b`. CPU DeePKS job `2216449`
completed successfully, but its two node-local diagnostics were interleaved
by MPI's forwarded output. One line contained
`OpenMP thread number: 4,OpenMP thread number: 2`, even though each node
used the requested two threads. The strict verifier rejected that result.
The original logs and failed acceptance status are preserved.

Each existing rank affinity wrapper now redirects its own stdout and stderr
to separate files before executing ABACUS. MPI exit status still controls
completion. After MPI finishes, the controller concatenates complete streams,
including stderr where ABACUS emits its thread report. New `direct-v1`
requests also recheck every raw stream, compare their contents with the
combined logs and include all file digests in the scientific evidence.
Numerical, thread-count, affinity and performance thresholds are unchanged.

## Same-image recovery

The recovery reused the already-built SIF, SHA256
`6a993b9257970ac015b32cd390424b362b170c0ca13446a7526a1167b57d47ff`,
with the original frozen controller and scientific verifier. Only the new
job's log capture code and corresponding script/request hashes changed.
No SIF or original controller was edited and no compilation was repeated.

| Job | Result | Explanation |
| --- | --- | --- |
| 2216449 | COMPLETED 0:0; not accepted | Original mixed MPI thread diagnostics |
| 2218191 | FAILED 1:0 | MPI `--output dir` produced `PMIX_ERR_EXISTS` and incomplete streams |
| 2218257 | COMPLETED 0:0; not accepted | Direct capture worked; collector initially omitted stderr from the combined verifier input |
| 2218404 | COMPLETED 0:0; accepted | Complete independent streams, all eight alternating benchmark runs verified |

The final DeePKS candidate/system SCF iteration medians are 0.68/0.73 s
(ratio 0.93151); whole-process medians are 4.31/4.85 s (ratio 0.88866).
These small fixtures establish the existing acceptance gates, not a general
performance benchmark. The two-node runtime, PW and HSE proofs were reused
after their original frozen verifier rechecked them. The controller then
published the CPU artifact, completing the four-partition development matrix.

[recovery.json](recovery.json) contains the artifact identity, original
corrupt line, all allocation accounting, preserved failure status and final
acceptance proofs. A separate read-only replay through the new verifier
passed the same completed allocation, including raw-stream digests in
[current-verifier.json](current-verifier.json). This supplemental replay does
not replace or relabel any original allocation.

The focused benchmark tests cover descriptor redirection, partial writes,
application exit-code propagation, actual wrong thread counts, missing/extra
streams, redirected files and post-collection changes. Publication fixtures
also exercise the new raw-stream contract.

## Beta publication

The ordinary beta workflow
[42028012790](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42028012790)
revalidated the four original SIFs and passed fresh module/SCF checks in jobs
`2218662`, `2218664`, `2218666` and `2218668`, each `COMPLETED 0:0`.
It rotated `abacus/develop-beta-later` to
`develop-beta-20261105-3c1dddac0667-r4d85319d4295-bd63fae8d9620`.
The [publication record](beta-publication.json) includes the complete
candidate matrix, partition checks, accounting and alias observation.
The original daily workflow retains its historical failed acceptance result;
the successful recovery and beta run are recorded separately.
