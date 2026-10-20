# Administrator handoff checks

`elpa-backtransform-20261020-preflight.txt` records the complete package check:
both final prefixes still contain the known previous inventories; both new
payloads match the manifests in `deployment/elpa-2026.02.001-backtransform/`.
No `/opt` replacement was executed.

`audit_access.py` evaluates effective POSIX ACLs for `saiuser01`, including
supplementary groups, every package path, ancestor traversal and symlink
destinations. It does not impersonate the account. The final access receipt
passes 308 paths and resolves all six links within the package. The account
has read/traverse access and no write permission; package files and directories
also have no group/other write bits.

The initial receipt is retained: preflight's Python import created a
group-writable `__pycache__` directory. The directory and its bytecode files
were restricted to 0755/0644, and the installer now sets
`PYTHONDONTWRITEBYTECODE=1`. The final receipt follows that correction.
The public ELPA module and payload inventories remain unchanged by this check.

`local-science-revalidation.json` records the separate local reparse of all 42
candidate calculations. The numerical parser and helper bytes match those
pinned by the original jobs. Regenerated samples and all single/two-node and
solver comparisons exactly match the three archived receipts. This validates
the isolated library tests; it does not authorize publication under the old
CP2K dependency lock.
