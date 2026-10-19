"""Reject ineffective direct flags and incorrect resource/science measurements."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cp2k_tuning', ROOT/'deployment/cp2k-diagonalization/run.py')
tuning = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tuning)


class TuningTests(unittest.TestCase):
    def fixture(self, folder, timer):
        (folder/'cp2k.log').write_text('PROGRAM ENDED AT today\n SCF run converged in 10 steps\n'
            'ENERGY| Total FORCE_EVAL QS energy [hartree] -100\n'
            ' CP2K 1 1.0 0.1 0.1 20.0 20.0\n'+timer)
        rows = [f'{i+1} 1 {atom} 0.0 0.0 0.0' for i,atom in enumerate(['O','H','H']*64)]
        (folder/'forces.xyz').write_text('ATOMIC FORCES in [a.u.]\n'+'\n'.join(rows)+'\nSUM OF ATOMIC FORCES\n')
        (folder/'elapsed.txt').write_text('21.0')

    def test_direct_requires_actual_generalized_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            standard = ' eigensolver 10 1.0 0.1 0.1 1.0 1.0\n cp_fm_diag_cusolver 10 1.0 0.1 0.1 1.0 1.0\n'
            direct = ' eigensolver_generalized 10 1.0 0.1 0.1 1.0 1.0\n cp_fm_general_cusolver 10 1.0 0.1 0.1 1.0 1.0\n'
            self.fixture(folder, standard)
            tuning.parse_sample(folder, 'cusolver-standard', 4)
            with self.assertRaisesRegex(ValueError, 'not executed'):
                tuning.parse_sample(folder, 'cusolver-direct', 4)
            self.fixture(folder, direct)
            tuning.parse_sample(folder, 'cusolver-direct', 4)
            with self.assertRaisesRegex(ValueError, 'Unexpected direct'):
                tuning.parse_sample(folder, 'cusolver-standard', 4)
            (folder/'forces.xyz').write_text('missing forces')
            with self.assertRaises(ValueError):
                tuning.parse_sample(folder, 'cusolver-direct', 4)

    def test_standard_and_direct_change_only_keyword(self):
        for edge in (4,6):
            a = tuning.input_text('cusolver-standard', edge)
            b = tuning.input_text('cusolver-direct', edge)
            self.assertEqual(a.replace('DIRECT_GENERALIZED_DIAGONALIZATION F',
                                       'DIRECT_GENERALIZED_DIAGONALIZATION T'), b)
            self.assertIn('CHOLESKY RESTORE', b)
            self.assertNotIn('&OT ON', b)
            self.assertIn('&OT ON', tuning.input_text('ot', edge))

    def test_affinity_counts_physical_cores_not_logical_threads(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            rank = dict(rank=0,ranks=1,image='/accepted.sif',gpu='0',omp_threads='2',affinity=[0,1,2,3])
            (folder/'rank.json').write_text(json.dumps(rank))
            (folder/'topology.txt').write_text('# CPU,Core,Socket\n0,0,0\n1,0,0\n2,1,0\n3,1,0\n')
            request = dict(artifact='/accepted.sif',omp_threads=2)
            tuning.check_rank(folder, request)
            rank['affinity']=[0,1]
            (folder/'rank.json').write_text(json.dumps(rank))
            with self.assertRaisesRegex(ValueError, 'physical-core'):
                tuning.check_rank(folder, request)


if __name__ == '__main__':
    unittest.main()
