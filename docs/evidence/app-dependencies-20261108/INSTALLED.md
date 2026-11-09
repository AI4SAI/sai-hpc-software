# Physical dependency installation acceptance

After the user installed the handoff, both physical prefixes matched their
complete manifests. The following jobs compiled and ran native consumers on
compute nodes, without launching a SIF or modifying `/opt` or module files.

| Dependency / partition | Job | Node | Elapsed | Result |
| --- | --- | --- | --- | --- |
| HDF5 / DSPRHBM | 2301062 | d9470n02 | 6 s | COMPLETED 0:0 |
| LibTorch / DSPRHBM | 2301067 | d9470n02 | 21 s | COMPLETED 0:0 |
| LibTorch / 16V100 | 2301076 | 16v100n12 | 17 s | COMPLETED 0:0 |

HDF5's 170 installed entries (33,986,628 bytes) match the accepted inventory.
NVHPC 26.3 / OpenMPI 5.0.10 built the consumer through the installed CMake
package. Imported target locations and the actual link command select all three
HDF5 static libraries from
`/opt/apps/qe/dependencies/hdf5/1.14.6-nvhpc26.3-ompi5.0.10-f90/DSPRHBM/lib`.
Two ranks confirmed HDF5 1.14.6 and read back values 17 and 18. Required dynamic
libraries resolved. The optional CUDA-plugin warnings on the CPU node did not
prevent this CPU MPI test. The [receipt](raw/installed/hdf5/r3/results/acceptance.json)
references 18 raw evidence files; all are archived and their hashes match.

LibTorch's 8,553 installed entries, including 8,418 ordinary files
(702,822,281 bytes), match the accepted inventory. Both GCC 13.3 consumers pass
checked matrix multiplication, TorchScript save/reload and loading the existing
ABACUS DeePKS model. Their `ldd` output resolves `libtorch.so`, `libtorch_cpu.so`
and `libc10.so` to
`/opt/apps/abacus/dependencies/libtorch/2.1.2-cpu-cxx11abi1/lib`, with no missing
libraries. The [archive](raw/installed/libtorch.json) contains requests,
scripts, compiler/model hashes, command outputs, mount information and accounting.
The recorded request, verifier and submitted-script hashes were independently
checked against the archived bytes.

Earlier failed test setup is retained: HDF5 job 2300961 stopped before
compilation because `--export=NIL` omitted the user HOME needed by OpenMPI's
wrapper query. Job 2300975 was canceled by root before payload execution; its
scheduler record does not establish why. A standard `--export=ALL` submission,
followed by the existing QE environment setup, passed. The initial LibTorch GPU
submission was rejected before allocating a job because that partition disallows
explicit `cpus-per-task`; the corrected submission used its existing GPU resource
policy. Tests and temporary/configuration files stayed in project storage.

This confirms physical library installation and consumer compatibility. It does
not activate daily QE/ABACUS reuse or replace a build and representative
application calculation against the new providers. LAMMPS's generator and
production build options remain unchanged.
