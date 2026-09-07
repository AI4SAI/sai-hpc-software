import argparse
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
