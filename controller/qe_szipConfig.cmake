# Ubuntu supplies libsz and its headers without a libaec CMake package.
# HDF5 supports an alternate SZIP package that exports these standard variables.
find_path(SZIP_INCLUDE_DIR NAMES szlib.h REQUIRED)
find_library(SZIP_LIBRARIES NAMES sz REQUIRED)
set(qe_szip_FOUND TRUE)
