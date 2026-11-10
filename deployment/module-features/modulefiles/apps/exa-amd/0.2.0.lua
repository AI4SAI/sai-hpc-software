-- SAI module documentation. Inspection commands below are documentation only.
whatis("exa-amd 0.2.0; Python 3.12.13")
whatis("Features: VASP workflow; CGCNN model; ASE; PyTorch")
help("exa-amd 0.2.0; Python 3.12.13\nFeatures: VASP workflow; CGCNN model; ASE; PyTorch\nFull recorded build information (readable on login nodes): cat /home/stardust/sai-hpc-software/module-info/20261111/exa-amd/0.2.0/index.txt\nExact installed package versions: cat /opt/apps/conda_env/exa-amd-0.2.0/share/sai/requirements.lock\nPackage inventory: cat /opt/apps/conda_env/exa-amd-0.2.0/share/sai/packages.json\nPython is selected through PATH without activating Conda; unload before changing environments.\nVASP is an external executable; this environment does not install VASP.")
if mode() == "help" or mode() == "whatis" then return end
family("python")
local root = "/opt/apps/conda_env/exa-amd-0.2.0"
prepend_path("PATH", pathJoin(root, "bin"))
pushenv("PYTHONPATH", false)
pushenv("PYTHONHOME", false)
pushenv("PYTHONNOUSERSITE", "1")
pushenv("PYTHONDONTWRITEBYTECODE", "1")
setenv("SAI_PYTHON_ENV", root)
setenv("EXA_AMD_ROOT", root)
