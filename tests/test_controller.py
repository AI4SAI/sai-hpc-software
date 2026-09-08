import argparse
from contextlib import redirect_stdout
import io
import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import remote_controller as policy
import software_controller as controller
import runtime_controller as runtime
import source_cache as cache

class PolicyTests(unittest.TestCase):
    def test_no_writable_host_binds_or_whole_opt(self):
        argv = policy.container_command("/home/u/base.sif", ["/bin/bash"],
                                        overlay="/home/u/work.ext3", control="/home/u/control",
                                        repository="/home/u/cache")
        binds = [argv[i + 1] for i, x in enumerate(argv) if x == "--bind"]
        self.assertTrue(all(x.endswith(":ro") for x in binds))
        self.assertIn("/opt/devtools:/opt/devtools:ro", binds)
        self.assertFalse(any(x.startswith("/opt:") for x in binds))
        self.assertIn("none", argv)
        self.assertNotIn("--writable", argv)
        self.assertNotIn("--writable-tmpfs", argv)

    def test_identifiers(self):
        for value in (".", "..", "../x", "a/b", "x\n#SBATCH", "x;touch", "-evil", ""):
            with self.assertRaises(ValueError):
                policy.safe_name(value)

    def test_precise_v100_profiles(self):
        self.assertEqual(policy.TARGETS["4v100-avx512"]["partition"], "4V100")
        self.assertEqual(policy.TARGETS["4v100-avx512"]["cpu_arch"], "znver4")
        self.assertEqual(policy.TARGETS["16v100-avx2"]["partition"], "16V100")
        self.assertEqual(policy.TARGETS["16v100-avx2"]["cpu_arch"], "znver3")
        self.assertEqual(policy.TARGETS["16v100-avx2"]["qos"], "flood-1o2gpu")
        self.assertEqual(policy.TARGETS["a100"]["qos"], "rush-1o2gpu")
        self.assertNotIn("v100", policy.TARGETS)

    def test_job_is_single_file_build(self):
        args = argparse.Namespace(software="abacus", run_id="test-1", sha="a" * 40,
                                  version="develop-aaaa", target="cpu-misc",
                                  jobs=8, minutes=60, overlay_mb=8192)
        with patch.object(controller, "ROOT", Path("/home/test/sai-hpc-software")):
            script = controller.render_job(args)
        self.assertNotIn("--sandbox", script)
        self.assertNotIn("rm -rf", script)
        self.assertNotRegex(script, r"(?:^|[= :])/tmp(?:/|$)")
        self.assertNotIn("--gpus-per-node=0", script)
        self.assertIn("--fakeroot --sparse", script)
        self.assertIn("--cpus-per-task=8", script)
        self.assertIn("container_entry.sh verify", script)
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)
        args.target = "16v100-avx2"
        with patch.object(controller, "ROOT", Path("/home/test/sai-hpc-software")):
            gpu_script = controller.render_job(args)
        self.assertNotIn("#SBATCH --cpus-per-task", gpu_script)
        self.assertNotIn("#SBATCH --mem", gpu_script)
        self.assertIn("#SBATCH --gpus-per-node=1", gpu_script)
        args.resume_run = "old-run"
        with patch.object(controller, "ROOT", Path("/home/test/sai-hpc-software")):
            repack = controller.render_job(args)
        self.assertNotIn("overlay create", repack)
        self.assertNotIn("container_entry.sh build", repack)
        self.assertIn("container_entry.sh metadata", repack)

    def test_multinode_runtime_uses_host_mpi_and_runtime_network(self):
        args = argparse.Namespace(run_id="runtime-test", version="develop-aaaa",
                                  target="16v100-avx2", nodes=2,
                                  gpus_per_node=1, minutes=30)
        with patch.object(runtime, "ROOT", Path("/home/test/sai-hpc-software")):
            script = runtime.render_job(args)
        self.assertIn("#SBATCH --partition=16V100", script)
        self.assertIn("#SBATCH --nodes=2", script)
        self.assertIn("#SBATCH --ntasks=2", script)
        self.assertIn("export USER=${SLURM_JOB_USER:?}\nexport LOGNAME=$USER", script)
        self.assertIn('export LD_LIBRARY_PATH="" LD_PRELOAD=""', script)
        self.assertIn("/mpi-runtime", script)
        self.assertLess(script.index("command -v apptainer >/dev/null"),
                        script.index("apptainer exec --cleanenv"))
        self.assertIn("mpirun -np 2", script)
        self.assertIn('--map-by "$MAP_OPT" --report-bindings "$launcher"', script)
        self.assertIn("MULTINODE_CONTAINER_MPI_VERIFIED", script)
        self.assertIn('export SAI_ABACUS_IMAGE="$image"', script)
        self.assertIn("$6 !~ /-avx2$/", script)
        self.assertNotIn("--network none", script)
        self.assertNotIn("--containall", script)
        self.assertNotRegex(script, r"--bind /opt:/opt")
        self.assertNotRegex(script, r"(?:^|[= :])/tmp(?:/|$)")
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)

        args.nodes = 3
        with patch.object(runtime, "ROOT", Path("/home/test/sai-hpc-software")):
            with self.assertRaises(ValueError):
                runtime.render_job(args)

        launcher = (ROOT / "controller/abacus_runtime.sh").read_text()
        self.assertIn("apptainer exec", launcher)
        self.assertIn("--nv", launcher)
        self.assertIn('-f "$image"', launcher)
        self.assertIn('--bind "$host_tmp:$host_tmp:rw"', launcher)
        self.assertIn('TMPDIR=$host_tmp', launcher)
        self.assertIn('rmdir -- "$job_runtime"', launcher)
        self.assertNotIn("--network none", launcher)
        self.assertNotIn("--containall", launcher)
        self.assertNotRegex(launcher, r"--bind [\"']?/opt:/opt")
        self.assertIn("4V100) target=4v100-avx512", launcher)
        self.assertIn("16V100) target=16v100-avx2", launcher)

    def test_runtime_pins_the_requesting_build_artifact(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            root = Path(directory)
            build_run = "build-run"
            artifact = root / "containers/software/abacus/v1/16v100-avx2/build-run.sif"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"sif")
            sidecar = artifact.with_suffix(".json")
            sidecar.write_text(json.dumps({
                "verified": True,
                "artifact": str(artifact),
                "version": "v1",
                "target": "16v100-avx2",
                "sha256": cache.checksum(artifact),
            }))
            build_task = root / "runs" / build_run
            build_task.mkdir(parents=True)
            (build_task / "artifact.path").write_text(str(artifact) + "\n")
            with patch.object(runtime, "ROOT", root):
                self.assertEqual(
                    runtime.build_artifact(build_run, "v1", "16v100-avx2"), artifact)
                (build_task / "artifact.path").write_text(
                    str(artifact.parent / "another-run.sif") + "\n")
                with self.assertRaises(ValueError):
                    runtime.build_artifact(build_run, "v1", "16v100-avx2")

    def test_publish_creates_current_image_and_module(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            root = Path(directory)
            control = root / "controller/reviewed/run"
            control.mkdir(parents=True)
            launcher = control / "abacus"
            launcher.write_text("#!/bin/bash\n")
            target = root / "containers/software/abacus/v1/4v100-avx512"
            target.mkdir(parents=True)
            artifact = target / "run.sif"
            artifact.write_bytes(b"sif")
            request = {"controller": str(control), "version": "v1"}
            manifest = {}
            with patch.object(controller, "ROOT", root):
                controller.publish_runtime_entry(request, artifact, manifest)
            self.assertEqual((target / "current.sif").resolve(), artifact)
            module = root / "modulefiles/apps/abacus/v1"
            self.assertIn(str(control), module.read_text())
            self.assertEqual(manifest["runtime_launcher_sha256"], cache.checksum(launcher))

    def test_catalog_rejects_changed_artifact(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            root = Path(directory)
            folder = root / "containers/software/abacus/v1/cpu-misc"
            folder.mkdir(parents=True)
            image = folder / "run.sif"
            image.write_bytes(b"test image data")
            data = {"verified": True, "source_sha": "a" * 40, "target": "cpu-misc",
                    "artifact": str(image), "sha256": cache.checksum(image)}
            image.with_suffix(".json").write_text(json.dumps(data))
            args = argparse.Namespace(software="abacus", version="v1", target="cpu-misc", sha="a" * 40)
            first = io.StringIO()
            with patch.object(controller, "ROOT", root), redirect_stdout(first):
                controller.lookup(args)
            self.assertEqual(json.loads(first.getvalue())["artifact"], str(image))
            image.write_bytes(b"changed")
            second = io.StringIO()
            with patch.object(controller, "ROOT", root), redirect_stdout(second):
                controller.lookup(args)
            self.assertEqual(json.loads(second.getvalue()), {})

class CacheTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "source"
        self.repo.mkdir()
        cache.git(self.repo, "init")
        cache.git(self.repo, "config", "user.email", "test@example.invalid")
        cache.git(self.repo, "config", "user.name", "Test")
        self.first = self.commit("first")

    def commit(self, value):
        (self.repo / "code").write_text(value)
        cache.git(self.repo, "add", "code")
        cache.git(self.repo, "commit", "-m", value)
        return cache.git(self.repo, "rev-parse", "HEAD")

    def test_full_then_incremental_preserves_sha(self):
        store = self.root / "cache.git"
        m = cache.pack(self.repo, self.first, self.root / "full")
        self.assertEqual(len(m["parts"]), 8)
        self.assertEqual(cache.receive(store, self.root / "full"), self.first)
        second = self.commit("next")
        delta = cache.pack(self.repo, second, self.root / "delta", self.first)
        self.assertEqual(delta["base"], self.first)
        self.assertEqual(cache.receive(store, self.root / "delta"), second)
        self.assertEqual(cache.git(store, "show", second + ":code"), "next")
        self.assertTrue(cache.complete(store, second))
        self.assertIn(second, cache.inventory(store)["cache_shas"])
        self.assertFalse((store / "code").exists())

    def test_corruption_and_path_injection(self):
        import json
        target = self.root / "pack"
        cache.pack(self.repo, self.first, target)
        (target / "source.part.00").write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            cache.assemble(target)
        other = self.root / "other"
        m = cache.pack(self.repo, self.first, other)
        m["parts"][0]["name"] = "../secret"
        (other / "manifest.json").write_text(json.dumps(m))
        with self.assertRaises(ValueError):
            cache.assemble(other)

    def test_missing_delta_base_rejected(self):
        second = self.commit("second")
        cache.pack(self.repo, second, self.root / "delta", self.first)
        with self.assertRaises(subprocess.CalledProcessError):
            cache.receive(self.root / "empty.git", self.root / "delta")

if __name__ == "__main__":
    unittest.main()
