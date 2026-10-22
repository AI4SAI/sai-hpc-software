#!/usr/bin/env python3
"""Freeze accepted inputs and raw reference outputs for physical native checks."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
import install as deployment


def main():
    release = deployment.load_release()
    project = deployment.PROJECT
    partitions = {}
    for delivery in release['deliveries']:
        spec, = delivery['installations']
        prefix = spec['prefix']
        record = deployment.pinned(project / delivery['publication'], delivery['publication_sha256'])
        image = project / delivery['image']
        listing = subprocess.check_output(['apptainer', 'sif', 'list', str(image)], text=True)
        matches = re.findall(r'^\s*\d+\s*\|[^\n|]*\|[^\n|]*\|\s*(\d+)-\d+\s*\|\s*FS \(Squashfs/\*System/[^)]+\)\s*$', listing, re.M)
        deployment.require(len(matches) == 1 and deployment.digest(image) == delivery['image_sha256'], 'wrong SIF')
        command = ['unsquashfs', '-no-progress', '-no-wildcards', '-o', matches[0], '-cat', str(image)]
        manifest = json.loads(subprocess.check_output(command + [prefix.lstrip('/') + '/share/sai/manifest.json']))
        inventory = {row['path']: row for row in manifest['files']}
        files, cases = {}, {}

        def pin(path, checksum=None):
            deployment.plain(path)
            sha = deployment.digest(path)
            deployment.require(checksum is None or sha == checksum, 'reference changed: ' + str(path))
            files[str(path.relative_to(project))] = sha
            return sha

        names = ['pw', 'hse', 'deepks'] + ([] if delivery['partition'] == 'DSPRHBM' else ['cusolvermp', 'nccl'])
        for name in names:
            benchmark = name in ('pw', 'hse', 'deepks')
            proof = record['benchmark_' + name if benchmark else 'gpu_features']
            task = project / 'runtime-tests' / proof['run_id']
            evidence_path = task / 'results/evidence.json'
            pin(evidence_path, proof['evidence_sha256'])
            evidence = json.loads(evidence_path.read_text())
            deployment.require(evidence['artifact_sha256'] == delivery['image_sha256'], 'reference artifact differs')
            pin(task / 'request.json')
            request = json.loads((task / 'request.json').read_text())
            source = task / ('input' if benchmark else 'cases/' + name)
            image_source = prefix.lstrip('/') + '/share/sai/' + (
                'benchmark-cases/' if benchmark else 'gpu-cases/') + name + '/'
            inputs = {}
            for key, row in inventory.items():
                if not key.startswith(image_source) or row['kind'] == 'directory':
                    continue
                deployment.require(row['kind'] == 'file', 'unexpected fixture link')
                relative = key.removeprefix(image_source)
                expected = row['sha256']
                if relative == 'INPUT' and name == 'pw' and delivery['partition'] == 'DSPRHBM':
                    original = subprocess.check_output(command + [key]).decode()
                    original = re.sub(r'^[ \t]*device[ \t]+\S+[^\n]*\n?', '', original, flags=re.M) + '\ndevice cpu\n'
                    expected = hashlib.sha256(original.encode()).hexdigest()
                sha = pin(source / relative, expected)
                inputs[relative] = dict(source=str((source / relative).relative_to(project)), sha256=sha)
            deployment.require({'INPUT', 'STRU', 'KPT'} <= set(inputs), 'incomplete case inputs')
            if benchmark:
                deployment.require({k: v['sha256'] for k, v in inputs.items()} == evidence['case_files'], 'input inventory differs')
                candidates = [r for r in evidence['runs'] if r['arm'] == 'candidate' and r['measured']]
                deployment.require(len(candidates) == 3, 'incomplete accepted candidate repetitions')
                candidate = candidates[0]
                result = task / 'runs' / candidate['id']
                for filename in ('stdout.log', request['scf_log'], 'info.log', 'wall-seconds.txt'):
                    path = result / filename
                    pin(path, evidence['files'][str(path.relative_to(task))])
                expected_energy = evidence['energy_reference']['energy_ev'] if evidence['energy_reference'] else candidate['energy_ev']
                device = evidence['case_device']
                raw_scf = result / request['scf_log']
            else:
                raw_scf = task / 'cases' / name / 'OUT.autotest/running_scf.log'
                pin(raw_scf, evidence['files'][str(raw_scf.relative_to(task))])
                raw_log = task / 'results' / name / 'abacus.log'
                pin(raw_log, evidence['files'][str(raw_log.relative_to(task))])
                candidate = dict(energy_ev=evidence['final_energy_ev'][name])
                expected_energy, device = candidate['energy_ev'], 'gpu'
            cases[name] = dict(inputs=inputs, device=device, energy_ev=expected_energy,
                               candidate_energy_ev=candidate['energy_ev'], reference_scf=str(raw_scf.relative_to(project)))
        native_source = project / spec['source']
        feature_lock = native_source / 'share/sai/dependency-lock.json'
        pin(feature_lock)
        lock = json.loads(feature_lock.read_text())
        labels = lock['required_info'] + ['JSON Support'] + ([] if delivery['partition'] == 'DSPRHBM' else lock['required_gpu_info'])
        partitions[delivery['partition']] = dict(
            build_run=delivery['build_run'], image_sha256=delivery['image_sha256'],
            publication_sha256=delivery['publication_sha256'], tolerance_ev=1e-5,
            cases=cases, files=files, required_info=labels)
        print(delivery['partition'] + ': pinned ' + str(len(cases)) + ' cases against the immutable SIF')
    with (deployment.PACKAGE / 'native-reference.json').open('x') as stream:
        json.dump(dict(schema=1, release_sha256=deployment.digest(deployment.PACKAGE / 'release.json'),
                       partitions=partitions), stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


if __name__ == '__main__':
    main()
