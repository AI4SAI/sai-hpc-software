"""Reject multi-node measurements that silently share GPUs or physical cores."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT/'deployment/cp2k-container-overhead'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = load('container_common', PACKAGE/'run.py')
with patch.dict(sys.modules, {'run': common}):
    multi = load('container_multinode', PACKAGE/'multinode.py')


class MultinodeOverheadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root/'ranks').mkdir()
        self.request = dict(uid=1234, prefix='/opt/apps/cp2k/accepted/16V100')
        self.inventory = {host: dict(devices=[dict(uuid=f'GPU-{host}-{i}') for i in range(4)])
                          for host in ('node-a', 'node-b')}
        self.rows = []
        for rank in range(8):
            host, local = ('node-a' if rank < 4 else 'node-b'), rank % 4
            cpus = list(range(local*4, local*4+4))
            row = dict(uid=1234, euid=1234, host=host, rank=rank, local_rank=local, size=8,
                       gpu=f'GPU-{host}-{local}', affinity=cpus,
                       topology={str(c): [0, c] for c in cpus}, cwd='/host/work', cwd_dev=4, cwd_inode=5,
                       environment={'OMP_NUM_THREADS': '4', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'},
                       binary=self.request['prefix']+'/bin/cp2k.psmp', binary_dev=7, pid=1000+rank)
            self.rows.append(row)
        self.save()

    def save(self):
        for row in self.rows:
            rank = row['rank']
            (self.root/f'ranks/inside-{rank}.json').write_text(json.dumps(row))
            host = {k: v for k, v in row.items() if k not in ('binary', 'binary_dev', 'pid')}
            (self.root/f'ranks/host-{rank}.json').write_text(json.dumps(host))

    def test_two_nodes_use_eight_distinct_cards(self):
        self.assertEqual(len(multi.check_ranks(self.root, self.request, self.inventory)), 8)

    def test_duplicate_gpu_rejected_even_with_eight_processes(self):
        self.rows[3]['gpu'] = self.rows[0]['gpu']
        self.save()
        with self.assertRaisesRegex(ValueError, 'GPU mapping'):
            multi.check_ranks(self.root, self.request, self.inventory)

    def test_overlapping_physical_cores_rejected(self):
        self.rows[3]['topology'] = self.rows[0]['topology']
        self.rows[3]['affinity'] = self.rows[0]['affinity']
        self.save()
        with self.assertRaisesRegex(ValueError, 'Overlapping physical cores'):
            multi.check_ranks(self.root, self.request, self.inventory)

    def test_container_must_use_same_host_work_directory(self):
        path = self.root/'ranks/inside-3.json'
        changed = {**self.rows[3], 'cwd_inode': 999}
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'rank context differs'):
            multi.check_ranks(self.root, self.request, self.inventory)

    def test_missing_remote_rank_rejected(self):
        (self.root/'ranks/inside-7.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing or extra MPI ranks'):
            multi.check_ranks(self.root, self.request, self.inventory)

    def test_each_sample_must_keep_the_same_binding(self):
        baseline = multi.check_ranks(self.root, self.request, self.inventory)
        actual = copy.deepcopy(baseline)
        actual['7']['affinity'] = [100, 101, 102, 103]
        with self.assertRaisesRegex(ValueError, 'placement changed'):
            multi.same_placement(actual, baseline)

    def test_separate_workload_job_requires_only_its_own_samples(self):
        samples = {f'water216-ot/{i}/{mode}': dict(elapsed_seconds=100+extra,
                    cp2k_seconds=90, max_rank_wall_seconds=99+extra)
                   for i in range(5) for mode, extra in [('native', 0), ('sif', 1)]}
        result = multi.summaries(samples, ['water216-ot'])
        self.assertEqual(set(result), {'water216-ot'})
        self.assertEqual(result['water216-ot']['elapsed_seconds']['paired_delta_median'], 1)

    def test_new_sif_mount_number_is_valid_but_physical_executable_is_not(self):
        native = multi.check_ranks(self.root, self.request, self.inventory)
        first_sif = copy.deepcopy(native)
        next_sif = copy.deepcopy(native)
        for rank in native:
            first_sif[rank]['binary_dev'] = 100+int(rank)
            next_sif[rank]['binary_dev'] = 200+int(rank)
        multi.same_placement(next_sif, first_sif)
        multi.check_binary_origin(next_sif, native, 'sif')
        with self.assertRaisesRegex(ValueError, 'wrong backing filesystem'):
            multi.check_binary_origin(native, native, 'sif')
        with self.assertRaisesRegex(ValueError, 'wrong backing filesystem'):
            multi.check_binary_origin(next_sif, native, 'native')

    def test_topology_evidence_survives_site_exit_cleanup(self):
        (self.root/'results').mkdir()
        kept = self.root/'results/nccl-topology.xml'
        temporary = self.root/'.nccl_topo_123.xml'
        kept.write_text('<system/>\n')
        temporary.write_bytes(kept.read_bytes())
        before = multi.evidence_files(self.root)
        temporary.unlink()
        self.assertEqual(before, multi.evidence_files(self.root))
        self.assertIn('results/nccl-topology.xml', before)
        self.assertNotIn(temporary.name, before)

    def test_different_topology_copy_is_rejected(self):
        (self.root/'results').mkdir()
        (self.root/'results/nccl-topology.xml').write_text('<system/>\n')
        (self.root/'.nccl_topo_123.xml').write_text('<different/>\n')
        with self.assertRaisesRegex(ValueError, 'Preserved NCCL topology differs'):
            multi.evidence_files(self.root)


if __name__ == '__main__':
    unittest.main()
