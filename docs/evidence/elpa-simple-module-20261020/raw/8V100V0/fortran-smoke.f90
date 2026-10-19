program check_fortran_module
  use mpi
  use elpa
  implicit none
  integer :: error
  call MPI_Init(error)
  error = elpa_init(20221109)
  if (error /= ELPA_OK) stop 1
  call elpa_uninit(error)
  if (error /= ELPA_OK) stop 2
  call MPI_Finalize(error)
  print *, 'ELPA_FORTRAN_CONSUMER_PASS'
end program
