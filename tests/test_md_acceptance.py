"""Synthetic protocol tests, not evidence that an installed model was executed."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import md_acceptance_controller as acceptance
from md_science import BACKENDS, TOLERANCES, _record, parse_lammps_output, parse_reference
from source_cache import checksum
from test_md_science import oracle_text, dump_text


class AcceptanceRenderTests(unittest.TestCase):
    def test_three_targets_one_and_two_nodes_pin_image_and_never_publish(self):
        for target in acceptance.MD_TARGETS:
            for nodes in (1, 2):
                with self.subTest(target=target, nodes=nodes):
                    request = {'target': target, 'nodes': nodes, 'ranks': 2 * nodes,
                               'run_id': 'science-unit', 'version': 'dp-a-lmp-b',
                               'artifact': '/experimental/pinned-candidate.sif'}
                    script = acceptance.render(request, Path('/experimental/science-unit'))
                    subprocess.run(['bash', '-n'], input=script, text=True, check=True)
                    self.assertIn('#SBATCH --partition=' + acceptance.TARGETS[target]['partition'], script)
                    self.assertIn(f'#SBATCH --nodes={nodes}', script)
                    self.assertIn('#SBATCH --ntasks-per-node=2', script)
                    self.assertIn('#SBATCH --gpus-per-node=1', script)
                    self.assertIn('SAI_MD_ACCEPTANCE_RANKS=' + str(nodes * 2), script)
                    self.assertIn('SAI_MD_IMAGE=/experimental/pinned-candidate.sif', script)
                    self.assertIn('SAI_MD_VERSION=dp-a-lmp-b', script)
                    self.assertNotIn('#SBATCH --cpus-per-task', script)
                    self.assertNotIn('#SBATCH --mem', script)
                    self.assertNotIn('current.sif', script)
                    self.assertNotIn('modulefiles/', script)
                    self.assertNotRegex(script, r'(?:^|[= :])/tmp(?:/|$)')

    def test_rank_resource_mismatch_rejected(self):
        request = {'target': '4v100-avx512', 'nodes': 1, 'ranks': 2}
        for change in ({'nodes': 0}, {'nodes': 3}, {'ranks': 1}, {'nodes': 2, 'ranks': 2}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                acceptance.render(dict(request, **change), Path('/experimental/test'))

    def test_host_mpi_enters_pinned_image_and_only_task_inputs_are_copied(self):
        runner = (ROOT / 'controller/md_acceptance.sh').read_text()
        runtime = (ROOT / 'controller/md_runtime.sh').read_text()
        for path in ('md_acceptance.sh', 'md_runtime.sh'):
            subprocess.run(['bash', '-n', str(ROOT / 'controller' / path)], check=True)
        self.assertIn('"mpirun","-np"', runner)
        self.assertIn('"--map-by","ppr:2:node"', runner)
        self.assertIn('for implementation in baseline candidate', runner)
        self.assertIn('for backend in tf pt jax', runner)
        self.assertIn('"$prefix/share/sai/smoke/." /work/', runner)
        self.assertIn('SAI_MD_IMAGE:?explicit candidate or accepted immutable image required', runtime)
        self.assertIn('"$work:/work:rw"', runtime)
        self.assertIn('"$runtime:$runtime:rw"', runtime)
        self.assertIn('SAI_MD_ALLOCATED_JOB=$SLURM_JOB_ID', runtime)
        self.assertIn('SAI_MD_ALLOCATED_NODE=$(hostname)', runtime)
        self.assertIn('"$control:/control:ro"', runtime)
        self.assertIn('rank-$rank.tsv', runtime)
        self.assertNotIn('current.sif', runtime)


class AcceptanceVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_id = 'unit-science'
        self.directory = self.root / 'runtime-tests' / self.run_id
        (self.directory / 'results').mkdir(parents=True)
        (self.directory / 'case').mkdir()
        self.oracle = oracle_text()
        self.graph = 'synthetic graph data for hash validation; never executed\n'
        self.source_sha = 'a' * 40
        self.recipe_sha = 'b' * 64
        self.fixture = parse_reference(self.oracle)
        self.fixture.update(tolerances=copy.deepcopy(TOLERANCES),
                            source_reference_sha256=hashlib.sha256(self.oracle.encode()).hexdigest(),
                            source_graph_sha256=hashlib.sha256(self.graph.encode()).hexdigest(),
                            models={backend: {'input_sha256': hashlib.sha256(backend.encode()).hexdigest()}
                                    for backend in BACKENDS})
        self.request = {'run_id': self.run_id, 'version': 'dp-a-lmp-b', 'target': '4v100-avx512',
                        'nodes': 1, 'ranks': 2, 'sources': {'deepmd-kit': {'sha': self.source_sha}},
                        'acceptance_recipe_sha256': self.recipe_sha}
        artifact = self.root / 'containers/software/deepmd-lammps/dp-a-lmp-b/4v100-avx512/candidate.sif'
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b'synthetic candidate artifact')
        self.request.update(artifact=str(artifact), artifact_sha256=checksum(artifact))
        (self.directory / 'job.id').write_text('123\n')
        script = self.directory / 'job.sbatch'
        script.write_text(acceptance.render(self.request, self.directory))
        self.request['job_script_sha256'] = checksum(script)
        self.save_request()
        self.write_records()
        self.addCleanup(patch.stopall)
        patch.object(acceptance, 'ROOT', self.root).start()
        patch.object(acceptance, 'fingerprint', return_value=self.recipe_sha).start()
        patch.object(acceptance, '_load_fixture', side_effect=self.load_fixture).start()
        self.git = patch.object(acceptance.subprocess, 'check_output', side_effect=self.git_show).start()

    def save_request(self):
        (self.directory / 'request.json').write_text(json.dumps(self.request))

    def load_fixture(self, directory, backend):
        self.assertEqual(directory, self.directory / 'case')
        self.assertIn(backend, BACKENDS)
        return copy.deepcopy(self.fixture)

    def git_show(self, argv, **kwargs):
        self.assertEqual(argv[:4], ['git', '--git-dir', str(self.root / 'cache/repositories/deepmd-kit'), 'show'])
        if argv[4] == self.source_sha + ':source/lmp/tests/test_lammps.py':
            return self.oracle
        if argv[4] == self.source_sha + ':source/tests/infer/deeppot.pbtxt':
            return self.graph
        raise AssertionError('unexpected source lookup: ' + repr(argv))

    def write_records(self):
        stdout = 'SAI_ENERGY = -21\n'
        dump = dump_text(self.fixture)
        observed = parse_lammps_output(stdout, dump)
        resources = dict(nodes=self.request['nodes'], ranks=self.request['ranks'], gpus_per_node=1, omp_threads=1)
        for side in ('baseline', 'candidate'):
            for backend in BACKENDS:
                for engine in ('python', 'lammps'):
                    rows = []
                    for index in range(4):
                        row = _record(self.fixture, backend, engine, side, resources, index == 0,
                                      0.01, copy.deepcopy(observed))
                        row['slurm_job'] = '123'
                        if engine == 'lammps':
                            row['plumed'] = {'passed': True}
                            trial = self.trial(side, backend, index)
                            (trial / 'sai-ranks').mkdir(parents=True, exist_ok=True)
                            (trial / 'stdout.txt').write_text(stdout)
                            (trial / 'result.dump').write_text(dump)
                            distance = format(self.fixture['distance_angstrom'], '.17g')
                            (trial / 'COLVAR').write_text(f'#! FIELDS time sai_distance\n0 {distance}\n0.0005 {distance}\n')
                            for rank in range(self.request['ranks']):
                                executable = ('/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70/bin/lmp'
                                              if side == 'baseline' else
                                              f'/opt/software/lammps/{self.request["version"]}/{self.request["target"]}/bin/lmp')
                                mpi = '/opt/devtools/openmpi/native-' + acceptance.TARGETS[self.request['target']]['dependency_isa']
                                fields = [f'node{rank // 2}', str(rank), str(self.request['ranks']), self.request['artifact'],
                                          side, self.request['target'], executable, mpi]
                                (trial / 'sai-ranks' / f'rank-{rank}.tsv').write_text('\t'.join(fields) + '\n')
                        rows.append(row)
                    self.record_path(side, backend, engine).write_text(json.dumps(rows))

    def record_path(self, side='candidate', backend='pt', engine='python'):
        return self.directory / 'results' / f'{side}-{backend}-{engine}.json'

    def trial(self, side='candidate', backend='pt', index=0):
        return self.directory / 'results' / f'{side}-{backend}-trials' / str(index)

    def mutate_rows(self, change, **selection):
        path = self.record_path(**selection)
        rows = json.loads(path.read_text())
        change(rows)
        path.write_text(json.dumps(rows))

    def verify(self):
        return acceptance.verify(self.run_id)

    def test_complete_raw_evidence_one_and_two_nodes_not_a_publication_gate(self):
        for nodes in (1, 2):
            with self.subTest(nodes=nodes):
                self.request.update(nodes=nodes, ranks=nodes * 2)
                self.save_request()
                self.write_records()
                report = self.verify()
                self.assertTrue(report['passed'])
                self.assertEqual(set(report['complete_backends']), set(BACKENDS))
                self.assertEqual(len(report['benchmarks']), 6)
                self.assertFalse(report['performance_verified'])
                self.assertFalse(report['published'])
                self.assertEqual(report['artifact_sha256'], self.request['artifact_sha256'])
                self.assertEqual(len(report['files']), 12 + 24 * (3 + nodes * 2))
                for path, digest in report['files'].items():
                    self.assertEqual(checksum(self.directory / path), digest)
        self.assertEqual(self.git.call_count, 4)

    def test_missing_engine_or_measurement_rejected(self):
        self.mutate_rows(lambda rows: rows.pop())
        with self.assertRaises(ValueError):
            self.verify()
        self.record_path().unlink()
        with self.assertRaises(FileNotFoundError):
            self.verify()

    def test_wrong_job_and_nonnumeric_matching_job_rejected(self):
        self.mutate_rows(lambda rows: rows[0].update(slurm_job='999'))
        with self.assertRaises(ValueError):
            self.verify()
        (self.directory / 'job.id').write_text('not-a-job\n')
        for path in (self.directory / 'results').glob('*.json'):
            rows = json.loads(path.read_text())
            for row in rows:
                row['slurm_job'] = 'not-a-job'
            path.write_text(json.dumps(rows))
        with self.assertRaises(ValueError):
            self.verify()

    def test_source_oracle_graph_hash_topology_and_tolerances_rejected(self):
        original = copy.deepcopy(self.fixture)
        changes = {'source_reference_sha256': 'f' * 64, 'source_graph_sha256': 'f' * 64,
                   'atom_types': [1] * 6, 'box': [1] * 9, 'coordinates': [[0] * 3] * 6,
                   'reference': {'energy': 0, 'forces': [], 'virial': []},
                   'tolerances': {key: {'atol': 1, 'rtol': 1} for key in TOLERANCES}}
        for key, value in changes.items():
            with self.subTest(key=key):
                self.fixture = copy.deepcopy(original)
                self.fixture[key] = value
                with self.assertRaises(ValueError):
                    self.verify()
        self.fixture = original
        self.oracle += '\n# different committed reference content\n'
        with self.assertRaises(ValueError):
            self.verify()

    def test_raw_dump_and_plumed_are_checked_even_if_json_reports_pass(self):
        path = self.trial() / 'result.dump'
        original = path.read_text()
        path.write_text(original.replace('ITEM: NUMBER OF ATOMS\n6', 'ITEM: NUMBER OF ATOMS\n5'))
        with self.assertRaises(ValueError):
            self.verify()
        path.write_text(original)
        (self.trial() / 'COLVAR').write_text('#! FIELDS time sai_distance\n0 1\n0.0005 1\n')
        with self.assertRaises(ValueError):
            self.verify()

    def test_valid_but_different_raw_forces_cannot_be_hidden_by_report(self):
        path = self.trial() / 'result.dump'
        lines = path.read_text().splitlines()
        fields = lines[9].split()
        fields[1] = str(float(fields[1]) + 1)
        lines[9] = ' '.join(fields)
        path.write_text('\n'.join(lines) + '\n')
        with self.assertRaisesRegex(ValueError, 'raw LAMMPS output differs'):
            self.verify()

    def test_wrong_rank_identity_executable_dependency_or_node_distribution_rejected(self):
        path = self.trial() / 'sai-ranks/rank-0.tsv'
        original = path.read_text().strip().split('\t')
        changes = {0: 'another-node', 1: '7', 2: '4', 3: '/different/image.sif',
                   4: 'baseline', 5: '16v100-avx2', 6: '/wrong/bin/lmp', 7: '/wrong/mpi-avx2'}
        for index, value in changes.items():
            with self.subTest(field=index):
                fields = original.copy()
                fields[index] = value
                path.write_text('\t'.join(fields) + '\n')
                with self.assertRaises(ValueError):
                    self.verify()
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.verify()

    def test_changed_image_script_or_verifier_rejected(self):
        artifact = Path(self.request['artifact'])
        original = artifact.read_bytes()
        artifact.write_bytes(b'different artifact')
        with self.assertRaises(ValueError):
            self.verify()
        artifact.write_bytes(original)
        with patch.object(acceptance, 'fingerprint', return_value='c' * 64), self.assertRaises(ValueError):
            self.verify()
        (self.directory / 'job.sbatch').write_text('# changed job\n')
        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_resources_model_hash_or_relaxed_record_tolerances_rejected(self):
        path = self.record_path()
        original = path.read_text()
        changes = [{'resources': {'ranks': 99}}, {'input_sha256': 'f' * 64}, {'implementation': 'baseline'},
                   {'tolerances': {key: {'atol': 100, 'rtol': 100} for key in TOLERANCES}}]
        for change in changes:
            with self.subTest(change=change):
                path.write_text(original)
                self.mutate_rows(lambda rows: rows[0].update(change))
                with self.assertRaises(ValueError):
                    self.verify()

    def test_linked_record_raw_result_or_rank_trace_rejected(self):
        paths = [self.record_path(), self.trial() / 'result.dump', self.trial() / 'sai-ranks/rank-0.tsv']
        for index, path in enumerate(paths):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                outside = self.root / f'linked-result-{index}'
                outside.write_bytes(original)
                path.unlink()
                path.symlink_to(outside)
                with self.assertRaises(ValueError):
                    self.verify()
                path.unlink()
                path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
