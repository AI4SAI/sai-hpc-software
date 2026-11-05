# QE's Makefile pwall suite includes these tools; its 7.6 CMake build omits
# two target declarations. Add build wiring without changing scientific source.
# HDF5's static exported target refers to ZLIB::ZLIB without finding it.
find_package(ZLIB REQUIRED)
include("${CMAKE_CURRENT_LIST_DIR}/qe_szipConfig.cmake")

function(sai_qe_complete_tools)
  foreach(tool IN ITEMS band_interpolation rism1d)
    if(tool STREQUAL "band_interpolation")
      set(sources "${qe_SOURCE_DIR}/PP/src/${tool}.f90")
      foreach(module IN ITEMS globalmod fouriermod idwmod)
        list(APPEND sources "${qe_SOURCE_DIR}/PP/src/${module}.f90")
      endforeach()
      set(libraries qe_pw qe_modules qe_pp qe_upflib)
    else()
      set(sources "${qe_SOURCE_DIR}/PW/tools/${tool}.f90")
      set(libraries qe_pw qe_modules qe_fftx qe_lax qe_upflib qe_xclib)
    endif()
    # Refuse silent source-layout changes in a future develop revision.
    foreach(source IN LISTS sources)
      if(NOT EXISTS "${source}")
        message(FATAL_ERROR "Required site QE tool source is missing: ${source}")
      endif()
    endforeach()
    qe_enable_cuda_fortran("${sources}")
    qe_add_executable(sai_qe_${tool} ${sources})
    set_target_properties(sai_qe_${tool} PROPERTIES OUTPUT_NAME "${tool}.x")
    target_link_libraries(sai_qe_${tool} PRIVATE ${libraries})
  endforeach()
  add_custom_target(sai_qe_tools DEPENDS sai_qe_band_interpolation sai_qe_rism1d)
endfunction()

cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL sai_qe_complete_tools)
