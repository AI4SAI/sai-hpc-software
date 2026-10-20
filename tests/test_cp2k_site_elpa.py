"""Bind shared ELPA selection to accepted files and preserve private deliveries."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
import cp2k_feature_contract as contract
import test_cp2k_contract as fixtures

ROOT = Path(__file__).resolve().parents[1]


class SharedELPATests(unittest.TestCase):
    def test_pins_match_the_archived_deployment_manifests(self):
        paths = {"16v100-avx2": "elpa-2026.02.001-backtransform/avx2-manifest.json",
                 "8v100v0-avx512": "elpa-2026.02.001-backtransform/avx2-manifest.json",
                 "4v100-avx512": "elpa-2026.02.001-backtransform/avx512-manifest.json",
                 "dsprhbm": "elpa-2026.02.001-commfix/dsprhbm-manifest.json"}
        for target, name in paths.items():
            with self.subTest(target=target):
                record, spec = contract.site_elpa_spec(target)
                data = (ROOT / "deployment" / name).read_bytes()
                rows = {row["path"]: row for row in json.loads(data)}
                self.assertEqual(record["manifest_sha256"], hashlib.sha256(data).hexdigest())
                for path, digest in spec["files"].items():
                    self.assertEqual(digest, rows[path]["sha256"])
                for path, destination in spec["symlinks"].items():
                    self.assertEqual(destination, rows[path]["target"])
                required = {row["path"] for row in rows.values()
                            if row["path"].startswith(("include/", "lib/"))
                            and row["kind"] != "directory"}
                self.assertTrue(required <= spec["files"].keys() | spec["symlinks"].keys())
                self.assertIn('share/sai/mpi-communicator-fix.json', spec['files'])
                self.assertIn('share/sai/mpi-communicator.patch', spec['files'])
                if target != 'dsprhbm':
                    self.assertIn('share/sai/gpu-backtransform-host-staging.patch', spec['files'])
                self.assertEqual(record["version"], "2026.02.001")
                self.assertTrue(record["prefix"].endswith("/cpu" if target == "dsprhbm" else "/nvidia"))
        self.assertEqual(contract.site_elpa_spec("16v100-avx2")[1],
                         contract.site_elpa_spec("8v100v0-avx512")[1])

    def cache(self, target):
        prefix = "/opt/software/cp2k/test/" + target
        record, _ = contract.site_elpa_spec(target)
        cache = fixtures.CP2KContractTests().cache(target).replace(prefix + "/dependencies/elpa", record["prefix"])
        cache += "\npkgcfg_lib_CP2K_ELPA_elpa_openmp:FILEPATH=" + record["prefix"] + "/lib/libelpa_openmp.so"
        cache += "\nCP2K_ELPA_VERSION:INTERNAL=2026.02.001\n"
        return prefix, record, cache

    def test_shared_cache_requires_correct_partition_provenance_and_library(self):
        for target in contract.SITE_ELPA_TARGETS:
            prefix, record, cache = self.cache(target)
            contract.check_cache(cache, prefix, target, record)
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    contract.check_cache(cache, prefix, target)
                for wrong in (dict(record, target="unknown"), dict(record, lock_sha256="0"*64),
                              dict(record, prefix=prefix + "/dependencies/elpa")):
                    with self.assertRaisesRegex(ValueError, "provider"):
                        contract.check_cache(cache, prefix, target, wrong)
                for old in ("2026.02.002", "2024.05.001"):
                    with self.assertRaisesRegex(ValueError, "version"):
                        contract.check_cache(cache.replace("VERSION:INTERNAL=2026.02.001", "VERSION:INTERNAL="+old),
                                             prefix, target, record)
                for wrong in ("/legacy/libelpa_openmp.so", record["prefix"] + "/lib/libelpa_openmp.so-NOTFOUND"):
                    with self.assertRaisesRegex(ValueError, "CMake library"):
                        contract.check_cache(cache.replace(record["prefix"] + "/lib/libelpa_openmp.so", wrong),
                                             prefix, target, record)

    def test_linkage_must_use_exact_selected_elpa(self):
        for target in contract.SITE_ELPA_TARGETS:
            prefix, record, _ = self.cache(target)
            links = "\n".join((
                "libelpa_openmp.so.19 => " + record["prefix"] + "/lib/libelpa_openmp.so.19",
                "libhcoll.so.1 => " + contract.HPCX_ROOT + "/hcoll/lib/libhcoll.so.1",
                "libocoms.so.0 => " + contract.HPCX_ROOT + "/hcoll/lib/libocoms.so.0",
                "libcusolverMp.so.0 => /opt/devtools/nvidia/mp_libs/lib/libcusolverMp.so.0",
                "libnccl.so.2 => /opt/devtools/nvidia/nccl_2.29.3_cuda12.9/lib/libnccl.so.2",
                "libcuda.so.1 => /opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/cuda/12.9/compat/libcuda.so.1"))
            contract.check_linkage(links, target, prefix, record)
            for wrong in ("/opt/devtools/elpa/elpa-2026.02.001-2603-gnu/nvidia",
                          prefix + "/dependencies/elpa", record["prefix"] + "-other"):
                with self.subTest(target=target, wrong=wrong), self.assertRaisesRegex(ValueError, "ELPA"):
                    contract.check_linkage(links.replace(record["prefix"], wrong), target, prefix, record)

    def test_runtime_rejects_replaced_files_and_redirected_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "libelpa.so.19"
            library.write_bytes(b"pinned library")
            link = root / "libelpa.so"
            link.symlink_to(library.name)
            record = dict(prefix=str(root), provider="fixture")
            spec = dict(files={library.name: hashlib.sha256(library.read_bytes()).hexdigest()},
                        symlinks={link.name: library.name})
            with patch.object(contract, "site_elpa_spec", return_value=(record, spec)):
                contract.check_site_elpa(record, "16v100-avx2")
                library.write_bytes(b"silently replaced library")
                with self.assertRaisesRegex(ValueError, "file differs"):
                    contract.check_site_elpa(record, "16v100-avx2")
                library.write_bytes(b"pinned library")
                link.unlink()
                link.symlink_to("missing")
                with self.assertRaisesRegex(ValueError, "link differs"):
                    contract.check_site_elpa(record, "16v100-avx2")

    def test_installed_verifier_reads_its_shipped_lock_without_controller(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("cp2k_feature_contract.py", "cp2k_site_elpa.json"):
                shutil.copyfile(ROOT / "controller" / name, Path(directory) / name)
            code = ("import runpy, sys; c = runpy.run_path(sys.argv[1]); "
                    "print(c['site_elpa_spec']('dsprhbm')[0]['prefix'])")
            result = subprocess.run([sys.executable, "-I", "-c", code,
                                     str(Path(directory) / "cp2k_feature_contract.py")],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), contract.site_elpa_spec("dsprhbm")[0]["prefix"])


if __name__ == "__main__":
    unittest.main()
