"""Native delivery extraction, including real Squashfs without container exec."""
import copy
import contextlib
import hashlib
import io
import json
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


def paired_entries(recipe="b" * 64, target="4v100-avx512"):
    pair = {"deepmd-kit": "a" * 40, "lammps": "c" * 40}
    entries = []
    for software, command in (("deepmd-kit", "dp"), ("lammps", "lmp")):
        identity = make_identity(software, "release", "v1", pair[software], "v1", recipe,
                                 target, stack_sources=pair)
        entries.append(dict(identity=identity, commands={command: "bin/" + command}, external_roots=[],
                            runtime={"modules": [], "prepend": {}, "set": {}}))
    return entries


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

    def test_command_symlinks_resolve_to_delivered_executables_without_rewriting(self):
        for target in ("abacus_max_gpu", "../bin/abacus_max_gpu", "absolute-link"):
            manifest, _ = minimal_manifest()
            executable = manifest["files"][2]
            command = executable["path"]
            executable["path"] = command + "_max_gpu"
            manifest["files"] += [
                {"path": command, "kind": "symlink", "mode": 0o755, "target": target},
                {"path": str(Path(command).with_name("absolute-link")), "kind": "symlink",
                 "mode": 0o755, "target": "/" + executable["path"]}]
            original = copy.deepcopy(manifest)
            with self.subTest(target=target):
                actual = delivery.validate_manifest(manifest)
                self.assertEqual(actual["files"], sorted(original["files"], key=lambda row: row["path"]))
                self.assertEqual(manifest, original)

    def test_command_symlinks_require_a_delivered_executable_target(self):
        for target in ("abacus_max_gpu", ".", "missing", "abacus", "/opt/devtools/example/abacus"):
            manifest, _ = minimal_manifest()
            executable = manifest["files"][2]
            command = executable["path"]
            executable.update(path=command + "_max_gpu", mode=0o644)
            manifest["files"].append({"path": command, "kind": "symlink", "mode": 0o755,
                                      "target": target})
            with self.subTest(target=target), self.assertRaises(ValueError):
                delivery.validate_manifest(manifest)

    def test_no_link_ancestors_or_reserved_metadata_paths(self):
        manifest, _ = minimal_manifest()
        prefix = manifest["files"][0]["path"]
        for row in ({"path": prefix + "/bin", "kind": "symlink", "mode": 0o755,
                     "target": "/opt/devtools/example"},
                    {"path": prefix + "/share", "kind": "symlink", "mode": 0o755,
                     "target": "/opt/devtools/example"},
                    {"path": prefix + "/" + delivery.MANIFEST_PATH, "kind": "file", "mode": 0o644,
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
        first["files"][2] = {"path": first["files"][2]["path"], "kind": "symlink",
                              "mode": 0o755, "target": "/" + second["files"][2]["path"]}
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

    def build_image(self, targets=("4v100-avx512",), corrupt=False, boundary=False, command_link=False):
        entries = []
        for target in targets:
            manifest, data = minimal_manifest(target)
            item = manifest["entries"][0]
            entries.append(item)
            base = self.source / item["identity"]["install_prefix"].lstrip("/")
            (base / "bin").mkdir(parents=True)
            executable = base / ("bin/abacus_max_gpu" if command_link else "bin/abacus")
            executable.write_bytes(data)
            executable.chmod(0o755)
            if command_link:
                (base / "bin/abacus").symlink_to("abacus_max_gpu")
            (base / "link").symlink_to("bin/abacus")
            extra = base / "data [literal] -> name"
            extra.write_bytes(b"second-file\x00bytes\n")
            # Unselected rootfs files never enter the delivery bundle.
        (self.source / "SECRET-NOT-IN-PREFIX").write_text("not exported")
        manifest = delivery.write_manifests(entries, self.source)
        metadata = self.source / entries[0]["identity"]["install_prefix"].lstrip("/") / delivery.MANIFEST_PATH
        local = json.loads(metadata.read_text())
        if corrupt:
            next(row for row in local["files"] if row["path"].endswith("bin/abacus"))["sha256"] = "0" * 64
        if boundary:
            rows = [row for row in local["files"] if row["kind"] == "file" and
                    (row["path"].endswith("bin/abacus") or row["path"].endswith("data [literal] -> name"))]
            self.assertEqual(len(rows), 2)
            first, second = ((self.source / row["path"]).read_bytes() for row in rows)
            forged = (first[:2], first[2:] + second)
            for row, data in zip(rows, forged):
                row.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        if corrupt or boundary:
            metadata.chmod(0o644)
            metadata.write_text(delivery.canonical(local))
            metadata.chmod(0o444)
        return self.pack(), manifest

    def pack(self, name="image.squashfs"):
        image = self.root / name
        subprocess.run(["mksquashfs", str(self.source), str(image), "-noappend", "-no-progress", "-processors", "1"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return image

    def export(self, image, destination=None, digest=None, prefixes=None):
        return delivery.export_image(image, digest or delivery.checksum(image), destination or self.root / "export",
                                     prefixes=prefixes, reader_factory=lambda path: delivery.SquashfsReader(path, 0))

    def test_real_export_multiple_partitions_modules_hashes_and_no_unselected_files(self):
        image, manifest = self.build_image(("4v100-avx512", "16v100-avx2"))
        old_umask = os.umask(0o077)
        try:
            receipt = self.export(image)
        finally:
            os.umask(old_umask)
        output = self.root / "export"
        self.assertEqual([path.name for path in output.iterdir()], ["abacus"])
        self.assertFalse((output / "rootfs").exists())
        self.assertEqual(len(receipt["installations"]), 2)
        self.assertIn("acceptance still required", receipt["validation"])
        for row in manifest["files"]:
            path = output / Path(row["path"]).relative_to("opt/software")
            if row["kind"] == "file":
                self.assertEqual(delivery.checksum(path), row["sha256"])
            elif row["kind"] == "symlink":
                self.assertEqual(os.readlink(path), row["target"])
        for installation in receipt["installations"]:
            base = output / installation["path"]
            self.assertTrue((base / "modulefiles").is_dir())
            self.assertEqual(delivery.checksum(base / delivery.MANIFEST_PATH), installation["manifest_sha256"])
            local = json.loads((base / delivery.MANIFEST_PATH).read_text())
            self.assertEqual(local["schema"], 2)
            self.assertEqual(local["entry"]["identity"]["install_prefix"], installation["expected_install_prefix"])
            self.assertEqual(installation["required_install_prefixes"], [])
            self.assertNotIn("must also be installed", (base / delivery.README_PATH).read_text())
            self.assertEqual(json.loads((base / delivery.EXPORT_PATH).read_text())["module_use"],
                             installation["expected_install_prefix"] + "/modulefiles")
            text = (base / delivery.FRAGMENT_PATH).read_text()
            self.assertNotIn(str(output), text)
            self.assertIn("/opt/software/abacus/development/", text)
            self.assertEqual((base / "modulefiles").stat().st_mode & 0o777, 0o755)
        self.assertEqual((output / "abacus").stat().st_mode & 0o777, 0o755)

    def test_single_selection_is_one_complete_folder_and_preserves_manifest_bytes(self):
        image, manifest = self.build_image(("4v100-avx512", "16v100-avx2"))
        item = manifest["entries"][0]
        prefix = item["identity"]["install_prefix"]
        receipt = self.export(image, prefixes=[prefix])
        output = self.root / "export"
        self.assertEqual(receipt["installations"][0]["path"], ".")
        self.assertEqual(receipt["expected_install_prefixes"], [prefix])
        self.assertTrue((output / "bin/abacus").is_file())
        self.assertTrue((output / "modulefiles").is_dir())
        self.assertFalse((output / "rootfs").exists())
        self.assertFalse((output / "opt").exists())
        self.assertFalse((output / "inventory.json").exists())
        self.assertEqual((output / delivery.MANIFEST_PATH).read_bytes(),
                         (self.source / prefix.lstrip("/") / delivery.MANIFEST_PATH).read_bytes())
        for wrong in (prefix + "/", prefix.replace("/opt/", "/tmp/"), "/opt/software/../../etc", ""):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, "canonical"):
                self.export(image, self.root / "bad", prefixes=[wrong])

    def test_command_symlink_survives_manifest_roundtrip_and_real_export(self):
        image, manifest = self.build_image(command_link=True)
        item = manifest["entries"][0]
        prefix = item["identity"]["install_prefix"]
        self.assertEqual(delivery.read_installed_manifests([item], self.source), manifest)
        receipt = self.export(image, prefixes=[prefix])
        output = self.root / "export"
        command = output / "bin/abacus"
        self.assertTrue(command.is_symlink())
        self.assertEqual(os.readlink(command), "abacus_max_gpu")
        executable = output / "bin/abacus_max_gpu"
        self.assertFalse(executable.is_symlink())
        row = next(row for row in manifest["files"] if row["path"].endswith("/bin/abacus_max_gpu"))
        self.assertEqual(delivery.checksum(executable), row["sha256"])
        self.assertEqual(executable.stat().st_mode & 0o777, 0o755)
        self.assertEqual(subprocess.run([str(command)], check=True).returncode, 0)
        self.assertEqual(receipt["expected_install_prefixes"], [prefix])
        for relative in (delivery.MANIFEST_PATH, delivery.FRAGMENT_PATH):
            self.assertEqual((output / relative).read_bytes(),
                             (self.source / prefix.lstrip("/") / relative).read_bytes())

    def test_write_manifest_has_no_self_hash_and_is_idempotent(self):
        _, manifest = self.build_image()
        item = manifest["entries"][0]
        prefix = item["identity"]["install_prefix"].lstrip("/")
        path = self.source / prefix / delivery.MANIFEST_PATH
        before = path.read_bytes()
        self.assertEqual(delivery.write_manifests([item], self.source), manifest)
        self.assertEqual(delivery.read_installed_manifests([item], self.source), manifest)
        self.assertEqual(path.read_bytes(), before)
        paths = {row["path"] for row in manifest["files"]}
        self.assertNotIn(prefix + "/" + delivery.MANIFEST_PATH, paths)
        self.assertIn(prefix + "/" + delivery.FRAGMENT_PATH, paths)
        self.assertTrue(any("/modulefiles/abacus/" in value for value in paths))
        self.assertFalse((self.source / "opt/sai-delivery").exists())
        (self.source / prefix / delivery.EXPORT_PATH).write_text("untrusted receipt")
        with self.assertRaisesRegex(ValueError, "reserved"):
            delivery.inventory([item], self.source)

    def test_multiple_environment_keys_survive_manifest_roundtrip_and_real_export(self):
        item = entry()
        prefix = item["identity"]["install_prefix"]
        item["runtime"] = {
            "modules": ["zeta/1.0", "alpha/1.0"],
            "prepend": {"PATH": [prefix + "/bin"],
                        "LD_LIBRARY_PATH": [prefix + "/lib/z", prefix + "/lib/a"]},
            "set": {"OMP_NUM_THREADS": "2", "ABACUS_ROOT": prefix},
        }
        with patch(__name__ + ".entry", return_value=item):
            image, manifest = self.build_image()
        self.assertEqual(delivery.read_installed_manifests([item], self.source), manifest)
        self.export(image)
        fragment = (self.root / "export" / delivery.FRAGMENT_PATH).read_text()
        self.assertEqual(fragment, (self.source / prefix.lstrip("/") / delivery.FRAGMENT_PATH).read_text())
        self.assertLess(fragment.index('depends-on "zeta/1.0"'), fragment.index('depends-on "alpha/1.0"'))
        self.assertLess(fragment.index('prepend-path LD_LIBRARY_PATH "' + prefix + '/lib/a"'),
                        fragment.index('prepend-path LD_LIBRARY_PATH "' + prefix + '/lib/z"'))

    def test_self_manifest_must_be_regular_readonly_and_not_list_itself(self):
        _, manifest = self.build_image()
        prefix = manifest["entries"][0]["identity"]["install_prefix"].lstrip("/")
        path = self.source / prefix / delivery.MANIFEST_PATH
        local = json.loads(path.read_text())
        local["files"].append({"path": prefix + "/" + delivery.MANIFEST_PATH, "kind": "file",
                               "mode": 0o444, "size": 0, "sha256": "0" * 64})
        path.chmod(0o644)
        path.write_text(delivery.canonical(local))
        path.chmod(0o444)
        image = self.pack("self-forged.squashfs")
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.export(image)
        local["files"].pop()
        path.chmod(0o644)
        path.write_text(delivery.canonical(local))
        image = self.pack("self-mode.squashfs")
        with self.assertRaisesRegex(ValueError, "mode, type or size"):
            self.export(image)

    def test_module_tampering_rejected_even_with_self_consistent_inventory(self):
        _, manifest = self.build_image()
        prefix = manifest["entries"][0]["identity"]["install_prefix"].lstrip("/")
        fragment = self.source / prefix / delivery.FRAGMENT_PATH
        fragment.chmod(0o644)
        fragment.write_text("#%Module1.0\nexec evil-command\n")
        fragment.chmod(0o444)
        changed = delivery.inventory(manifest["entries"], self.source)
        metadata = self.source / prefix / delivery.MANIFEST_PATH
        metadata.chmod(0o644)
        metadata.write_text(delivery.canonical({"schema": 2, "entry": changed["entries"][0], "files": changed["files"]}))
        metadata.chmod(0o444)
        image = self.pack("tampered.squashfs")
        with self.assertRaisesRegex(ValueError, "trusted declarative"):
            self.export(image)
        with self.assertRaises(ValueError):
            delivery.write_manifests(manifest["entries"], self.source)

    def make_paired_source(self, entries):
        for item in entries:
            base = self.source / item["identity"]["install_prefix"].lstrip("/")
            (base / "bin").mkdir(parents=True)
            (base / "lib").mkdir()
            for command in item["commands"].values():
                path = base / command
                path.write_bytes(b"#!/bin/sh\nexit 125\n")
                path.chmod(0o755)
        return delivery.write_manifests(entries, self.source)

    def test_selected_paired_installation_automatically_exports_exact_companion(self):
        entries = paired_entries() + paired_entries("d" * 64) + paired_entries(target="16v100-avx2")
        first, companion = entries[:2]
        first_prefix = first["identity"]["install_prefix"]
        companion_prefix = companion["identity"]["install_prefix"]
        companion["runtime"]["prepend"]["LD_LIBRARY_PATH"] = [first_prefix + "/lib"]
        self.make_paired_source(entries)
        image = self.pack()
        receipt = self.export(image, prefixes=[companion_prefix])
        self.assertEqual(set(receipt["expected_install_prefixes"]), {first_prefix, companion_prefix})
        self.assertEqual(len(receipt["installations"]), 2)
        for item in receipt["installations"]:
            self.assertNotEqual(item["path"], ".")
            path = self.root / "export" / item["path"]
            self.assertTrue((path / delivery.MANIFEST_PATH).is_file())
            self.assertEqual(set(item["bundle_install_prefixes"]), {first_prefix, companion_prefix})
            self.assertEqual(item["required_install_prefixes"], [
                companion_prefix if item["expected_install_prefix"] == first_prefix else first_prefix])
        # Removing the manifest cannot turn a paired delivery into standalone.
        missing = self.source / first_prefix.lstrip("/") / delivery.MANIFEST_PATH
        missing.unlink()
        image = self.pack("missing-companion.squashfs")
        with self.assertRaisesRegex(ValueError, "companion"):
            self.export(image, destination=self.root / "missing", prefixes=[companion_prefix])

    def test_same_source_pair_cannot_cross_recipe_runtime_or_symlink(self):
        entries = paired_entries() + paired_entries("d" * 64)
        manifest = self.make_paired_source(entries)
        changed = copy.deepcopy(manifest)
        foreign = entries[2]["identity"]["install_prefix"]
        changed["entries"][1]["runtime"]["prepend"]["LD_LIBRARY_PATH"] = [foreign + "/lib"]
        with self.assertRaisesRegex(ValueError, "outside this delivery"):
            delivery.validate_manifest(changed)
        changed = copy.deepcopy(manifest)
        changed["files"].append({"path": entries[1]["identity"]["install_prefix"].lstrip("/") + "/foreign",
                                 "kind": "symlink", "mode": 0o755, "target": foreign + "/lib"})
        with self.assertRaisesRegex(ValueError, "escapes"):
            delivery.validate_manifest(changed)

    def test_inventoried_extra_file_boundary_and_manifest_symlink_rejected(self):
        _, manifest = self.build_image()
        prefix = manifest["entries"][0]["identity"]["install_prefix"].lstrip("/")
        extra = self.source / prefix / "not-in-inventory"
        extra.write_text("unlisted")
        image = self.pack("unlisted.squashfs")
        with self.assertRaisesRegex(ValueError, "differ from"):
            self.export(image)
        extra.unlink()
        metadata = self.source / prefix / delivery.MANIFEST_PATH
        moved = self.source / "manifest-outside-prefix"
        metadata.rename(moved)
        metadata.symlink_to("/manifest-outside-prefix")
        image = self.pack("linked.squashfs")
        with self.assertRaises(ValueError):
            self.export(image)

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

    def test_not_software_probe_verifies_four_partitions_and_single_folder(self):
        import probe_native_export as probe
        native_run = subprocess.run
        with contextlib.redirect_stdout(io.StringIO()) as output, patch.object(
                probe.subprocess, "run", side_effect=lambda *args, **kwargs:
                native_run(*args, **dict(kwargs, stdout=subprocess.DEVNULL))):
            probe.prepare(self.root / "probe")
        prepared = json.loads(output.getvalue())
        self.assertFalse(prepared["scientific_artifact"])
        def export_fixture(image, digest, destination, **kwargs):
            return delivery.export_image(image, digest, destination, **kwargs,
                                         reader_factory=lambda path: delivery.SquashfsReader(path, 0))
        with patch.object(probe, "export_image", side_effect=export_fixture), contextlib.redirect_stdout(io.StringIO()) as output:
            probe.verify(prepared["fixture"], self.root / "probe-all")
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["container_executed"])
        self.assertFalse(result["system_opt_modified"])
        self.assertEqual(len(result["export_receipt"]["installations"]), 4)
        prefix = result["export_receipt"]["expected_install_prefixes"][0]
        with patch.object(probe, "export_image", side_effect=export_fixture), contextlib.redirect_stdout(io.StringIO()) as output:
            probe.verify(prepared["fixture"], self.root / "probe-one", prefixes=[prefix])
        result = json.loads(output.getvalue())
        self.assertEqual(result["export_receipt"]["installations"][0]["path"], ".")
        self.assertTrue((self.root / "probe-one/bin/abacus").is_file())


if __name__ == "__main__":
    unittest.main()
