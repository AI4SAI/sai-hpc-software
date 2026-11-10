# Module feature description validation, 2026-11-10

All six application descriptions were evaluated by the cluster login node's
actual Lmod with `module --ignore_cache whatis`. The files were staged under
`/org/org-open-sai/proj-sai-hpc-software/operations/module-features-20261110-r1/preview/modulefiles`.
`whatis-preview.log` is candidate output, not a claim that public beta generations
were replaced. No Slurm allocation or application execution was needed.

`preflight.log` confirms the four release candidates differ from the actual
`/opt` modules only in `module-whatis` lines. The administrator updater ran in
read-only `--check` mode. No physical `/opt` module or application was changed.

Local validation passed 133 existing tests from `test_sif_modules`, `test_sif_beta`,
`test_native_module`, `test_module_publication`, `test_lammps_deployment`,
`test_qe_deployment`, `test_abacus_beta12_deployment`, `test_cp2k_site_deploy` and
`test_daily_beta`. The tests include Tcl discovery without an allocation,
partition selection, fixed-generation unload, publication integrity and native
release metadata. No scientific rebuild or benchmark was needed for these text
changes.
