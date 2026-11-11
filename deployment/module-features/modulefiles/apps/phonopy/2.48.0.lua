-- SAI module documentation. Inspection commands below are documentation only.
whatis("phonopy 2.48.0; Python 3.12.13; Features: CPU; harmonic phonons; finite displacements; spglib; Python API")
help("phonopy 2.48.0; Python 3.12.13\nFeatures: CPU; harmonic phonons; finite displacements; spglib; Python API\nFull recorded build information (readable on login nodes): cat /home/stardust/sai-hpc-software/module-info/20261111/phonopy/2.48.0/index.txt\nExact installed package versions: cat /opt/apps/conda_env/phonopy-2.48.0/share/sai/requirements.lock\nPackage inventory: cat /opt/apps/conda_env/phonopy-2.48.0/share/sai/packages.json\nPython is selected through PATH without activating Conda; unload before changing environments.")
if mode() == "help" or mode() == "whatis" then return end
family("python")
local root = "/opt/apps/conda_env/phonopy-2.48.0"
prepend_path("PATH", pathJoin(root, "bin"))
-- Save and restore user settings while preventing external Python packages
-- or a foreign standard library from overriding this environment.
pushenv("PYTHONPATH", false)
pushenv("PYTHONHOME", false)
pushenv("PYTHONNOUSERSITE", "1")
pushenv("PYTHONDONTWRITEBYTECODE", "1")
setenv("SAI_PYTHON_ENV", root)
