# Independent LibXC reference protocol

This protocol checks the **unchanged final SIF's** LibXC 7.1.2 HOST implementation
against previously published Density Functional Repository values distributed
in the pinned LibXC source archive. It does not change the failed default
built-in-PBE/LibXC comparison or its thresholds, and it does not confer normal
CP2K science, publication or installation acceptance. The original CP2K
HOST/DEVICE comparison and its CUDA execution evidence remain separate checks.

The source archive SHA256 is
`3915fac94416e4c415534223ea492ad2663f928acf27e98662c861b094a6c306`.
The two raw files are extracted byte-for-byte, including their attribution to
`http://www.cse.clrc.ac.uk/qcg/dft/`; no reference values are generated from the
candidate library. The original website has not been independently retrieved
for this check. The archive is the immediate provenance of the data.

| Archive member under `libxc-7.1.2/` | SHA256 |
|---|---|
| `testsuite/df_repo/gga_x_pbe.data` | `25136ea8b025c51c1ea818b040503e2cb0717b41d1ca72311fab42dd1e59da5e` |
| `testsuite/df_repo/gga_c_pbe.data` | `75193580a06223c04acfc3080171c5880ecf485230295e458463fb80b70eb6c8` |

LibXC's `testsuite/Makefile.am:41` includes `df_repo/*.data` in distributed
reference data. Inspection of that version's test drivers did not find an
active test consuming these files or an applicable upstream tolerance. This
protocol therefore declares its own precision rule; it does not claim to run
LibXC's complete upstream test suite.

## Fixed rules before loading the candidate

Both files contain 23 density/gradient records with energy, first and second
derivatives. This check uses energy and first derivatives. All 23 raw records
are retained. Selection depends **only on exact decimal input values**:

- Each spin density must be positive. Records 12–15 have a zero spin density;
  LibXC 7.1.2 floors individual spin densities in `src/work_gga_inc.c:50–57`, so
  the unregularized endpoint is outside this protocol.
- The gradient Gram matrix must be positive semidefinite:
  `sigmaaa >= 0`, `sigmabb >= 0`, and `sigmaab² <= sigmaaa*sigmabb`.
  Records 17, 22 and 23 violate Cauchy–Schwarz and cannot represent two real
  spin-density gradient vectors. LibXC additionally clamps the cross term in
  `src/work_gga_inc.c:59–64`. These records are excluded before any library call.
- The remaining 16 records exercise the polarized API for each functional.
  Of these, records 1–11 have equal spin densities and identical three gradient
  contractions; these also exercise the unpolarized API. Record 21 has equal
  densities but unequal gradient contractions and is not used for that mapping.

The raw reference `zk` is energy per volume; LibXC returns energy per particle
`exc`, so the check compares `(rhoa+rhob)*exc` to `zk`. Polarized `vrho` and
`vsigma` are compared directly. For spin-symmetric records the exact chain rule
gives:

```text
rho = rhoa + rhob
sigma = sigmaaa + 2*sigmaab + sigmabb
vrho = (vrhoa + vrhob)/2
vsigma = (vsigmaaa + vsigmaab + vsigmabb)/4
```

Reference output numbers are printed to 12 significant decimal digits. For
each nonzero reference value the fixed allowed error is **10 units of its last
printed decimal place plus 128 binary64 epsilons times its absolute value**.
This allows a small guard beyond rounding of archived decimal output while
remaining tied to its precision. Printed zeros use absolute `1e-12` in the
quantity's atomic units. Bounds for the unpolarized chain-rule mapping are the
same weighted sums of the original component bounds. NaN or infinity fails.
No error is divided by a small reference value, and no observed candidate error
is used to select points or choose a tolerance.

`protocol.json` records every selection and exclusion reason, source hashes,
parameter defaults, transformations and this precision rule. Its SHA256 must
be supplied explicitly to verification. The script validates that hash and
reconstructs the entire protocol from the fixed raw files **before** loading
the candidate library. The caller must also freeze the script and protocol
hashes in its independent submission request before running the job.

## Library and execution boundary

The GPU delivery provides its identity-specific
`dependencies/libxc/lib/libxc.so`. The DSPRHBM delivery embeds static LibXC in
`lib64/libcp2k.so.2026.2`; its dynamic symbol table exports the C API used here.
The caller supplies the exact library checksum from the final SIF. The script
requires the library to be inside the supplied install prefix, verifies its
checksum, and uses `dladdr` to require every API symbol to come from that same
file. It checks version 7.1.2 and the actual default PBE parameters.

`ctypes` signatures follow the pinned `src/xc.h`; opaque objects are allocated
by LibXC, not reconstructed in Python. `xc_func_init_flags(..., XC_FLAGS_ON_HOST)`
explicitly selects host execution, and the initialized flags are checked.
This tests the library's independent mathematical reference and does not
replace the existing GPU invocation evidence. The same Python script works
with the CPU static provider and GPU shared provider without compiling code.

Prepare references locally, before candidate execution:

```bash
python3 -B libxc_reference.py prepare \
  --archive /path/to/libxc-7.1.2.tar.bz2 \
  --output-directory /new/path/reference-v1
```

After the parent workflow verifies the sealed SIF identity/hash, stage the
script and reference directory in a **new** project-backed diagnostic task.
Run inside that final SIF after its original `runtime-env.sh`, with the same
read-only site binds and project-backed runtime directory as the original
launcher. The parent runner must retain the SIF identity/hash, allocation,
staged file hashes, command and process exit code. This script does not submit
jobs or modify a frozen launcher:

```bash
python3 -B /work/libxc_reference.py verify \
  --library "$prefix/dependencies/libxc/lib/libxc.so" \
  --expected-library-sha256 "$library_sha256" \
  --prefix "$prefix" \
  --protocol /work/reference-v1/protocol.json \
  --expected-protocol-sha256 "$protocol_sha256" \
  --output /work/reference-result.json
```

Use the CPU `libcp2k.so.2026.2` path instead for DSPRHBM. Results are written
exclusively; an existing result is not overwritten. A failed numerical check
still records all completed comparisons and returns a nonzero exit status.
No candidate library has been run as part of preparing this protocol.

## Why the original default PBE comparison differs

At CP2K `cfe5e5d87aaf302953f14eb43587bef2411a8fb2`,
`src/xc/xc_pbe.F:466–485` hard-codes `beta=0.066725`,
`mu=beta*pi²/3=0.2195164512208958`, and PW92 `A1=0.031091`.
LibXC uses `beta=0.06672455060314922` (`src/gga_c_pbe.c:52–53`),
`mu=0.2195149727645171` (`src/util.h:444`), and modified PW92
`A1=0.0310907` (`maple/gga_exc/gga_c_pbe.mpl:17–19`,
`maple/lda_exc/lda_c_pw.mpl:28–30`). The PBE correlation's PW92 coefficients
are not exposed as external parameters. Changing only `_beta` and `_mu` cannot
make the two full functionals identical.

CP2K itself documents in `src/input_cp2k_xc.F:533–535`:
“Beta parameters used have only 5 significant digits, as published. For higher
precision and program comparison use the LIBXC library.” The CPU and GPU
repeat of the original default comparison is retained as an observed
parameterization difference, including its failed `1e-6 Ha` criterion; it is
not rewritten as a successful comparison by this protocol.
