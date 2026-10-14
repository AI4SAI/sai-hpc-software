# Adapter for the site's unmodified autotools Libint2 installation.
set(_root "$ENV{CP2K_SITE_DEPENDENCIES}/libint-v2.6.0-cp2k-lmax-5")
find_library(_sai_int2 NAMES int2 PATHS "${_root}/lib" NO_DEFAULT_PATH REQUIRED)
find_path(_sai_int2_mod NAMES libint_f.mod PATHS "${_root}/include" "${_root}/include/libint2" NO_DEFAULT_PATH REQUIRED)
if(NOT TARGET Libint2::int2)
  add_library(Libint2::int2 INTERFACE IMPORTED)
  set_target_properties(Libint2::int2 PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_root}/include;${_sai_int2_mod}"
    INTERFACE_LINK_LIBRARIES "${_sai_int2};stdc++")
endif()
set(Libint2_FOUND TRUE)
