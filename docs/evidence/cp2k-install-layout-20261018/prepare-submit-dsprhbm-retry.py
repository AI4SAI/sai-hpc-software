#!/usr/bin/env python3
"""Submit one new DSPRHBM application using the unchanged sealed dependencies.

Execute on SAI only after the operator reviews the mount-timeout failure.
This script DOES submit one job when run. It does not change the timeout,
monitor, publish, update GitHub, or modify the old run/controller/checkpoint.
Any failed or interrupted attempt leaves the new directory in place and
requires manual inspection; rerunning this script will refuse to submit.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone

sys.dont_write_bytecode = True
ROOT = Path('/home/stardust/sai-hpc-software')
OLD_ID = '39078998459-1-release-dsprhbm-2026-10-17-67b5da876dd6'
NEW_ID = 'cp2k-v2026-2-dsprhbm-retry-20261018-a'
DEPS_ID = OLD_ID + '-dependencies'
OLD = ROOT / 'runs' / OLD_ID
NEW = ROOT / 'runs' / NEW_ID
DEPS = ROOT / 'runs' / DEPS_ID
CONTROL_SHA = 'c3b65b97f9d95ecb29a453713dfe292af20f6d39'
CONTROL = ROOT / 'controller' / CONTROL_SHA / OLD_ID
SOURCE_SHA = '67b5da876dd6a76b8b021d5a04d1c81ba79a4c50'
RECIPE = 'b59c9f26a1846d5a3863c04cefc0f7b746b3078bc48cb0a8138d67084d28e150'
SEAL_SHA = '91d912a1fa6787551afd3fc4861720aa6b652e530e7c269fc3c1c965f2979df1'
OVERLAY_SHA = '665b92e0bc3732acb8c8ec3d4a890bc625261cadd28a0d1da888a933843a6b2c'
PURPOSE = 'cp2k-final-prefix-dependencies-not-application-acceptance'
DRIVER_SHA = '46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10'
DRIVER = ROOT / 'cache/tools' / ('fuse2fs-' + DRIVER_SHA) / 'bin/fuse2fs'
SITE_CONFIG = Path('/opt/devtools/apptainer/1.4.4/etc/apptainer/apptainer.conf')
ARCHIVES = {
    'libwignernj-0.8.0.tar.gz': '7220cea92652040d6456aba92ff151124d9c69ce8695840490c18dd25a0da80c',
    'tblite-0.6.0.tar.xz': '372281aedb89234168d00eb691addb303197a9462a9c55d145c835f2cf5e8b42',
}
EVIDENCE = ('install-success.json', 'libint-export-repair.json', 'spack.yaml',
            'spack.lock', 'native-profile.json', 'validated-graph.json',
            'probe-success.json', 'stage-identity.json', 'image-driver.json')
IDENTITY = {
    'build_id': 'v2026.2-g67b5da876dd6-rb59c9f26a184',
    'cpu_arch': 'x86-64-v4', 'cuda_arch': '', 'dependency_isa': 'avx512', 'gpus': 0,
    'install_prefix': '/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-rb59c9f26a184/DSPRHBM',
    'partition': 'DSPRHBM', 'recipe_sha256': RECIPE, 'repository': 'cp2k/cp2k',
    'schema': 2, 'software': 'cp2k', 'source_ref': 'v2026.2', 'source_sha': SOURCE_SHA,
    'source_version': 'v2026.2', 'stack_digest': None, 'stack_sources': {},
    'target': 'dsprhbm', 'track': 'release', 'version_label': 'v2026.2',
}
# SHA256 of git c3b65b97 controller blobs; deployed "cp2k" is cp2k_runtime.sh.
CONTROL_HASHES = {
    'build_capacity.py': '088494aa992817edaafcd1e4f8a2c63ce389e11af164d6be383824555630b15e',
    'cp2k': '21e5404e8b750c5f1526905ec59fa26e84dd816d36b46148ff310e3aa0b574c0',
    'cp2k_Libint2Config.cmake': '223aac33a7743bc46a25738cb75b4a4bd1100571b1ea13fe4c19dff7c4019de9',
    'cp2k_benchmark.py': 'aefd8b234b1b43f2815a6af5f3502204319a490f4919ad417421d920034a10e3',
    'cp2k_build.sh': '11aabffbe7528ba57383414f2e4bf7d50006c9d593e30c48299e5e4ab2c61c33',
    'cp2k_cmake_prerequisites.cmake': '8c923f94b76475eba88e14280b05e37ddb8f491a03821193253e49e4af519a0d',
    'cp2k_container_entry.sh': '2f53b0af453a22fa505d28c2c595f53dc0708fca1ec943af4d13ee3c271b28f0',
    'cp2k_dependencies.sh': '99d5bbcf9dda385257c0088fe9a12a1f6eea86364e2dc8bef46a17d1af822562',
    'cp2k_dependency_stage.py': 'ac170f92f21e833c5b409074a183a5df034371576d1a79f73b3ad1b85ff6174f',
    'cp2k_feature_contract.py': '45334abffb646b152ca2fde319ef8ebe08a8b9b1bdf03927bae831c8efb1e461',
    'cp2k_image_driver.py': 'e0ea6a2b7ce25189b3eb9b7749392b0416894f9d365e1f3c31c577546a8df049',
    'cp2k_libxsmmConfig.cmake': '734aa00bd820e244cde406866c1c6065f8a2f85571dde413f7c2bf058b1fb038',
    'cp2k_overlay_workdir.py': 'f22d281427ede157c03c7fc20a8b625e9c5a511e023a6c358de93040589fe468',
    'cp2k_payload.py': '5a6c59dd438c7daf46abf351e2b27fa7bb016f7870ac5bf1ac66641cee196709',
    'cp2k_spack.py': 'fd9dc98b2e5597996cc77fcf485b6ecff8af852b61600c76054232fa0decb798',
    'cp2k_spack_dependencies.sh': '62977f63321a57a3c7d66f15e20f82cebc2ddf3987f1faa0e6719f7e6a640985',
    'cp2k_spack_environment.py': '319f1f38602c271ac4de8a3154f9e75d77230bf0ecb771b860987cd4057a8b08',
    'cp2k_spack_install_evidence.py': '391ad327602cabf1e49db806a6d82c1887669f40da097ba2dfdf3ed193d481cc',
    'cp2k_spack_libint_repair.py': 'b38fd9292b264bc83c308ca8c4ce4b42a1b2beb3367713de3f7308106b2cd014',
    'cp2k_spack_link.py': '52efab5e2c77f195e1dd380911a1d7617f2d1f632c3a808dfe5d4e61e3ab88f8',
    'cp2k_spack_native.py': 'bdfadff1c81c8cc0bf03d80f950e4dd3f3176496f02febcb9ae102cd847abb04',
    'cp2k_spack_probe.sh': '4c266c5704f1cf96f4aad3da70030fe7c4f5ea8eb9eae5bb3e409f601cf3723e',
    'cp2k_spack_seed.py': '9c41a9fc602f2f5e351eb845785a0b86882268b58b53573b3972a85b2fa35dfe',
    'create_rootfs.sh': 'ae0818e8bb485222605290fd3f3c410c40354aa518c8f613ac26236b3e382852',
    'delivery_layout.py': '268a18bfe45b1f8cca9d955d5b34215392ee74d734dcf4358c5da66728f6d7f2',
    'environment.sh': 'cd1fbc37593a421e889aba160b08c74fc31d7384e9658dfff219efdb9b40871d',
    'export_native.py': 'cf5709c9037af843e360b2ad1b73b2d50dcdc1525940db9350df5783d9e8c619',
    'module_publication.py': 'dfb74bdc51f9eeff6931c66458f80bd571b2ba587368c50ee0a752abe6f6d06f',
    'native_module.py': 'feb0065834df656ab4de54df86ffaa650ded3ed030fab4190b2d5df8cd3e4421',
    'release_contract.py': '5054afa813bece49e24841f83b33c47993ff84391859fad15a8ddd373ede3b02',
    'remote_controller.py': '40cb626f809b346a0679ae9820d65ebe2315a9cfb70532914bd31ec9f61d2ecf',
    'resolve_source.py': '2d26c1217d70cec680bbbbe81a9b6806030641edb06636262405bb7b7761fa19',
    'software_controller.py': '0ca83428f8633af7f1e2de3d88fadb3ed8251180ea08cb32b37a851cf50a53db',
    'source_cache.py': '1c6ee2a243509f0fdf74e32736bea562e2777569c4c22480b2accdb78334f52b',
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def regular(path):
    require(path.resolve() == path and not path.is_symlink(), 'redirected path: ' + str(path))
    require(stat.S_ISREG(path.stat().st_mode), 'not a regular file: ' + str(path))
    return path


def digest(path):
    # Only small metadata/controller/archive/driver files are hashed here.
    require(path.name != 'work.ext3', 'overlay hashing belongs to the frozen submit CLI')
    value = hashlib.sha256()
    with regular(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(regular(path).read_text())


def write_json(path, value):
    require(path.is_relative_to(NEW) and path.resolve() == path, 'write outside new run')
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def command(argv, env):
    return subprocess.run([str(x) for x in argv], cwd=ROOT, env=env,
                          text=True, capture_output=True, check=True)


def terminal_accounting(env):
    result = command(['sacct', '-X', '-nP', '-j', '1838161,1837486',
                      '-o', 'JobIDRaw,State,ExitCode,Partition'], env)
    records = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split('|')
        if fields[0] in ('1838161', '1837486'):
            require(fields[0] not in records, 'ambiguous accounting for ' + fields[0])
            records[fields[0]] = fields[1:4]
    require(records == {'1838161': ['FAILED', '255:0', 'DSPRHBM'],
                        '1837486': ['COMPLETED', '0:0', 'DSPRHBM']},
            'old application/dependency terminal accounting changed: ' + repr(records))
    return records


def check_request(request, run_id):
    expected = dict(run_id=run_id, software='cp2k', sha=SOURCE_SHA,
                    version='v2026.2', target='dsprhbm', track='release',
                    source_ref='v2026.2', controller=str(CONTROL),
                    recipe_sha256=RECIPE, identity=IDENTITY, contract_schema=3,
                    dependencies_run=DEPS_ID, resume_run=None,
                    overlay_mb=16384, jobs=8, minutes=240)
    require(all(request.get(k) == v for k, v in expected.items()),
            'application request differs from pinned delivery/resources')


def main():
    require(len(sys.argv) == 1, 'this single-use script takes no arguments')
    require(pwd.getpwuid(os.getuid()).pw_name == 'stardust', 'requires SAI stardust account')
    os.umask(0o077)
    for path in (ROOT, ROOT / 'runs', OLD, DEPS, CONTROL):
        require(path.is_dir() and path.resolve() == path, 'missing or redirected directory: ' + str(path))
    require(NEW.resolve() == NEW and not NEW.exists() and not NEW.is_symlink(),
            'new run already exists; inspect it manually, never resubmit automatically')
    env = {k: v for k, v in os.environ.items() if not k.startswith('PYTHON')}
    env.update(SAI_SOFTWARE_ROOT=str(ROOT), PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    cli = ['/usr/bin/python3', '-B', '-s', str(CONTROL / 'software_controller.py')]
    observed = {str(CONTROL / name): digest(CONTROL / name) for name in CONTROL_HASHES}
    require(all(observed[str(CONTROL / n)] == h for n, h in CONTROL_HASHES.items()),
            'frozen controller differs from pinned Git blobs')
    resolved = command([*cli, 'identity', 'cp2k', SOURCE_SHA, 'v2026.2', 'dsprhbm',
                        '--track', 'release', '--source-ref', 'v2026.2'], env)
    require(json.loads(resolved.stdout) == IDENTITY, 'frozen recipe/identity mismatch')
    require(regular(OLD / 'job.id').read_text().strip() == '1838161', 'old application job changed')
    require(regular(DEPS / 'job.id').read_text().strip() == '1837486', 'dependency job changed')
    check_request(read_json(OLD / 'request.json'), OLD_ID)
    dependency = read_json(DEPS / 'request.json')
    require(dependency.get('identity') == IDENTITY and dependency.get('controller') == str(CONTROL)
            and dependency.get('purpose') == PURPOSE and dependency.get('job_id') == '1837486'
            and dependency.get('overlay_mb') == 16384, 'dependency request mismatch')
    seal_path = DEPS / 'results/dependency-stage.json'
    require(digest(seal_path) == SEAL_SHA, 'dependency seal bytes changed')
    seal = read_json(seal_path)
    require(seal.get('schema') == 1 and seal.get('purpose') == PURPOSE
            and seal.get('identity') == IDENTITY and seal.get('job_id') == '1837486'
            and seal.get('overlay_sha256') == OVERLAY_SHA
            and seal.get('script_sha256') == dependency.get('script_sha256')
            and seal.get('script_sha256') == digest(DEPS / 'job.sbatch'), 'dependency seal mismatch')
    require(set(seal['evidence_sha256']) == set(EVIDENCE), 'dependency evidence inventory changed')
    for name, expected in seal['evidence_sha256'].items():
        require(digest(DEPS / 'results' / name) == expected, 'dependency evidence changed: ' + name)
    overlay = regular(DEPS / 'work.ext3').stat()
    require(not overlay.st_mode & 0o222 and overlay.st_nlink == 1
            and overlay.st_size == 16384 * 1024 * 1024, 'sealed overlay mode/link/size mismatch')
    driver_record = read_json(DEPS / 'results/image-driver.json')
    config = (regular(SITE_CONFIG).read_text() + '\nbinary path = ' + str(DRIVER.parent)
              + ':$PATH:/usr/bin:/bin:/usr/sbin:/sbin\n').encode()
    expected_driver = dict(schema=1, source_commit='45651efd6db7da8738bc9d8d1f17e99259cabfe4',
        patched_source_sha256='c63d1fb25510d316836952953e9adddacd3214cd91d6002bf3b16aed350c4ddc',
        binary_sha256=DRIVER_SHA, driver=str(DRIVER),
        apptainer='/opt/devtools/apptainer/1.4.4/bin/apptainer',
        config_sha256=hashlib.sha256(config).hexdigest())
    require(driver_record == expected_driver, 'original driver/config provenance changed')
    require(digest(DRIVER) == DRIVER_SHA and not DRIVER.stat().st_mode & 0o222
            and os.access(DRIVER, os.X_OK), 'pinned fuse2fs driver changed')
    for name, expected in ARCHIVES.items():
        require(digest(OLD / 'input/cp2k-updates' / name) == expected, 'source archive changed: ' + name)
    tracked = [OLD / n for n in ('request.json', 'job.id', 'job.sbatch')]
    tracked += [DEPS / n for n in ('request.json', 'job.id', 'job.sbatch')]
    tracked += [DEPS / 'results' / n for n in (*EVIDENCE, 'dependency-stage.json')]
    tracked += [OLD / 'input/cp2k-updates' / n for n in ARCHIVES]
    tracked += [DRIVER, SITE_CONFIG]
    observed.update({str(path): digest(path) for path in tracked})
    accounting = terminal_accounting(env)
    artifact = ROOT / 'containers/software/cp2k/release' / IDENTITY['build_id'] / 'DSPRHBM' / (NEW_ID + '.sif')
    require(artifact.resolve() == artifact and not artifact.exists() and not artifact.is_symlink()
            and not artifact.with_suffix('.json').exists() and not artifact.with_suffix('.json').is_symlink(),
            'new run already has artifact/manifest records')

    # Exclusive creation is the single-use guard, including concurrent callers.
    # Never remove this directory on any failure, including uncertain sbatch results.
    NEW.mkdir(mode=0o700)
    command([*cli, 'init', 'cp2k', NEW_ID], env)
    updates = NEW / 'input/cp2k-updates'
    updates.mkdir()
    for name, expected in ARCHIVES.items():
        source = regular(OLD / 'input/cp2k-updates' / name)
        with source.open('rb') as incoming, (updates / name).open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        require(digest(updates / name) == expected, 'copied archive mismatch: ' + name)
    submit = [*cli, 'submit', 'cp2k', NEW_ID, SOURCE_SHA, 'v2026.2', 'dsprhbm',
              '--track', 'release', '--source-ref', 'v2026.2',
              '--dependencies-run', DEPS_ID, '--overlay-mb', '16384',
              '--jobs', '8', '--minutes', '240']
    write_json(NEW / 'input/retry-plan.json', dict(schema=1, prepared_at=datetime.now(timezone.utc).isoformat(),
        purpose='one-new-application-attempt-with-original-sealed-spack-dependencies',
        original_workflow='39078998459', old_run=OLD_ID, new_run=NEW_ID,
        dependencies_run=DEPS_ID, controller=str(CONTROL), identity=IDENTITY,
        accounting=accounting, original_small_file_sha256=observed,
        sealed_overlay_sha256=OVERLAY_SHA, overlay_hash_verification='frozen-submit-then-compute-node-clone',
        archives=ARCHIVES, argv=submit, mount_timeout_changed=False,
        claims='submission only; no scientific/performance/publication acceptance or original workflow success'))
    # Recheck immediately before submitting, after new-run preparation only.
    require(terminal_accounting(env) == accounting, 'accounting changed before submission')
    require(all(digest(Path(p)) == h for p, h in observed.items()), 'original small files changed during preparation')
    for name in ('job.id', 'request.json', 'job.sbatch', 'work.ext3'):
        require(not (NEW / name).exists() and not (NEW / name).is_symlink(), 'new run has submission state: ' + name)
    write_json(NEW / 'input/submission-attempt.json', dict(started_at=datetime.now(timezone.utc).isoformat(),
        argv=submit, rule='one call only; retain run and inspect Slurm if interrupted or unsuccessful'))
    print('SUBMITTING_NEW_APPLICATION ' + json.dumps(dict(run_id=NEW_ID, controller=str(CONTROL))), flush=True)
    with (NEW / 'results/submit.stdout').open('x') as out, (NEW / 'results/submit.stderr').open('x') as err:
        process = subprocess.run(submit, cwd=ROOT, env=env, stdout=out, stderr=err, check=False)
    unchanged = all(digest(Path(p)) == h for p, h in observed.items())
    result = dict(returncode=process.returncode, old_small_files_unchanged=unchanged,
                  submitted=False, run_id=NEW_ID, inspect_directory=str(NEW))
    if process.returncode == 0:
        job = regular(NEW / 'job.id').read_text().strip()
        require(re.fullmatch(r'[0-9]+', job), 'submit returned an invalid job record; inspect, do not retry')
        check_request(read_json(NEW / 'request.json'), NEW_ID)
        result.update(submitted=True, job_id=job, script_sha256=digest(NEW / 'job.sbatch'))
    write_json(NEW / 'results/submission-result.json', result)
    print(json.dumps(result, sort_keys=True, indent=2), flush=True)
    require(process.returncode == 0 and unchanged,
            'submission or preservation check failed; retain this run and inspect records, never resubmit automatically')


if __name__ == '__main__':
    main()
