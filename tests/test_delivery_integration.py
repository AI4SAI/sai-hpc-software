"""Identity continuity is mandatory from the first compile prefix to runtime."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "controller"))
import delivery_layout as layout
import abacus_dependencies as dependencies
import software_controller as controller
from release_contract import make_identity
from source_cache import checksum
from remote_controller import HPCX_ROOT


class DeliveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def identity(self, software="abacus", track="development", target="4v100-avx512", version="v1"):
        return make_identity(software, track, "develop" if software == "abacus" else "master",
                             "a" * 40, version, "b" * 64, target)

    def candidate(self, identity):
        artifact = layout.artifact_path(self.root, identity, "build")
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"synthetic artifact, never submitted or exported")
        record = dict(identity=identity, software=identity["software"], version=identity["source_version"],
                      target=identity["target"], source_sha=identity["source_sha"],
                      recipe_sha256=identity["recipe_sha256"], contract_schema=layout.CONTRACT_SCHEMA,
                      artifact=str(artifact), sha256=checksum(artifact), build_verified=True)
        artifact.with_suffix(".json").write_text(json.dumps(record))
        return artifact, record

    def test_channels_and_real_partitions_are_distinct_catalogs(self):
        paths = set()
        for software in ("abacus", "cp2k"):
            for track in ("development", "prerelease", "release"):
                for target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
                    identity = self.identity(software, track, target)
                    artifact, record = self.candidate(identity)
                    self.assertEqual(artifact.parent.name, identity["partition"])
                    self.assertEqual(layout.load_artifact(self.root, artifact), record)
                    self.assertIn(f"/{software}/{track}/{identity['build_id']}/", str(artifact))
                    paths.add(artifact)
        self.assertEqual(len(paths), 24)

    def test_legacy_relabelled_and_corrupt_sidecars_fail_closed(self):
        artifact, record = self.candidate(self.identity())
        for field, value in (("identity", None), ("contract_schema", 2), ("build_verified", False),
                             ("source_sha", "c" * 40), ("version", "v2"),
                             ("recipe_sha256", "c" * 64), ("target", "16v100-avx2"),
                             ("sha256", "0" * 64), ("artifact", "/wrong.sif")):
            with self.subTest(field=field):
                artifact.with_suffix(".json").write_text(json.dumps(dict(record, **{field: value})))
                with self.assertRaises(ValueError):
                    layout.load_artifact(self.root, artifact)
        for track in ("release", "prerelease"):
            altered = dict(record, identity=self.identity(track=track))
            artifact.with_suffix(".json").write_text(json.dumps(altered))
            with self.assertRaises(ValueError):
                layout.load_artifact(self.root, artifact)

    def test_first_build_command_uses_identity_not_old_version_target_prefix(self):
        for software in ("abacus", "cp2k"):
            args = argparse.Namespace(software=software, run_id="build", sha="a" * 40,
                                      version="v1", target="4v100-avx512", track="development",
                                      source_ref="develop" if software == "abacus" else "master",
                                      jobs=8, minutes=30, overlay_mb=8192)
            with patch.object(controller, "ROOT", self.root), patch.object(controller, "recipe_fingerprint", return_value="b" * 64):
                script = controller.render_job(args)
            identity = self.identity(software)
            self.assertIn(identity["install_prefix"], script)
            self.assertIn(str(layout.artifact_path(self.root, identity, "build")), script)
            self.assertNotIn(f"/opt/software/{software}/v1/4v100-avx512", script)
            subprocess.run(["bash", "-n"], input=script, text=True, check=True)
            args.identity = self.identity(software, track="release")
            with patch.object(controller, "ROOT", self.root), patch.object(controller, "recipe_fingerprint", return_value="b" * 64):
                with self.assertRaisesRegex(ValueError, "identity"):
                    controller.render_job(args)

    def test_container_prefix_and_installed_identity_are_recomputed(self):
        identity = self.identity()
        installed = self.root / "identity.json"
        installed.write_text(json.dumps(identity))
        command = [sys.executable, str(REPO / "controller/delivery_layout.py"), "prefix", json.dumps(identity),
                   "abacus", "a" * 40, "v1", "4v100-avx512", "--installed", str(installed)]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), identity["install_prefix"])
        installed.write_text(json.dumps(self.identity(track="release")))
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        for entry in ("container_entry.sh", "cp2k_container_entry.sh"):
            script = (REPO / "controller" / entry).read_text()
            self.assertIn('delivery_layout.py prefix "$delivery"', script)
            self.assertIn('share/sai/release-identity.json', script)
            self.assertIn('umask 022', script)
        cp2k = (REPO / "controller/cp2k_container_entry.sh").read_text()
        self.assertLess(cp2k.index('cp2k_payload.py "$INSTALL_PREFIX" /workspace/export'),
                        cp2k.index('cp2k_feature_contract.py native-package'))

    def test_launcher_uses_validated_manifest_prefix_and_rejects_wrong_partition(self):
        identity = self.identity()
        artifact, _ = self.candidate(identity)
        command = [sys.executable, str(REPO / "controller/delivery_layout.py"), "runtime",
                   str(self.root), str(artifact), "abacus", "4v100-avx512"]
        self.assertEqual(subprocess.run(command, check=True, text=True, capture_output=True).stdout.strip(), identity["install_prefix"])
        command[-1] = "16v100-avx2"
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        for software in ("abacus", "cp2k"):
            launcher = (REPO / f"controller/{software}_runtime.sh").read_text()
            self.assertIn('delivery_layout.py" runtime', launcher)
            self.assertNotIn('prefix="/opt/software/', launcher)
            self.assertNotIn('image="$catalog/current.sif"', launcher)

    def test_cp2k_skylake_native_cpu_uses_site_avx2_dependencies(self):
        recipe = (REPO / "controller/cp2k_build.sh").read_text()
        self.assertIn("8v100v0-avx512)", recipe)
        self.assertIn("-march=native -mtune=native", recipe)
        self.assertIn("Gold 6146", recipe)
        self.assertIn('[[ "${OPAL_PREFIX:?}" == *-avx2 ]]', recipe)
        self.assertIn('[[ "${OPENBLAS_ROOT:?}" == *-avx2 ]]', recipe)

    def test_cp2k_build_rebuilds_parallel_hdf5_inside_delivery_prefix(self):
        recipe = (REPO / "controller/cp2k_build.sh").read_text()
        from cp2k_spack_environment import environment
        env = environment('16V100', '/opt/apps/cp2k/cp2k-development-test/16V100', 'zen3', 'ubuntu24.04')['spack']
        self.assertIn('hdf5@1.14.6 +mpi +fortran ~hl ~cxx ~shared', env['specs'])
        self.assertEqual(env['config']['install_tree']['root'],
                         '/opt/apps/cp2k/cp2k-development-test/16V100/dependencies/spack')
        self.assertIn('hdf5_root="$CP2K_HDF5_ROOT"', recipe)
        self.assertIn('check_parallel_hdf5(path.read_text())', recipe)
        self.assertLess(recipe.index('check_parallel_hdf5(path.read_text())'),
                        recipe.index('bash /control/cp2k_dependencies.sh'))
        self.assertIn('-DHDF5_ROOT="$hdf5_root" -DHDF5_PREFER_PARALLEL=ON', recipe)
        self.assertNotIn('$site', recipe)
        self.assertIn('/usr/lib/x86_64-linux-gnu/blas:/usr/lib/x86_64-linux-gnu/lapack', recipe)

    def test_cp2k_spack_gets_real_system_alternatives_and_bin_tools(self):
        args = argparse.Namespace(run_id='cp2k-binds', software='cp2k', sha='a' * 40,
                                  version='2026.2', target='16v100-avx2', jobs=8, minutes=240,
                                  overlay_mb=32768, track='development', source_ref='master')
        script = controller.render_job(args)
        build = next(line for line in script.splitlines() if 'cp2k_container_entry.sh build' in line)
        for bind in ('/etc/alternatives:/etc/alternatives:ro', '/usr/bin:/bin:ro',
                     '/var/lib/dpkg:/var/lib/dpkg:ro', HPCX_ROOT + '/hcoll:/opt/mellanox/hcoll:ro',
                     '/input/spack:ro', '/results:rw'):
            self.assertIn(bind, build)
        self.assertNotIn('/opt/apps/cp2k/cp2k-2026.1', build)

    def test_cp2k_cpu_and_gpu_builds_use_shared_hcoll_for_every_container_phase(self):
        for target in ('dsprhbm', '4v100-avx512', '16v100-avx2', '8v100v0-avx512'):
            args = argparse.Namespace(run_id='cp2k-hcoll', software='cp2k', sha='a' * 40,
                                      version='2026.2', target=target, jobs=8, minutes=240,
                                      overlay_mb=16384, track='release', source_ref='v2026.2')
            script = controller.render_job(args)
            for phase in ('build', 'export', 'verify'):
                with self.subTest(target=target, phase=phase):
                    line = next(line for line in script.splitlines()
                                if 'cp2k_container_entry.sh ' + phase in line)
                    self.assertIn(HPCX_ROOT + '/hcoll:/opt/mellanox/hcoll:ro', line)
                    self.assertNotIn('--bind /opt/mellanox/hcoll:', line)

    def test_actual_cp2k_launcher_uses_shared_hcoll_without_old_cp2k_bindings(self):
        commands = self.root / 'commands'
        commands.mkdir()
        apptainer = commands / 'apptainer'
        apptainer.write_text('#!/usr/bin/env python3\nimport json,os,sys\n'
                            'with open(os.environ["CP2K_TEST_ARGV"], "w") as f: '
                            'json.dump(sys.argv[1:], f)\n')
        apptainer.chmod(0o755)
        temporary = self.root / 'runtime/mpi'
        temporary.mkdir(parents=True)
        for target in ('dsprhbm', '16v100-avx2'):
            identity = self.identity(software='cp2k', target=target)
            artifact, _ = self.candidate(identity)
            captured = self.root / (target + '.argv.json')
            environment = dict(os.environ, PATH=f'{commands}:{os.environ["PATH"]}',
                               SAI_SOFTWARE_ROOT=str(self.root), SAI_CP2K_IMAGE=str(artifact),
                               SLURM_JOB_PARTITION=identity['partition'], TMPDIR=str(temporary),
                               CP2K_TEST_ARGV=str(captured), PYTHONDONTWRITEBYTECODE='1')
            for name in ('SAI_CP2K_TRACE_DIR', 'SLURM_JOB_ID'):
                environment.pop(name, None)
            subprocess.run(['bash', str(REPO / 'controller/cp2k_runtime.sh'), '--version'],
                           cwd=self.root, env=environment, check=True, capture_output=True, text=True)
            argv = json.loads(captured.read_text())
            binds = [argv[i + 1] for i, word in enumerate(argv) if word == '--bind']
            with self.subTest(target=target):
                self.assertIn(HPCX_ROOT + '/hcoll:/opt/mellanox/hcoll:ro', binds)
                self.assertNotIn('/opt/mellanox/hcoll:/opt/mellanox/hcoll:ro', binds)
                self.assertFalse(any('cp2k-2026.1' in bind or bind == '::ro' for bind in binds))
                self.assertEqual('--nv' in argv, target != 'dsprhbm')

    def test_build_submit_allows_long_cp2k_compile_with_safe_default(self):
        recipe = (REPO / "controller/software_controller.py").read_text()
        self.assertIn("not 1 <= args.minutes <= 360", recipe)
        self.assertIn('a.add_argument("--minutes", type=int, default=240)', recipe)

    def test_ci_installs_real_native_validation_tools_before_tests(self):
        for workflow in ("build.yml", "cp2k.yml"):
            script = (REPO / ".github/workflows" / workflow).read_text()
            self.assertIn("sudo apt-get install -y squashfs-tools tcl", script)
            self.assertLess(script.index("command -v mksquashfs unsquashfs tclsh"),
                            script.index("python3 -m unittest discover"))

    def test_resume_never_relabels_a_legacy_or_other_channel_overlay(self):
        old = self.root / "runs/old"
        old.mkdir(parents=True)
        args = argparse.Namespace(software="abacus", run_id="new", sha="a" * 40, version="v1",
                                  target="4v100-avx512", track="development", source_ref="develop",
                                  jobs=8, minutes=30, overlay_mb=8192, resume_run="old")
        for previous in ({"software": "abacus", "sha": "a" * 40, "version": "v1", "target": args.target},
                         dict(software="abacus", sha="a" * 40, version="v1", target=args.target,
                              recipe_sha256="b" * 64, contract_schema=layout.CONTRACT_SCHEMA,
                              identity=self.identity(track="release")),
                         dict(software="abacus", sha="a" * 40, version="v1", target=args.target,
                              recipe_sha256="b" * 64, contract_schema=2, identity=self.identity())):
            (old / "request.json").write_text(json.dumps(previous))
            with patch.object(controller, "ROOT", self.root), patch.object(controller, "recipe_fingerprint", return_value="b" * 64), \
                    patch.object(controller, "call") as call:
                with self.assertRaises(ValueError):
                    controller.submit(args)
            self.assertEqual([str(item.args[0][0]) for item in call.call_args_list], ["git"])
            self.assertFalse((self.root / "runs/new/job.sbatch").exists())

    def test_dependency_cache_is_verified_before_sbatch_and_failure_cannot_submit(self):
        args = argparse.Namespace(software="abacus", run_id="new", sha="a" * 40, version="v1",
                                  target="4v100-avx512", track="development", source_ref="develop",
                                  jobs=8, minutes=30, overlay_mb=8192, resume_run=None)
        for failed in (True, False):
            with self.subTest(failed=failed):
                operations = []

                def cache(path, *, source):
                    self.assertEqual(path, self.root / dependencies.ARCHIVE_CACHE)
                    self.assertEqual(source, self.root / "runs/new/input/abacus-updates")
                    operations.append("dependencies")
                    if failed:
                        raise ValueError("pinned archive mismatch")

                def call(argv, **kwargs):
                    operations.append(str(argv[0]))
                    return subprocess.CompletedProcess(argv, 0, stdout="12345\n")

                with patch.object(controller, "ROOT", self.root), \
                        patch.object(controller, "recipe_fingerprint", return_value="b" * 64), \
                        patch.object(controller, "render_job", return_value="#!/bin/bash\ntrue\n"), \
                        patch.object(controller, "call", side_effect=call), \
                        patch.object(dependencies, "cache_archives", side_effect=cache), \
                        redirect_stdout(io.StringIO()):
                    if failed:
                        with self.assertRaisesRegex(ValueError, "pinned archive mismatch"):
                            controller.submit(args)
                    else:
                        controller.submit(args)
                self.assertEqual(operations, ["git", "dependencies"] + ([] if failed else ["bash", "sbatch"]))
                self.assertEqual((self.root / "runs/new/job.id").exists(), not failed)


if __name__ == "__main__":
    unittest.main()
