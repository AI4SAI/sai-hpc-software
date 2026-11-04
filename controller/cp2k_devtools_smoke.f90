program shared_hdf5_consumer
  use mpi
  use hdf5
  use libint_f
  implicit none
  integer :: ierr, rank, nproc
  integer(hid_t) :: file, access, space, dataset, memory
  integer(hsize_t) :: shape(1), count(1), offset(1)
  integer :: actual(1), expected(1)
  call MPI_Init(ierr)
  call MPI_Comm_rank(MPI_COMM_WORLD, rank, ierr)
  call MPI_Comm_size(MPI_COMM_WORLD, nproc, ierr)
  shape = nproc
  count = 1
  offset = rank
  expected = 17 + rank
  call h5open_f(ierr)
  call check(ierr)
  call h5pcreate_f(H5P_FILE_ACCESS_F, access, ierr)
  call check(ierr)
  call h5pset_fapl_mpio_f(access, MPI_COMM_WORLD, MPI_INFO_NULL, ierr)
  call check(ierr)
  call h5fcreate_f('roundtrip.h5', H5F_ACC_TRUNC_F, file, ierr, access_prp=access)
  call check(ierr)
  call h5screate_simple_f(1, shape, space, ierr)
  call check(ierr)
  call h5dcreate_f(file, 'values', H5T_NATIVE_INTEGER, space, dataset, ierr)
  call check(ierr)
  call h5sselect_hyperslab_f(space, H5S_SELECT_SET_F, offset, count, ierr)
  call check(ierr)
  call h5screate_simple_f(1, count, memory, ierr)
  call check(ierr)
  call h5dwrite_f(dataset, H5T_NATIVE_INTEGER, expected, count, ierr, &
                 mem_space_id=memory, file_space_id=space)
  call check(ierr)
  call h5fflush_f(file, H5F_SCOPE_GLOBAL_F, ierr)
  call check(ierr)
  call MPI_Barrier(MPI_COMM_WORLD, ierr)
  actual = -1
  call h5dread_f(dataset, H5T_NATIVE_INTEGER, actual, count, ierr, &
                mem_space_id=memory, file_space_id=space)
  call check(ierr)
  if (any(actual /= expected)) call MPI_Abort(MPI_COMM_WORLD, 2, ierr)
  call h5dclose_f(dataset, ierr)
  call check(ierr)
  call h5sclose_f(memory, ierr)
  call h5sclose_f(space, ierr)
  call h5fclose_f(file, ierr)
  call check(ierr)
  call h5pclose_f(access, ierr)
  call h5close_f(ierr)
  call check(ierr)
  if (rank == 0) print *, 'SAI_HDF5_MPI_ROUNDTRIP_PASS'
  call MPI_Finalize(ierr)
contains
  subroutine check(status)
    integer, intent(in) :: status
    integer :: abort_error
    if (status /= 0) call MPI_Abort(MPI_COMM_WORLD, 1, abort_error)
  end subroutine check
end program shared_hdf5_consumer
