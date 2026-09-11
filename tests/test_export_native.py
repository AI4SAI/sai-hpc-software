"""Native delivery extraction, including real Squashfs without container exec."""
import copy
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
import export_native as delivery
from release_contract import make_identity


def entry(target="4v100-avx512"):
    identity = make_identity("abacus", "development", "develop", "a" * 40,
                             "v3.11.0", "b" * 64, target)
    return {"identity": identity, "commands": {"abacus": "bin/abacus"},
            "external_roots": ["/opt/devtools/example"],
            "runtime": {"modules": ["example/1.0"], "prepend": {}, "set": {}}}


def minimal_manifest(target="4v100-avx512"):
    item = entry(target)
    prefix = item["identity"]["install_prefix"].lstrip("/")
    data = b"#!/bin/sh\nexit 0\n"
    return {"schema": 1, "entries": [item], "files": [
        {"path": prefix, "kind": "directory", "mode": 0o755},
        {"path": prefix + "/bin", "kind": "directory", "mode": 0o755},
        {"path": prefix + "/bin/abacus", "kind": "file", "mode": 0o755,
         "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}, data


class InventoryTests(unittest.TestCase):
    def test_valid_inventory_is_fresh_and_canonical(self):
        manifest, _ = minimal_manifest()
        original = copy.deepcopy(manifest)
        actual = delivery.validate_manifest(manifest)
        actual["files"][0]["mode"] = 0o555
        self.assertEqual(manifest, original)

    def test_path_traversal_absolute_duplicate_special_and_unknown_rejected(self):
        base, _ = minimal_manifest()
        bad_paths = ["../escape", "/etc/passwd", "opt//software", "opt/a/../b", "opt/a\nname", "opt/software/else"]
        for path in bad_paths:
            manifest = copy.deepcopy(base)
            manifest["files"][2]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                delivery.validate_manifest(manifest)
        for modify in (lambda m: m["files"].append(dict(m["files"][2])),
                       lambda m: m["files"][2].update(kind="fifo"),
                       lambda m: m["files"][2].update(uid=0),
                       lambda m: m.update(schema=True),
                       lambda m: m["entries"][0]["runtime"].update(shell="/payload.sh")):
            manifest = copy.deepcopy(base)
            modify(manifest)
            with self.assertRaises(ValueError):
                delivery.validate_manifest(manifest)

    def test_inaccessible_or_privileged_modes_fail(self):
        for index, mode in ((0, 0o700), (0, 0o777), (2, 0o700), (2, 0o4755),
                            (2, 0o664), (2, 0o644), (2, -1), (2, True)):
            manifest, _ = minimal_manifest()
            manifest["files"][index]["mode"] = mode
            with self.subTest(index=index, mode=mode), self.assertRaises(ValueError):
                delivery.validate_manifest(manifest)

    def test_no_link_ancestors_or_reserved_module_paths(self):
        manifest, _ = minimal_manifest()
        prefix = manifest["files"][0]["path"]
        for row in ({"path": prefix + "/bin", "kind": "symlink", "mode": 0o755,
                     "target": "/opt/devtools/example"},
                    {"path": prefix + "/share", "kind": "symlink", "mode": 0o755,
                     "target": "/opt/devtools/example"},
                    {"path": prefix + "/" + delivery.FRAGMENT_PATH, "kind": "file", "mode": 0o644,
                     "size": 0, "sha256": hashlib.sha256(b"").hexdigest()}):
            changed = copy.deepcopy(manifest)
            changed["files"] = [item for item in changed["files"] if item["path"] != row["path"]] + [row]
            with self.assertRaises(ValueError):
                delivery.validate_manifest(changed)

    def test_symlinks_resolve_chains_before_dotdot_and_reject_cycles_or_dangling(self):
        base, _ = minimal_manifest()
        prefix = base["files"][0]["path"]
        bridge = {"path": prefix + "/bridge", "kind": "symlink", "mode": 0o755,
                  "target": "/opt/devtools/example"}
        for target in ("bridge/../secret", "missing", "missing/../bin/abacus", "escape", "/etc/passwd"):
            manifest = copy.deepcopy(base)
            manifest["files"].extend([bridge, {"path": prefix + "/escape", "kind": "symlink",
                                             "mode": 0o755, "target": target}])
            with self.subTest(target=target), self.assertRaises(ValueError):
                delivery.validate_manifest(manifest)
        base["files"].extend([bridge, {"path": prefix + "/link", "kind": "symlink",
                                      "mode": 0o755, "target": "bin/abacus"}])
        delivery.validate_manifest(base)

    def test_cross_partition_symlink_is_not_a_paired_dependency(self):
        first, _ = minimal_manifest()
        second, _ = minimal_manifest("16v100-avx2")
        first["entries"] += second["entries"]
        first["files"] += second["files"]
        first["files"].append({"path": first["files"][0]["path"] + "/cross", "kind": "symlink",
                               "mode": 0o755, "target": "/" + second["files"][2]["path"]})
        with self.assertRaisesRegex(ValueError, "partition"):
            delivery.validate_manifest(first)

    def test_paired_bundle_requires_matching_companion_source_and_stack(self):
        pair = {"deepmd-kit": "a" * 40, "lammps": "c" * 40}
        entries, files = [], []
        for software, command in (("deepmd-kit", "dp"), ("lammps", "lmp")):
            identity = make_identity(software, "release", "v1", pair[software], "v1", "b" * 64,
                                     "4v100-avx512", stack_sources=pair)
            item = dict(identity=identity, commands={command: "bin/" + command}, external_roots=[],
                        runtime={"modules": [], "prepend": {}, "set": {}})
            prefix = identity["install_prefix"].lstrip("/")
            entries.append(item)
            files += [{"path": prefix, "kind": "directory", "mode": 0o755},
                      {"path": prefix + "/bin", "kind": "directory", "mode": 0o755},
                      {"path": prefix + "/bin/" + command, "kind": "file", "mode": 0o755,
                       "size": 0, "sha256": hashlib.sha256(b"").hexdigest()}]
        base = {"schema": 1, "entries": entries, "files": files}
        delivery.validate_manifest(base)
        changed = copy.deepcopy(base)
        changed["entries"].pop()
        with self.assertRaisesRegex(ValueError, "companion"):
            delivery.validate_manifest(changed)

        # A prerelease component may use its partner's stable release, without
        # relabelling that partner as prerelease. Their full source-pair lock
        # must still match, and a wrong pair remains forbidden above.
        mixed = copy.deepcopy(base)
        old = mixed["entries"][1]["identity"]
        newer = make_identity("lammps", "prerelease", "v1-rc1", pair["lammps"], "v1-rc1",
                              "b" * 64, "4v100-avx512", stack_sources=pair)
        mixed["entries"][1]["identity"] = newer
        for row in mixed["files"]:
            if row["path"].startswith(old["install_prefix"].lstrip("/")):
                row["path"] = row["path"].replace(old["install_prefix"].lstrip("/"),
                                                   newer["install_prefix"].lstrip("/"), 1)
        mixed["entries"][1]["runtime"]["prepend"]["LD_LIBRARY_PATH"] = [
            mixed["entries"][0]["identity"]["install_prefix"] + "/lib"]
        delivery.validate_manifest(mixed)
        self.assertEqual([item["identity"]["track"] for item in mixed["entries"]], ["release", "prerelease"])
        changed = copy.deepcopy(base)
        mismatched = dict(pair, lammps="d" * 40)
        changed["entries"][1]["identity"] = make_identity("lammps", "release", "v1", "d" * 40,
            "v1", "b" * 64, "4v100-avx512", stack_sources=mismatched)
        with self.assertRaisesRegex(ValueError, "companion"):
            delivery.validate_manifest(changed)

    def test_sif_primary_partition_offset_and_never_exec(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "file.sif"
            image.write_bytes(b"x" * 1000)
            listing = "1 |1 |NONE |80-99 |JSON.Generic\n2 |1 |NONE |100-900 |FS (Squashfs/*System/amd64)\n"
            with patch.object(delivery.subprocess, "check_output", return_value=listing) as command:
                reader = delivery.SquashfsReader.from_sif(image)
            self.assertEqual(reader.offset, 100)
            self.assertEqual(command.call_args.args[0], ["apptainer", "sif", "list", str(image)])
            for bad in ("", listing + listing, listing.replace("900", "9999"), listing.replace("*System", "Data")):
                with patch.object(delivery.subprocess, "check_output", return_value=bad), self.assertRaises(ValueError):
                    delivery.SquashfsReader.from_sif(image)


@unittest.skipUnless(shutil.which("mksquashfs") and shutil.which("unsquashfs"), "Squashfs tools required")
class RealSquashfsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sai-export-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "image-root"
        self.source.mkdir()

    def build_image(self, targets=("4v100-avx512",), corrupt=False, boundary=False):
        entries = []
        for target in targets:
            manifest, data = minimal_manifest(target)
            item = manifest["entries"][0]
            entries.append(item)
            base = self.source / item["identity"]["install_prefix"].lstrip("/")
            (base / "bin").mkdir(parents=True)
            executable = base / "bin/abacus"
            executable.write_bytes(data)
            executable.chmod(0o755)
            (base / "link").symlink_to("bin/abacus")
            extra = base / "data [literal] -> name"
            extra.write_bytes(b"second-file\x00bytes\n")
            # Unselected rootfs files never enter the delivery bundle.
        (self.source / "SECRET-NOT-IN-PREFIX").write_text("not exported")
        manifest = delivery.inventory(entries, self.source)
        metadata = self.source / delivery.MANIFEST_PATH
        metadata.parent.mkdir(parents=True)
        if corrupt:
            next(row for row in manifest["files"] if row["kind"] == "file")["sha256"] = "0" * 64
        if boundary:
            rows = [row for row in manifest["files"] if row["kind"] == "file"]
            self.assertEqual(len(rows), 2)
            first, second = ((self.source / row["path"]).read_bytes() for row in rows)
            forged = (first[:2], first[2:] + second)
            for row, data in zip(rows, forged):
                row.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        metadata.write_text(delivery.canonical(manifest))
        image = self.root / "image.squashfs"
        subprocess.run(["mksquashfs", str(self.source), str(image), "-noappend", "-no-progress", "-processors", "1"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return image, manifest

    def export(self, image, destination=None, digest=None):
        return delivery.export_image(image, digest or delivery.checksum(image), destination or self.root / "export",
                                     reader_factory=lambda path: delivery.SquashfsReader(path, 0))

    def test_real_export_multiple_partitions_modules_hashes_and_no_unselected_files(self):
        image, manifest = self.build_image(("4v100-avx512", "16v100-avx2"))
        old_umask = os.umask(0o077)
        try:
            receipt = self.export(image)
        finally:
            os.umask(old_umask)
        output = self.root / "export"
        self.assertFalse((output / "rootfs/SECRET-NOT-IN-PREFIX").exists())
        self.assertEqual(len(receipt["generated_files"]), 3)
        self.assertIn("acceptance still required", receipt["validation"])
        for row in manifest["files"]:
            path = output / "rootfs" / row["path"]
            if row["kind"] == "file":
                self.assertEqual(delivery.checksum(path), row["sha256"])
            elif row["kind"] == "symlink":
                self.assertEqual(os.readlink(path), row["target"])
        for row in receipt["generated_files"]:
            text = (output / row["path"]).read_text()
            self.assertNotIn(str(output), text)
            self.assertIn("/opt/software/abacus/development/", text)
            self.assertEqual(delivery.checksum(output / row["path"]), row["sha256"])
        self.assertEqual((output / "rootfs/opt").stat().st_mode & 0o777, 0o755)
        self.assertEqual((output / "modulefiles").stat().st_mode & 0o777, 0o755)

    def test_digest_mismatch_fails_before_destination_creation(self):
        image, _ = self.build_image()
        with self.assertRaisesRegex(ValueError, "image checksum"):
            self.export(image, digest="0" * 64)
        self.assertFalse((self.root / "export").exists())

    def test_corrupt_file_inventory_preserves_no_partial_export(self):
        image, _ = self.build_image(corrupt=True)
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.export(image)
        self.assertFalse((self.root / "export").exists())
        self.assertFalse(list(self.root.glob(".sai-native-export-*")))

    def test_forged_boundaries_cannot_redistribute_concatenated_bytes(self):
        image, _ = self.build_image(boundary=True)
        with self.assertRaisesRegex(ValueError, "size differs"):
            self.export(image)
        self.assertFalse((self.root / "export").exists())

    def test_existing_destination_and_symlink_parent_are_never_overwritten(self):
        image, _ = self.build_image()
        destination = self.root / "existing"
        destination.mkdir()
        marker = destination / "keep"
        marker.write_text("user data")
        with self.assertRaisesRegex(ValueError, "already exist"):
            self.export(image, destination)
        self.assertEqual(marker.read_text(), "user data")
        link = self.root / "link-parent"
        link.symlink_to(destination)
        with self.assertRaisesRegex(ValueError, "symlink ancestors"):
            self.export(image, link / "new")
        self.assertFalse((destination / "new").exists())

    def test_inventory_rejects_host_umask_0700_before_packing(self):
        item = entry()
        base = self.source / item["identity"]["install_prefix"].lstrip("/")
        (base / "bin").mkdir(parents=True)
        (base / "bin/abacus").write_bytes(b"test")
        (base / "bin/abacus").chmod(0o700)
        with self.assertRaisesRegex(ValueError, "unreadable"):
            delivery.inventory([item], self.source)


if __name__ == "__main__":
    unittest.main()
