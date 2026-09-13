"""Runtime ranks share checksums, while every rank still validates its image."""
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "controller"))
import delivery_layout as layout
from release_contract import make_identity
from source_cache import checksum


def runtime_rank(root, artifact, barrier, hashes, results):
    def counted_checksum(path):
        with hashes.get_lock():
            hashes.value += 1
        time.sleep(0.05)  # Let other ranks contend while this rank reads the SIF.
        return checksum(path)

    with patch.object(layout, "checksum", side_effect=counted_checksum):
        barrier.wait(timeout=15)
        record = layout.load_runtime_artifact(root, artifact, software="abacus", target="dsprhbm")
        results.put(record["identity"]["install_prefix"])


class RuntimeArtifactCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.identity = make_identity("abacus", "development", "develop", "a" * 40,
                                      "v1", "b" * 64, "dsprhbm")
        self.artifact = layout.artifact_path(self.root, self.identity, "build")
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b"synthetic SIF bytes, not a scientific calculation")
        self.sidecar = self.artifact.with_suffix(".json")
        self.record = dict(identity=self.identity, software="abacus", version="v1", target="dsprhbm",
                           source_sha="a" * 40, recipe_sha256="b" * 64,
                           contract_schema=layout.CONTRACT_SCHEMA, artifact=str(self.artifact),
                           sha256=checksum(self.artifact), build_verified=True)
        self.write_record(self.record)
        environment = patch.dict(os.environ, SLURM_JOB_ID="101", SLURM_JOB_START_TIME="1000",
                                 SLURM_RESTART_COUNT="0")
        environment.start()
        self.addCleanup(environment.stop)
        hostname = patch.object(layout.socket, "gethostname", return_value="node-a")
        hostname.start()
        self.addCleanup(hostname.stop)

    def write_record(self, record):
        self.sidecar.write_text(json.dumps(record))

    def runtime(self, **kwargs):
        return layout.load_runtime_artifact(self.root, self.artifact, **kwargs)

    def cache_path(self):
        paths = list((self.root / "runtime/jobs/101").glob("artifact-check-*.json"))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def test_concurrent_ranks_hash_the_image_once(self):
        context = multiprocessing.get_context("fork")
        hashes = context.Value("i", 0)
        barrier = context.Barrier(8)
        results = context.Queue()
        ranks = [context.Process(target=runtime_rank,
                                 args=(self.root, self.artifact, barrier, hashes, results))
                 for _ in range(8)]
        try:
            for rank in ranks:
                rank.start()
            for rank in ranks:
                rank.join(timeout=20)
                self.assertEqual(rank.exitcode, 0)
            self.assertEqual(hashes.value, 1)
            self.assertEqual([results.get(timeout=2) for _ in ranks],
                             [self.identity["install_prefix"]] * len(ranks))
        finally:
            for rank in ranks:
                if rank.is_alive():
                    rank.terminate()
                    rank.join(timeout=5)
            results.close()

    def test_default_loader_remains_strict_with_a_warm_runtime_cache(self):
        self.runtime()
        with patch.object(layout, "checksum", wraps=checksum) as hashes:
            for _ in range(2):
                self.assertEqual(layout.load_artifact(self.root, self.artifact), self.record)
            self.assertEqual(hashes.call_count, 2)

    def test_without_slurm_job_each_runtime_call_hashes_the_image(self):
        os.environ.pop("SLURM_JOB_ID")
        with patch.object(layout, "checksum", wraps=checksum) as hashes:
            self.runtime()
            self.runtime()
            self.assertEqual(hashes.call_count, 2)
        self.assertFalse((self.root / "runtime/jobs").exists())

    def test_runtime_cli_reuses_the_cache_and_checks_software_and_target(self):
        for software in ("abacus", "cp2k"):
            with self.subTest(software=software):
                identity = make_identity(software, "development", "develop" if software == "abacus" else "master",
                                         "a" * 40, "v1", "b" * 64, "dsprhbm")
                artifact = layout.artifact_path(self.root, identity, "cli")
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(self.artifact.read_bytes())
                record = dict(self.record, identity=identity, software=software, artifact=str(artifact))
                artifact.with_suffix(".json").write_text(json.dumps(record))
                command = [sys.executable, str(REPO / "controller/delivery_layout.py"), "runtime",
                           str(self.root), str(artifact), software, "dsprhbm"]
                for _ in range(2):
                    result = subprocess.run(command, check=True, capture_output=True, text=True)
                    self.assertEqual(result.stdout.strip(), identity["install_prefix"])
                command[-1] = "4v100-avx512"
                self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(len(list((self.root / "runtime/jobs/101").glob("artifact-check-*.json"))), 2)

    def test_job_hostname_start_and_restart_changes_do_not_reuse_receipts(self):
        with patch.object(layout, "checksum", wraps=checksum) as hashes:
            self.runtime()
            self.runtime()
            self.assertEqual(hashes.call_count, 1)
            with patch.dict(os.environ, SLURM_JOB_ID="102"):
                self.runtime()
            with patch.object(layout.socket, "gethostname", return_value="node-b"):
                self.runtime()
            with patch.dict(os.environ, SLURM_JOB_START_TIME="2000"):
                self.runtime()
            with patch.dict(os.environ, SLURM_RESTART_COUNT="1"):
                self.runtime()
            self.assertEqual(hashes.call_count, 5)

    def test_changed_image_is_rejected_even_when_size_and_mtime_are_restored(self):
        self.runtime()
        before = self.artifact.stat()
        # Filesystems may update ctime at a coarser resolution than stat's ns unit.
        time.sleep(0.01)
        self.artifact.write_bytes(b"X" * before.st_size)
        os.utime(self.artifact, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = self.artifact.stat()
        self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))
        self.assertNotEqual(after.st_ctime_ns, before.st_ctime_ns)
        with patch.object(layout, "checksum", wraps=checksum) as hashes:
            with self.assertRaisesRegex(ValueError, "immutable delivery identity"):
                self.runtime()
            self.assertEqual(hashes.call_count, 1)

    def test_replaced_image_or_new_mtime_requires_a_full_hash(self):
        with patch.object(layout, "checksum", wraps=checksum) as hashes:
            self.runtime()
            before = self.artifact.stat()
            replacement = self.artifact.with_suffix(".replacement")
            replacement.write_bytes(self.artifact.read_bytes())
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            replacement.replace(self.artifact)
            self.runtime()
            os.utime(self.artifact, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000))
            self.runtime()
            self.assertEqual(hashes.call_count, 3)

    def test_sidecar_content_and_expected_hash_changes_invalidate_cache(self):
        with patch.object(layout, "checksum", wraps=checksum) as hashes:
            self.runtime()
            cache_inode = self.cache_path().stat().st_ino
            self.write_record(dict(self.record, note="updated metadata"))
            self.runtime()
            self.assertEqual(self.cache_path().stat().st_ino, cache_inode)
            self.write_record(dict(self.record, sha256="0" * 64))
            with self.assertRaisesRegex(ValueError, "immutable delivery identity"):
                self.runtime()
            self.assertEqual(hashes.call_count, 3)

    def test_every_rank_rejects_invalid_metadata_despite_a_warm_cache(self):
        self.runtime()
        for key, value in (("identity", None), ("artifact", "/wrong.sif"),
                           ("build_verified", False), ("contract_schema", 2),
                           ("source_sha", "c" * 40), ("target", "4v100-avx512")):
            with self.subTest(field=key):
                self.write_record(dict(self.record, **{key: value}))
                with self.assertRaises(ValueError):
                    self.runtime()
        self.write_record(self.record)
        with self.assertRaises(ValueError):
            self.runtime(software="cp2k")
        with self.assertRaises(ValueError):
            self.runtime(target="4v100-avx512")

    def test_invalid_or_interrupted_cache_writes_are_rehashed(self):
        self.runtime()
        cache_path = self.cache_path()
        for broken in (b'{"schema":', b"\xff\xfe", b"{}"):
            with self.subTest(cache=broken):
                cache_path.write_bytes(broken)
                with patch.object(layout, "checksum", wraps=checksum) as hashes:
                    self.assertEqual(self.runtime(), self.record)
                    self.runtime()
                    self.assertEqual(hashes.call_count, 1)

    def test_corrupt_first_image_never_creates_a_valid_receipt(self):
        self.artifact.write_bytes(b"corrupt SIF")
        with patch.object(layout, "checksum", wraps=checksum) as hashes:
            for _ in range(2):
                with self.assertRaises(ValueError):
                    self.runtime()
            self.assertEqual(hashes.call_count, 2)
        self.assertEqual(self.cache_path().read_text(), "")

    def test_changes_during_full_hash_are_rejected(self):
        for changed_path in (self.artifact, self.sidecar):
            with self.subTest(path=changed_path):
                original = changed_path.read_bytes()

                def mutate_after_read(path):
                    digest = checksum(path)
                    changed_path.write_bytes(original + b" ")
                    return digest

                with patch.object(layout, "checksum", side_effect=mutate_after_read):
                    with self.assertRaisesRegex(ValueError, "changed during runtime validation"):
                        self.runtime()
                self.assertEqual(self.cache_path().read_text(), "")
                changed_path.write_bytes(original)

    def test_change_during_cache_hit_is_rejected_without_hashing(self):
        self.runtime()
        read_cache = json.load

        def mutate_after_cache_read(stream):
            receipt = read_cache(stream)
            self.artifact.write_bytes(b"changed during a cache hit")
            return receipt

        with patch.object(layout.json, "load", side_effect=mutate_after_cache_read), \
                patch.object(layout, "checksum", wraps=checksum) as hashes:
            with self.assertRaisesRegex(ValueError, "changed during runtime validation"):
                self.runtime()
            self.assertEqual(hashes.call_count, 0)

    def test_symlinked_artifact_sidecar_cache_and_job_directory_are_rejected(self):
        self.runtime()
        for path in (self.artifact, self.sidecar, self.cache_path(), self.root / "runtime/jobs/101"):
            with self.subTest(path=path):
                original = path.with_name(path.name + ".original")
                path.rename(original)
                path.symlink_to(original, target_is_directory=original.is_dir())
                try:
                    with self.assertRaises(ValueError):
                        self.runtime()
                finally:
                    path.unlink()
                    original.rename(path)

    def test_shared_writable_or_hardlinked_cache_files_are_rejected(self):
        self.runtime()
        cache_path = self.cache_path()
        self.assertEqual(cache_path.stat().st_mode & 0o777, 0o600)
        cache_path.chmod(0o622)
        with self.assertRaisesRegex(ValueError, "owned, private regular file"):
            self.runtime()
        cache_path.chmod(0o600)
        alias = cache_path.with_suffix(".link")
        os.link(cache_path, alias)
        with self.assertRaisesRegex(ValueError, "owned, private regular file"):
            self.runtime()

    def test_nonregular_cache_is_rejected_without_blocking(self):
        self.runtime()
        cache_path = self.cache_path()
        cache_path.unlink()
        os.mkfifo(cache_path, 0o600)
        with self.assertRaisesRegex(ValueError, "owned, private regular file"):
            self.runtime()

    def test_unsafe_job_ids_are_rejected_before_creating_runtime_directories(self):
        for job in ("../escape", "/absolute", ".", "..", "job/name"):
            with self.subTest(job=job), patch.dict(os.environ, SLURM_JOB_ID=job):
                with self.assertRaises(ValueError):
                    self.runtime()
        self.assertFalse((self.root / "runtime").exists())


if __name__ == "__main__":
    unittest.main()
