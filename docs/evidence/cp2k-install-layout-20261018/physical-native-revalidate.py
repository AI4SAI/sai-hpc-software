"""Independently read back physical native science logs with the frozen parser."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
PROJECT = Path('/home/stardust/sai-hpc-software')
RUN = PROJECT / 'runtime-tests/cp2k-2026.2-physical-20261018-193800-3592647'
PACKAGE = RUN / 'controller'
sys.path.insert(0, str(PACKAGE))
import install as deployment


def check_partition(partition):
    spec = next(row for row in json.loads((PACKAGE / 'release.json').read_text())['deliveries']
                if row['partition'] == partition)
    folder = RUN / partition
    files = {}

    def read(relative):
        path = folder / relative
        assert path.is_file() and not path.is_symlink(), path
        data = path.read_bytes()
        files[relative] = hashlib.sha256(data).hexdigest()
        return data.decode()

    result = json.loads(read('native-acceptance.json'))
    job = read('job.id').strip()
    assert result['passed'] is True and result['job'] == job
    accounting = subprocess.check_output(['sacct', '-X', '-j', job, '-n', '-P',
                                          '--format=JobID,State,ExitCode,Elapsed,NodeList,Start,End'], text=True)
    fields = next(line.split('|') for line in accounting.splitlines() if line.startswith(job + '|'))
    assert fields[1:3] == ['COMPLETED', '0:0'], fields
    slurm_log = read('slurm-' + job + '.log')
    assert 'CP2K_PHYSICAL_NATIVE_ACCEPTANCE_PASSED' in slurm_log
    allocation = dict(re.findall(r'(?:^|\s)([A-Za-z][A-Za-z0-9/]*)=([^\s]+)', read('allocation.txt')))
    assert allocation['JobId'] == job and allocation['Partition'] == partition
    assert allocation['NumNodes'] == '2' and allocation['NumTasks'] == '2'
    hosts = read('nodes.txt').splitlines()
    assert len(hosts) == len(set(hosts)) == 2
    prefix = deployment.APP_ROOT / ('cp2k-release-' + spec['build_id']) / partition
    binary = prefix / 'bin/cp2k.psmp'
    assert read('binary.txt').strip() == str(binary)
    receipt = json.loads((prefix / deployment.RECEIPT).read_text())
    assert receipt['identity'] == result['identity']
    assert receipt['source_native_manifest_sha256'] == spec['manifest_sha256']
    assert receipt['image_sha256'] == result['artifact_sha256'] == spec['image_sha256']
    artifact = (PROJECT / 'containers/software/cp2k/release' / spec['build_id'] /
                partition / (spec['run_id'] + '.json'))
    accepted = json.loads(artifact.read_text())
    proof = accepted['cp2k_science']
    control = Path(accepted['controller'])
    assert deployment.checksum(control / 'cp2k_acceptance.py') == proof['controller_sha256']
    assert deployment.checksum(control / 'cp2k_benchmark.py') == proof['helpers_sha256']
    sys.path.insert(0, str(control))
    import cp2k_acceptance as science
    evidence_path = PROJECT / 'runtime-tests' / proof['run_id'] / 'results/evidence.json'
    assert deployment.checksum(evidence_path) == proof['evidence_sha256'] == result['reference_evidence_sha256']
    evidence = json.loads(evidence_path.read_text())
    version = science.fixtures.parse_version(read('version/cp2k.log'))
    assert version == evidence['versions']['candidate'] == result['native_version']
    version_elapsed = float(read('version/elapsed.txt'))
    assert version_elapsed > 0
    modules = read('modules.txt')
    assert 'cp2k/2026.2-auto' in modules and 'openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto' in modules
    mpi = read('mpirun.txt').strip()
    assert mpi.startswith('/opt/devtools/openmpi/') and mpi.endswith('/bin/mpirun')
    mpi_version = read('mpi-version.txt')

    def ranks(relative, count):
        rows = [read(f'{relative}/ranks/rank-{rank}.tsv').strip().split('\t') for rank in range(count)]
        assert len(list((folder / relative / 'ranks').glob('rank-*.tsv'))) == count
        for rank, row in enumerate(rows):
            assert len(row) == 5 and row[:3] == [str(rank), hosts[rank], str(binary)], row
            if partition == 'DSPRHBM':
                assert row[3] == ''
            else:
                assert row[3] and row[3] != '-1' and ',' not in row[3]
            assert row[4]
        return rows

    assert ranks('version', 2) == result['version_ranks']
    loader = []
    for rank in range(2):
        ldd = read(f'version/ranks/ldd-{rank}.txt')
        assert 'not found' not in ldd
        paths = list((folder / 'version/ranks').glob(f'loader-{rank}.*'))
        assert len(paths) == 1
        trace = read(str(paths[0].relative_to(folder)))
        loaded = re.findall(r'calling init:\s*(\S+)', trace)
        hcoll = [path for path in loaded if '/hpcx/hpcx-2.25.1/hcoll/lib/libhcoll.so.1' in path]
        elpa = [path for path in loaded if path.startswith(str(prefix / 'dependencies/elpa/lib/'))
                and '/libelpa' in path and '.so.' in path]
        assert hcoll and elpa and not any('/opt/mellanox/hcoll/' in path for path in loaded)
        loader.append({'rank': rank, 'host': hosts[rank], 'hcoll': hcoll, 'elpa': elpa,
                       'missing_libraries': False, 'loaded_legacy_hcoll_alias': False})
    for name, digest in science.DATA_HASHES.items():
        assert deployment.checksum(prefix / 'share/cp2k/data' / name) == digest
    samples, comparisons, timing = {}, {}, {}
    for case, mode in science.schedule():
        key = case + '/' + mode
        relative = 'cases/' + key
        assert hashlib.sha256(read(relative + '/input.inp').encode()).hexdigest() == evidence['input_hashes'][case]['input.inp']
        sample = science.parse_science(read(relative + '/cp2k.log'), read(relative + '/forces.xyz'),
                                      read(relative + '/elapsed.txt'), case)
        assert sample == result['samples'][key]
        comparison = science.compare_science(sample, evidence['samples'][key])
        assert comparison == result['comparisons_to_sif'][key]
        assert ranks(relative, science.MODES[mode]) == result['placements'][key]
        samples[key], comparisons[key] = sample, comparison
        timing[key] = {'physical_wall_seconds': sample['elapsed_seconds'],
                       'physical_cp2k_seconds': sample['cp2k_seconds'],
                       'accepted_sif_cp2k_seconds': evidence['samples'][key]['cp2k_seconds']}
    assert len(samples) == len(result['samples']) == 14
    single_multi = {case: science.compare_science(samples[case + '/single'], samples[case + '/multi'])
                    for case in science.CASES}
    assert single_multi == result['single_multi_comparisons']
    data_records = [row for row in receipt['files'] if row['path'].startswith('share/cp2k/data/')]
    return {'partition': partition, 'job': job, 'state': fields[1], 'exit_code': fields[2],
            'elapsed': fields[3], 'node_list': fields[4], 'start': fields[5], 'end': fields[6],
            'identity': result['identity'], 'artifact_sha256': spec['image_sha256'],
            'receipt_sha256': deployment.checksum(prefix / deployment.RECEIPT),
            'manifest_sha256': spec['manifest_sha256'], 'native_version': version,
            'science_passed': len(samples), 'reference_evidence_sha256': proof['evidence_sha256'],
            'max_energy_difference_to_sif_ha': max(row['energy_delta_ha'] for row in comparisons.values()),
            'max_force_difference_to_sif_ha_bohr': max(row['max_force_delta_ha_bohr'] for row in comparisons.values()),
            'max_single_multi_energy_difference_ha': max(row['energy_delta_ha'] for row in single_multi.values()),
            'max_single_multi_force_difference_ha_bohr': max(row['max_force_delta_ha_bohr'] for row in single_multi.values()),
            'energy_tolerance_ha': science.ENERGY_TOLERANCE_HA,
            'force_tolerance_ha_bohr': science.FORCE_TOLERANCE_HA_BOHR,
            'loaded_libraries': loader, 'mpi_binary': mpi, 'mpi_version': mpi_version.strip(),
            'installed_data_files': sum(row['kind'] == 'file' for row in data_records),
            'installed_data_links': sum(row['kind'] == 'symlink' for row in data_records),
            'timings_descriptive_only': timing, 'raw_evidence_root': str(folder),
            'raw_file_sha256': files, 'independent_raw_revalidation': True}


if len(sys.argv) == 2:
    print(json.dumps(check_partition(sys.argv[1]), sort_keys=True))
else:
    records = [json.loads(subprocess.check_output([sys.executable, __file__, partition], text=True))
               for partition in ['4V100', '16V100', '8V100V0', 'DSPRHBM']]
    submission = json.loads((RUN / 'submission.json').read_text())
    for name, digest in submission['package_sha256'].items():
        assert deployment.checksum(PACKAGE / name) == digest
    assert deployment.checksum(deployment.MODULE) == submission['package_sha256']['2026.2-auto']
    result = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'passed': True,
              'scope': 'physical /opt native CP2K 2026.2, four partitions, single/two-node science',
              'execution_account': 'stardust', 'uses_apptainer': False,
              'submission': submission, 'scheduling_adjustment': json.loads((RUN / 'scheduling-adjustment.json').read_text()),
              'revalidator_sha256': deployment.checksum(Path(__file__)), 'cases_passed': sum(row['science_passed'] for row in records),
              'partitions': records, 'installed_module': str(deployment.MODULE)}
    print(json.dumps(result, indent=2, sort_keys=True))
