#!/usr/bin/env python3
"""Compile exact CP2K source excerpts to exercise redistribution state and rank selection.

This is a control-flow reproducer, not a numerical CP2K or performance test.
MPI/BLACS handles are inert stubs; the settings, lifecycle routines, rank-count
formula and group-assignment statements come from the specified source files.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


EXPECTED = {
    'src/fm/cp_fm_diag_utils.F': 'e3edf28d199b4ba624bd94b0e3e73a8863f6db2a7ffc8e4d8e0017f32f7e8c83',
    'src/mpiwrap/message_passing.F': 'd332f39cbf43d243a3dcd3a5a0913162129e1db67946dd86e4fae2569c9dc178',
}


def routine(text, kind, name):
    pattern = rf'(?ms)^   (?:PURE )?{kind} {name}\(.*?^   END {kind} {name}$'
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        raise ValueError('Ambiguous source routine: ' + name)
    return matches[0]


def generate(source, variant):
    text = source['src/fm/cp_fm_diag_utils.F']
    declarations = text[text.index('   TYPE cp_fm_redistribute_type'):text.index('   ! Public subroutines')]
    init = routine(text, 'SUBROUTINE', 'cp_fm_redistribute_init')
    finalize = routine(text, 'SUBROUTINE', 'cp_fm_redistribute_work_finalize')
    optimal = routine(text, 'FUNCTION', 'cp_fm_diag_get_optimal_ncpu')
    reset = '      work_redistribute = cp_fm_redistribute_type()'
    if variant != 'original':
        assert init.count(reset) == 1
        init = init.replace('      ! Init work\n' + reset + '\n', '')
        init = init.replace('      work_redistribute%a = a', reset + '\n      work_redistribute%a = a')
    if variant == 'preserve_settings':
        assert finalize.count(reset) == 1
        finalize = finalize.replace('      ! Return work to its initial state\n' + reset,
            '\n'.join('      NULLIFY (work_redistribute%' + field + ')' for field in
                      ('group_distribution', 'group_partition', 'blacs_env_new', 'para_env_new')))
    split = source['src/mpiwrap/message_passing.F']
    begin = split.index('            IF (nnodes/n_subgroups > 0) THEN')
    end = split.index('         DEALLOCATE (rank_permutation)', begin) + len('         DEALLOCATE (rank_permutation)')
    selection = split[begin:end]
    # Close the enclosing n_subgroups branch before copying the remaining exact statements.
    assert selection.count('            my_subgroup_min_size = nnodes/ngroups\n         END IF') == 1
    selection = selection.replace('            my_subgroup_min_size = nnodes/ngroups\n         END IF',
                                  '            my_subgroup_min_size = nnodes/ngroups')
    return '''! CP2K excerpts: GPL-2.0-or-later; see pinned source URLs in README.md.
MODULE source_probe
   IMPLICIT NONE
   TYPE cp_blacs_env_type
      INTEGER :: unused = 0
   END TYPE
   TYPE mp_para_env_type
   CONTAINS
      PROCEDURE :: free => free_stub
   END TYPE
''' + declarations + '''
CONTAINS
   SUBROUTINE free_stub(self)
      CLASS(mp_para_env_type) :: self
   END SUBROUTINE
   SUBROUTINE cp_blacs_env_release(handle)
      TYPE(cp_blacs_env_type), POINTER :: handle
      DEALLOCATE(handle)
   END SUBROUTINE
''' + '\n'.join((init, finalize, optimal)) + '''
   SUBROUTINE assign_groups(nnodes, n_subgroups, group_partition, distribution, stride)
      INTEGER, INTENT(IN) :: nnodes, n_subgroups
      INTEGER, OPTIONAL, INTENT(IN) :: group_partition(0:), stride
      INTEGER, INTENT(OUT) :: distribution(0:)
      INTEGER :: ngroups, my_subgroup_min_size, local_stride, k, istride, irank, i, j
      INTEGER, ALLOCATABLE :: rank_permutation(:), group_distribution(:)
      ALLOCATE(group_distribution(0:nnodes-1))
''' + selection + '''
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
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--fc', required=True)
    args = parser.parse_args()
    source = {}
    for name, expected in EXPECTED.items():
        raw = (args.source / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Source differs from the pinned 2026.2 release: ' + name)
        source[name] = raw.decode()
    args.out.mkdir(parents=True, exist_ok=False)
    report = {'source': str(args.source), 'source_sha256': EXPECTED,
              'compiler': subprocess.check_output([args.fc, '--version'], text=True).splitlines()[0],
              'scope': 'source control flow only; no MPI or numerical eigensolver execution', 'runs': []}
    for variant in ('original', 'init_only', 'preserve_settings'):
        path = args.out / (variant + '.f90')
        path.write_text(generate(source, variant))
        for flag in ('-O0', '-O3'):
            binary = args.out / (variant + flag)
            argv = [args.fc, flag, '-ffree-line-length-none', '-fcheck=all', '-J', str(args.out), str(path), '-o', str(binary)]
            subprocess.run(argv, check=True, capture_output=True, text=True)
            output = subprocess.check_output([str(binary)], text=True)
            expected_counts = ((16, 8), (32, 16), (64, 32)) if variant == 'original' else ((16, 16), (32, 32), (64, 64))
            counts = [tuple(map(int, row)) for row in re.findall(r'RANKS allocated solving =\s+(\d+)\s+(\d+)', output)]
            assert counts == list(expected_counts), output
            after = int(re.search(r'AFTER_CLEANUP target =\s+(-?\d+)', output)[1])
            assert after == (560 if variant == 'preserve_settings' else -33396), output
            report['runs'].append(dict(variant=variant,flags=flag,argv=argv,output=output,
                                      source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            print(variant, flag, '\n' + output, flush=True)
    (args.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
