help([[
Apptainer 1.4.4 with a 30-second grace period for normal image-driver shutdown.
Installation: /opt/devtools/apptainer-sai/1.4.4
The original apptainer/1.4.4 installation remains available.
The native executable path must be permitted by AppArmor on each execution node.
]])
whatis("Apptainer 1.4.4: SAI image-driver shutdown fix")
conflict("apptainer")
local prefix = "/opt/devtools/apptainer-sai/1.4.4"
prepend_path("PATH", pathJoin(prefix, "bin"))
prepend_path("MANPATH", pathJoin(prefix, "share/man"))
setenv("SAI_HPC_APPTAINER", pathJoin(prefix, "bin/apptainer"))
