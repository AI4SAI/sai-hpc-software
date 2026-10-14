# Adapter for the exact private, receipt-verified LIBXSMM root.
set(_root "$ENV{CP2K_LIBXSMM_ROOT}")
if(NOT IS_ABSOLUTE "${_root}" OR NOT IS_DIRECTORY "${_root}")
  message(FATAL_ERROR "Verified CP2K_LIBXSMM_ROOT is required")
endif()
find_library(_sai_xsmm NAMES xsmm PATHS "${_root}/lib" "${_root}/lib64" NO_DEFAULT_PATH REQUIRED)
if(NOT TARGET libxsmm::libxsmm)
  add_library(libxsmm::libxsmm INTERFACE IMPORTED)
  set_target_properties(libxsmm::libxsmm PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_root}/include"
    INTERFACE_LINK_LIBRARIES "${_sai_xsmm};m;dl;pthread")
endif()
set(libxsmm_FOUND TRUE)
