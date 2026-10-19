# HDF5 1.14's static config exports ZLIB::ZLIB in its link interface without
# finding an external zlib. Match the /usr zlib provider in the Spack recipe
# before CP2K imports HDF5, preserving its full native static target graph.
find_package(ZLIB 1.3 REQUIRED)
