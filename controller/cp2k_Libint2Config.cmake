# Adapter for the exact private, receipt-verified autotools Libint2 root.
set(_root "$ENV{CP2K_LIBINT_ROOT}")
if(NOT IS_ABSOLUTE "${_root}" OR NOT IS_DIRECTORY "${_root}")
  message(FATAL_ERROR "Verified CP2K_LIBINT_ROOT is required")
endif()
find_library(_sai_int2 NAMES int2 libint2 PATHS "${_root}/lib" "${_root}/lib64" NO_DEFAULT_PATH REQUIRED)
find_path(_sai_int2_mod NAMES libint_f.mod PATHS "${_root}/include" "${_root}/include/libint2" NO_DEFAULT_PATH REQUIRED)
if(NOT TARGET Libint2::int2)
  add_library(Libint2::int2 INTERFACE IMPORTED)
  set_target_properties(Libint2::int2 PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_root}/include;${_sai_int2_mod}"
    INTERFACE_LINK_LIBRARIES "${_sai_int2};stdc++")
endif()
set(Libint2_FOUND TRUE)
