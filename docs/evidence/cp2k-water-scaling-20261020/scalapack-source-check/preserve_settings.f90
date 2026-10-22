! CP2K excerpts: GPL-2.0-or-later; see pinned source URLs in README.md.
MODULE source_probe
   IMPLICIT NONE
   TYPE cp_blacs_env_type
      INTEGER :: unused = 0
   END TYPE
   TYPE mp_para_env_type
   CONTAINS
      PROCEDURE :: free => free_stub
   END TYPE
   TYPE cp_fm_redistribute_type
      ! Settings
      INTEGER                                  :: a = -1, x = -1
      LOGICAL                                  :: should_print = .FALSE.
      LOGICAL                                  :: elpa_force_redistribute = .FALSE.
      ! Temporaries
      INTEGER, DIMENSION(:), POINTER           :: group_distribution => NULL(), &
                                                  group_partition => NULL()
      TYPE(cp_blacs_env_type), POINTER         :: blacs_env_new => NULL()
      TYPE(mp_para_env_type), POINTER          :: para_env_new => NULL()
   END TYPE cp_fm_redistribute_type

   ! Permanent instance of the redistribute type
   TYPE(cp_fm_redistribute_type), PRIVATE, &
      SAVE                                     :: work_redistribute


CONTAINS
   SUBROUTINE free_stub(self)
      CLASS(mp_para_env_type) :: self
   END SUBROUTINE
   SUBROUTINE cp_blacs_env_release(handle)
      TYPE(cp_blacs_env_type), POINTER :: handle
      DEALLOCATE(handle)
   END SUBROUTINE
   SUBROUTINE cp_fm_redistribute_init(a, x, should_print, elpa_force_redistribute)
      INTEGER, INTENT(IN)                                :: a, x
      LOGICAL, INTENT(IN)                                :: should_print, elpa_force_redistribute

      work_redistribute = cp_fm_redistribute_type()
      work_redistribute%a = a
      work_redistribute%x = x
      work_redistribute%should_print = should_print
      work_redistribute%elpa_force_redistribute = elpa_force_redistribute

   END SUBROUTINE cp_fm_redistribute_init
   SUBROUTINE cp_fm_redistribute_work_finalize(has_redistributed)
      LOGICAL, INTENT(IN)                                :: has_redistributed

      IF (ASSOCIATED(work_redistribute%group_distribution)) THEN
         IF (has_redistributed) THEN
            CALL cp_blacs_env_release(work_redistribute%blacs_env_new)
         END IF
         CALL work_redistribute%para_env_new%free()
         DEALLOCATE (work_redistribute%para_env_new)
         DEALLOCATE (work_redistribute%group_distribution)
         DEALLOCATE (work_redistribute%group_partition)
      END IF
      NULLIFY (work_redistribute%group_distribution)
      NULLIFY (work_redistribute%group_partition)
      NULLIFY (work_redistribute%blacs_env_new)
      NULLIFY (work_redistribute%para_env_new)

   END SUBROUTINE cp_fm_redistribute_work_finalize
   PURE FUNCTION cp_fm_diag_get_optimal_ncpu(size) RESULT(ncpu)
      INTEGER, INTENT(IN)                                :: size
      INTEGER                                            :: ncpu

      ncpu = ((size + work_redistribute%a*work_redistribute%x - 1)/ &
              (work_redistribute%a*work_redistribute%x))*work_redistribute%a

   END FUNCTION cp_fm_diag_get_optimal_ncpu
   SUBROUTINE assign_groups(nnodes, n_subgroups, group_partition, distribution, stride)
      INTEGER, INTENT(IN) :: nnodes, n_subgroups
      INTEGER, OPTIONAL, INTENT(IN) :: group_partition(0:), stride
      INTEGER, INTENT(OUT) :: distribution(0:)
      INTEGER :: ngroups, my_subgroup_min_size, local_stride, k, istride, irank, i, j
      INTEGER, ALLOCATABLE :: rank_permutation(:), group_distribution(:)
      ALLOCATE(group_distribution(0:nnodes-1))
            IF (nnodes/n_subgroups > 0) THEN ! we have a least one cpu per group
               ngroups = n_subgroups
            ELSE ! well, only one group then
               ngroups = 1
            END IF
            my_subgroup_min_size = nnodes/ngroups

         ! rank_permutation: is a permutation of ranks, so that groups are not necessarily continuous in rank of the master group
         ! while the order is not critical (we only color ranks), it can e.g. be used to make groups that have just 1 rank per node
         ! (by setting stride equal to the number of mpi ranks per node), or by sharing  a node between two groups (stride 2).
         ALLOCATE (rank_permutation(0:nnodes - 1))
         local_stride = 1
         IF (PRESENT(stride)) local_stride = stride
         k = 0
         DO istride = 1, local_stride
            DO irank = istride - 1, nnodes - 1, local_stride
               rank_permutation(k) = irank
               k = k + 1
            END DO
         END DO

         DO i = 0, nnodes - 1
            group_distribution(rank_permutation(i)) = MIN(i/my_subgroup_min_size, ngroups - 1)
         END DO
         ! even the user gave a partition, see if we can use it to overwrite this choice
         IF (PRESENT(group_partition)) THEN
            IF (ALL(group_partition > 0) .AND. (SUM(group_partition) == nnodes) .AND. (ngroups == SIZE(group_partition))) THEN
               k = 0
               DO i = 0, SIZE(group_partition) - 1
                  DO j = 1, group_partition(i)
                     group_distribution(rank_permutation(k)) = i
                     k = k + 1
                  END DO
               END DO
            ELSE
               ! just ignore silently as we have reasonable defaults. Probably a warning would not be to bad
            END IF
         END IF
         DEALLOCATE (rank_permutation)
      distribution = group_distribution
   END SUBROUTINE
SUBROUTINE exercise()
   IMPLICIT NONE
   INTEGER :: k, ranks, target, group_partition(0:1), distribution(0:63), nsolve
   INTEGER :: i
   INTEGER, PARAMETER :: counts(3) = [16,32,64]
   k = 33396
   CALL cp_fm_redistribute_init(4,60,.TRUE.,.TRUE.)
   PRINT '(A,2I8,2L3)', 'INIT a x print elpa = ', work_redistribute%a, work_redistribute%x, &
                       work_redistribute%should_print, work_redistribute%elpa_force_redistribute
   target = cp_fm_diag_get_optimal_ncpu(k)
   PRINT '(A,I8)', 'TARGET matrix_order_33396 = ', target
   DO i=1,SIZE(counts)
      ranks = counts(i)
      IF (ranks > target) THEN
         group_partition = [target,ranks-target]
         CALL assign_groups(ranks,2,group_partition,distribution(0:ranks-1))
         nsolve = COUNT(distribution(0:ranks-1)==0)
      ELSE
         nsolve = ranks
      END IF
      PRINT '(A,2I8)', 'RANKS allocated solving = ',ranks,nsolve
   END DO

   ! Isolate the independent cleanup bug by starting with valid saved settings.
   work_redistribute%a = 4
   work_redistribute%x = 60
   work_redistribute%should_print = .TRUE.
   work_redistribute%elpa_force_redistribute = .TRUE.
   ALLOCATE(work_redistribute%group_distribution(0:1))
   ALLOCATE(work_redistribute%group_partition(0:1))
   ALLOCATE(work_redistribute%blacs_env_new, work_redistribute%para_env_new)
   CALL cp_fm_redistribute_work_finalize(.TRUE.)
   PRINT '(A,2I8,2L3)', 'CLEANUP a x print elpa = ', work_redistribute%a, work_redistribute%x, &
                       work_redistribute%should_print, work_redistribute%elpa_force_redistribute
   PRINT '(A,I8)', 'AFTER_CLEANUP target = ', cp_fm_diag_get_optimal_ncpu(k)
END SUBROUTINE
END MODULE

PROGRAM reproduce
   USE source_probe, ONLY: exercise
   CALL exercise()
END PROGRAM
