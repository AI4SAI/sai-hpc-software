# Python toolkit module consistency — November 11

The administrator handoffs for NepTrainKit 3.1.5 and GPUMDkit 1.5.9 now use the
same Python environment policy as the installed `phonopy/2.48.0`,
`sscha/1.6.2` and `exa-amd/0.2.0` modules:

- One whatis entry containing application version, Python version and Features.
- Login-readable help with concrete installation and static record paths.
- Python selected through PATH, without Conda activation.
- Membership of the `python` family; another Python environment cannot coexist.
- `pushenv` isolation of PYTHONPATH/PYTHONHOME and temporary disabling of user-site
  packages and bytecode writes; saved settings restored on unload.
- `SAI_PYTHON_ENV` identifies the selected prefix; no Python LD_LIBRARY_PATH entry.

The two new files retain Tcl module syntax, as does the existing SSCHA 1.6.0
module. Lmod applies the same family and environment semantics to Tcl and Lua.
The applications remain in their previously compiled application prefixes;
there is no relocation or rebuild of either accepted SIF/runtime.

## Verification

Both [NepTrainKit](neptrainkit.log) and [GPUMDkit](gpumdkit.log) passed the real
Lmod environment checks. Both revised administrator packages also passed
`install.sh --check` after the module hashes were updated.

Real cluster Lmod was tested against the previously accepted native trial
installations. Only the module's application prefix was substituted for the
trial prefix. Original module hashes and substituted paths are in
`inputs.json`; per-application logs include help, whatis and family rejection.

The repeatable check is `tests/check_python_module_environment.sh`. It verifies
readable static records, a single Features-bearing whatis, correct executable
and Python selection, NumPy imported from the selected installation despite a
foreign PYTHONHOME/PYTHONPATH, rejection of simultaneous phonopy loading when
auto-swap is disabled, and clean unloading. Both explicitly set values and
previously unset Python variables are covered. PATH and LD_LIBRARY_PATH are
checked before and after the load/unload cycle.

This site's Lmod normalizes saved empty variable values to unset on unload.
The installed Lua phonopy module exhibits exactly the same behavior. The check
therefore requires empty settings to remain logically empty, not preservation
of an empty-versus-unset distinction that Lmod does not provide.

Remote evidence directory:

```text
/org/org-open-sai/proj-sai-hpc-software/operations/python-module-consistency-20261111
```

This is module behavior validation, supplementing the earlier SIF and scientific
native acceptance. It is not a claim of an administrator installation into
physical `/opt`. The handoff release manifests pin the revised module hashes;
the SIF hashes and native payload manifests are unchanged.
