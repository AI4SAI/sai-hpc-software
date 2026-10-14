# Adapter for the site's unmodified LIBXSMM pkg-config package.
set(_root "$ENV{CP2K_SITE_DEPENDENCIES}/libxsmm-e0c4a2389afba36c453233ad7de07bd92c715bec")
find_library(_sai_xsmm NAMES xsmm PATHS "${_root}/lib" NO_DEFAULT_PATH REQUIRED)
if(NOT TARGET libxsmm::libxsmm)
  add_library(libxsmm::libxsmm INTERFACE IMPORTED)
  set_target_properties(libxsmm::libxsmm PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_root}/include"
    INTERFACE_LINK_LIBRARIES "${_sai_xsmm};m;dl;pthread")
endif()
set(libxsmm_FOUND TRUE)
