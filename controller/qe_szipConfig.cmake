# Ubuntu supplies libsz and its headers without a libaec CMake package.
# HDF5 supports an alternate SZIP package that exports these standard variables.
find_path(SZIP_INCLUDE_DIR NAMES szlib.h REQUIRED)
find_library(QE_SZIP_LIBRARY NAMES sz REQUIRED)
if(NOT TARGET qe_szip::sz)
  add_library(qe_szip::sz UNKNOWN IMPORTED)
  set_target_properties(qe_szip::sz PROPERTIES
    IMPORTED_LOCATION "${QE_SZIP_LIBRARY}"
    INTERFACE_INCLUDE_DIRECTORIES "${SZIP_INCLUDE_DIR}"
    OUTPUT_NAME sz)
endif()
set(SZIP_LIBRARIES qe_szip::sz)
set(qe_szip_FOUND TRUE)
