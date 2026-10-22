! Compile with CP2K's GCC 14.3 against the site's GCC 13.3 ELPA modules.
! Exercise the type-bound Fortran API and a real numerical solve before CP2K.
program check_elpa_consumer
  use mpi
  use elpa
  implicit none
  integer, parameter :: n = 32
  integer :: error, i
  real(kind=8) :: a(n,n), original(n,n), ev(n), z(n,n), residual, spectrum
  class(elpa_t), pointer :: solver

  call MPI_Init(error)
  if (error /= MPI_SUCCESS) stop 1
  error = elpa_init(20221109)
  if (error /= ELPA_OK) stop 2
  solver => elpa_allocate(error)
  call require_ok(error)
  call solver%set('na', n, error)
  call require_ok(error)
  call solver%set('nev', n, error)
  call require_ok(error)
  call solver%set('local_nrows', n, error)
  call require_ok(error)
  call solver%set('local_ncols', n, error)
  call require_ok(error)
  call solver%set('nblk', 16, error)
  call require_ok(error)
  call solver%set('mpi_comm_parent', MPI_COMM_WORLD, error)
  call require_ok(error)
  call solver%set('process_row', 0, error)
  call require_ok(error)
  call solver%set('process_col', 0, error)
  call require_ok(error)
  error = solver%setup()
  call require_ok(error)
  call solver%set('solver', ELPA_SOLVER_2STAGE, error)
  call require_ok(error)
  ! A tridiagonal Toeplitz matrix has an independent analytic spectrum.
  a = 0.0d0
  do i = 1, n
    a(i,i) = 2.0d0
    if (i < n) then
      a(i,i+1) = -1.0d0
      a(i+1,i) = -1.0d0
    end if
  end do
  original = a
  call solver%eigenvectors(a, ev, z, error)
  call require_ok(error)
  residual = 0.0d0
  spectrum = 0.0d0
  do i = 1, n
    spectrum = max(spectrum, abs(ev(i) - (2.0d0 - 2.0d0*cos(i*acos(-1.0d0)/(n+1)))))
    residual = max(residual, maxval(abs(matmul(original,z(:,i)) - ev(i)*z(:,i))))
  end do
  if (spectrum > 1.0d-11 .or. residual > 1.0d-11) stop 4
  call elpa_deallocate(solver, error)
  call require_ok(error)
  call elpa_uninit(error)
  call require_ok(error)
  call MPI_Finalize(error)
  if (error /= MPI_SUCCESS) stop 5
  print *, 'CP2K_SITE_ELPA_GCC14_PASS', spectrum, residual
contains
  subroutine require_ok(status)
    integer, intent(in) :: status
    if (status /= ELPA_OK) stop 3
  end subroutine
end program
