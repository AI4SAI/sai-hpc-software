-- SAI module documentation. Inspection commands below are documentation only.
whatis("sscha 1.6.2; Python 3.12.13")
whatis("Features: CPU/MPI; stochastic anharmonic phonons; CellConstructor; ASE; spglib")
help("sscha 1.6.2; Python 3.12.13\nFeatures: CPU/MPI; stochastic anharmonic phonons; CellConstructor; ASE; spglib\nExact installed package versions: cat /opt/apps/conda_env/sscha-1.6.2/share/sai/requirements.lock\nPackage inventory: cat /opt/apps/conda_env/sscha-1.6.2/share/sai/packages.json\nSelected Python: python -c 'import sys; print(sys.executable)'\nPython is selected through PATH without activating Conda; unload before changing environments.")
if mode() == "help" or mode() == "whatis" then return end
family("python")
depends_on("openmpi/5.0.8-cuda12.4-classic-auto")
prepend_path("LD_LIBRARY_PATH", "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/sharp/lib")
prepend_path("LD_LIBRARY_PATH", "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1/hcoll/lib")
local root = "/opt/apps/conda_env/sscha-1.6.2"
prepend_path("PATH", pathJoin(root, "bin"))
pushenv("PYTHONPATH", false)
pushenv("PYTHONHOME", false)
pushenv("PYTHONNOUSERSITE", "1")
pushenv("PYTHONDONTWRITEBYTECODE", "1")
setenv("SAI_PYTHON_ENV", root)
setenv("SSCHA_ROOT", root)
setenv("SSCHA_VERSION", "1.6.2")
