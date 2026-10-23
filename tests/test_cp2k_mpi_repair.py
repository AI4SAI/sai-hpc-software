"""Guard the causal source repair and real-MPI evidence requirements."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/'deployment/cp2k-water-scaling'/filename)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


water = load('mpi_test_water', 'run.py')
fix = load('mpi_test_fix', 'fix-redistribution.py')
with patch.dict(sys.modules, {'run': water}):
    retest = load('mpi_retest', 'retest-mpi.py')
    campaign = load('mpi_repair_campaign', 'campaign.py')


class MpiRepairTests(unittest.TestCase):
    def test_exact_lifecycle_change_matches_compiled_source_reproducer(self):
        evidence = ROOT/'docs/evidence/cp2k-water-scaling-20261020/scalapack-source-check'
        original = (evidence/'original.f90').read_text()
        self.assertEqual(fix.transform(original), (evidence/'preserve_settings.f90').read_text())
        with self.assertRaisesRegex(ValueError, 'Unrecognized'):
            fix.transform((evidence/'init_only.f90').read_text())

    def test_unknown_source_cannot_be_patched(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/'source.F', Path(folder)/'output.F'
            source.write_text('different CP2K revision')
            with self.assertRaisesRegex(ValueError, 'pinned CP2K'):
                fix.repair(source, output)
            self.assertFalse(output.exists())

    def test_executable_identity_alone_is_not_enough(self):
        with tempfile.TemporaryDirectory() as folder:
            prefix = Path(folder); (prefix/'lib64').mkdir(); (prefix/'bin').mkdir()
            binary = prefix/'bin/cp2k.psmp'; binary.write_bytes(b'unchanged launcher')
            library = prefix/'lib64/libcp2k.so.2026.2'; library.write_bytes(b'original math')
            before = water.cpu_library_digest(binary); executable = water.digest(binary)
            library.write_bytes(b'fixed math')
            self.assertEqual(water.digest(binary), executable)
            self.assertNotEqual(water.cpu_library_digest(binary), before)

    def traces(self, folder, mutate=None):
        for rank in range(4):
            rows = [dict(sequence=i, rank=rank, world_size=4, n=1472, grid_rows=2, grid_cols=2,
                         row=rank//2, col=rank%2, query=i%2==0, info=0, seconds=1.) for i in range(6)]
            if mutate:
                mutate(rank, rows)
            (folder/f'scalapack-rank-{rank}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))

    def test_trace_requires_repeated_complete_blacs_grids(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); self.traces(folder)
            proof = retest.trace_proof(folder, 4, 1472, 4)
            self.assertEqual(proof['solving_ranks'], [0,1,2,3])
            self.assertEqual(proof['repeated_solve_count'], 3)
            (folder/'scalapack-rank-3.jsonl').unlink()
            with self.assertRaisesRegex(ValueError, 'number of solving'):
                retest.trace_proof(folder, 4, 1472, 4)

    def test_cleanup_regression_and_failed_solve_rejected(self):
        for key,value in [('grid_cols',1),('info',7),('n',9016),('row',3),('sequence',99)]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as folder:
                folder=Path(folder)
                def change(rank, rows):
                    if rank == 0:
                        rows[-1][key] = value
                self.traces(folder, change)
                with self.assertRaises(ValueError):
                    retest.trace_proof(folder, 4, 1472, 4)

    def test_schedule_keeps_trace_out_of_timing_and_excludes_warmup(self):
        request = dict(gpus=4, trace_shape=[4,4,4], shape=[6,6,6])
        schedule = list(retest.schedule(request))
        self.assertEqual(len(schedule), 14)
        self.assertTrue(all(row[2] for row in schedule[:6]))
        self.assertTrue(all(not row[2] for row in schedule[6:]))
        self.assertEqual([row[1] for row in schedule[6:]], ['before','after','after','before','before','after','after','before'])

    def test_campaign_waits_for_causal_mpi_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name,job in [('build','1'),('retest','2'),('calibration','3')]:
                (root/name).mkdir(); (root/name/'job.id').write_text(job)
            document=dict(name='corrected',cpu={'16V100':dict(build=str(root/'build'),mpi_retests=[str(root/'retest')])},
                          points=[dict(partition='16V100',gpus=1,calibration=str(root/'calibration'))])
            path=root/'campaign.json'; water.write_json(path,document)
            for retest_state in ('RUNNING','FAILED','NODE_FAIL'):
                states={job:dict(state=status) for job,status in [('1','COMPLETED'),('2',retest_state),('3','COMPLETED')]}
                with patch.object(campaign,'scheduler',return_value=states), patch.object(campaign,'submit') as submit:
                    self.assertFalse(campaign.tick(path))
                    submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
