"""Source channels and immutable prefixes are independent of build execution."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import release_contract as contract
import resolve_source


class IdentityTests(unittest.TestCase):
    def identity(self, **changes):
        values = dict(software="abacus", track="release", source_ref="v3.10.0",
                      source_sha="a" * 40, source_version="v3.10.0",
                      recipe_sha256="b" * 64, target="dsprhbm")
        values.update(changes)
        return contract.make_identity(**values)

    def test_all_software_have_exact_tracks_targets_and_real_partitions(self):
        profile = json.loads((ROOT / "profiles/software-tracking.json").read_text())
        self.assertEqual(profile["schema"], 1)
        self.assertEqual(profile["tracks"], list(contract.TRACKS))
        self.assertEqual(profile["partitions"], contract.PARTITIONS)
        self.assertEqual(set(profile["software"]), set(contract.SOFTWARE))
        for software, settings in contract.SOFTWARE.items():
            declared = profile["software"][software]
            self.assertEqual(declared["tracks"], list(contract.TRACKS))
            self.assertEqual(declared["targets"], list(settings["targets"]))
            for name in ("repository", "development_ref"):
                self.assertEqual(declared[name], settings[name])
            self.assertEqual(contract.allowed_partitions(software),
                             tuple(contract.PARTITIONS[t] for t in settings["targets"]))
            for target in settings["targets"]:
                identity = self.identity(software=software, target=target)
                self.assertEqual(identity["partition"], contract.PARTITIONS[target])
                self.assertTrue(identity["install_prefix"].endswith("/" + identity["partition"]))
                self.assertEqual(contract.validate_identity(identity), identity)
            if software not in ("abacus", "cp2k"):
                with self.assertRaises(ValueError):
                    self.identity(software=software)
        with self.assertRaises(ValueError):
            contract.allowed_partitions("unregistered")

    def test_prefix_separates_recipe_source_partition_and_all_three_tracks(self):
        base = self.identity()
        self.assertEqual(base["build_id"], "v3.10.0-g" + "a" * 12 + "-r" + "b" * 12)
        self.assertEqual(base["install_prefix"], "/opt/software/abacus/release/" + base["build_id"] + "/DSPRHBM")
        variants = [base, self.identity(source_sha="c" * 40), self.identity(recipe_sha256="d" * 64),
                    self.identity(target="4v100-avx512"), self.identity(track="development"),
                    self.identity(track="prerelease")]
        self.assertEqual(len({item["install_prefix"] for item in variants}), len(variants))
        skylake = self.identity(target="8v100v0-avx512")
        self.assertEqual(skylake["cpu_arch"], "skylake-avx512")
        self.assertEqual(skylake["dependency_isa"], "avx2")
        self.assertEqual(skylake["cuda_arch"], "70")
        self.assertEqual(base["gpus"], 0)

    def test_normalization_truncation_and_literal_suffix_cannot_alias(self):
        first = self.identity(source_version="v/1")
        second = self.identity(source_version="v?1")
        third = self.identity(source_version=first["version_label"])
        self.assertEqual(len({entry["install_prefix"] for entry in (first, second, third)}), 3)
        for raw in ("../../v1;$(echo bad)", "v" * 1000, " ._v 1_.. "):
            identity = self.identity(source_version=raw)
            self.assertLessEqual(len(identity["version_label"]), 64)
            self.assertLessEqual(len(identity["build_id"]), contract.MAX_BUILD_ID)
            self.assertRegex(identity["version_label"], r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
            self.assertEqual(identity, self.identity(source_version=raw))
            self.assertEqual(len(Path(identity["install_prefix"]).parts), 7)
        self.assertNotEqual(self.identity(source_version="v" * 100 + "a")["install_prefix"],
                            self.identity(source_version="v" * 100 + "b")["install_prefix"])

    def test_invalid_names_hashes_and_target_aliases_are_rejected(self):
        cases = [dict(software="../abacus"), dict(track="latest-release"), dict(track="../release"),
                 dict(target="DSPRHBM"), dict(target="a100"), dict(target="4V100"),
                 dict(target="../4v100-avx512"), dict(source_sha="A" * 40),
                 dict(source_sha="a" * 39), dict(source_sha=None), dict(recipe_sha256="b" * 63),
                 dict(recipe_sha256="b" * 64 + "/tmp"), dict(source_ref="../develop"),
                 dict(source_ref="$(echo bad)"), dict(source_ref="refs//heads/master"),
                 dict(source_version="..."), dict(source_version="v1\ncommand"),
                 dict(source_version="v" * 1025)]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.identity(**changes)

    def test_every_derived_identity_field_is_recomputed(self):
        original = self.identity()
        alterations = {
            "schema": (2, True, 1.0), "install_prefix": ("/opt/software/abacus/release/forged/4V100",),
            "build_id": ("different",), "version_label": ("different",),
            "repository": ("attacker/repo",), "partition": ("4V100",),
            "cpu_arch": ("generic",), "dependency_isa": ("avx2",),
            "gpus": (1, False, 0.0), "cuda_arch": ("70",), "target": ("4v100-avx512",),
            "source_sha": ("c" * 40,), "recipe_sha256": ("d" * 64,),
            "track": ("prerelease",), "stack_digest": ("a" * 64,),
        }
        for name, values in alterations.items():
            for value in values:
                changed = dict(original, **{name: value})
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    contract.validate_identity(changed)
        for changed in ({}, dict(original, unknown="ignored"), None):
            with self.assertRaises(ValueError):
                contract.validate_identity(changed)
        self.assertIsNot(contract.validate_identity(original), original)

    def test_paired_stack_locks_both_sources_and_order_does_not_change_digest(self):
        pair = {"deepmd-kit": "a" * 40, "lammps": "c" * 40}
        common = dict(target="16v100-avx2", stack_sources=pair)
        deepmd = self.identity(software="deepmd-kit", **common)
        lammps = self.identity(software="lammps", source_sha="c" * 40, **common)
        self.assertEqual(deepmd["stack_digest"], lammps["stack_digest"])
        self.assertTrue(deepmd["build_id"].endswith("-s" + deepmd["stack_digest"][:12]))
        reversed_pair = dict(reversed(list(pair.items())))
        self.assertEqual(deepmd, self.identity(software="deepmd-kit", target="16v100-avx2",
                                              stack_sources=reversed_pair))
        new_pair = dict(pair, lammps="d" * 40)
        updated = self.identity(software="deepmd-kit", target="16v100-avx2", stack_sources=new_pair)
        self.assertNotEqual(deepmd["install_prefix"], updated["install_prefix"])
        self.assertEqual(deepmd["source_sha"], updated["source_sha"])
        self.assertEqual(contract.validate_identity(deepmd), deepmd)
        for bad in ({"deepmd-kit": "a" * 40}, dict(pair, gpumd="e" * 40),
                    dict(pair, lammps="short"), dict(pair, **{"deepmd-kit": "f" * 40})):
            with self.subTest(pair=bad), self.assertRaises(ValueError):
                self.identity(software="deepmd-kit", target="4v100-avx512", stack_sources=bad)
        with self.assertRaises(ValueError):
            self.identity(stack_sources=pair)
        changed = copy.deepcopy(deepmd)
        changed["stack_sources"]["lammps"] = "d" * 40
        with self.assertRaises(ValueError):
            contract.validate_identity(changed)


class ResolveTrackTests(unittest.TestCase):
    @staticmethod
    def result(sha, ref="v1", version="v1"):
        return {"sha": sha * 40, "ref": ref, "version": version}

    def test_resolves_live_channels_and_preserves_track_when_shas_match(self):
        resolver = Mock(side_effect=[self.result("a", "develop"), self.result("b", "v2-rc1"),
                                     self.result("c", "v1"), self.result("d", "develop")])
        first = contract.resolve_tracks("abacus", contract.TRACKS, resolver)
        second = contract.resolve_tracks("abacus", ["development"], resolver)
        self.assertEqual([call.args for call in resolver.call_args_list], [
            ("deepmodeling/abacus-develop", ref) for ref in
            ("develop", "latest-prerelease", "latest-release", "develop")])
        self.assertEqual([row["track"] for row in first], list(contract.TRACKS))
        self.assertNotEqual(first[0]["source_sha"], second[0]["source_sha"])
        same = contract.resolve_tracks("cp2k", contract.TRACKS,
                                       Mock(return_value=self.result("a", "tag")))
        self.assertEqual(len({row["track"] for row in same}), 3)
        self.assertEqual(same[0]["requested_ref"], "master")

    def test_only_explicit_absence_skips_release_channels(self):
        for track in ("prerelease", "release"):
            message = "no latest-" + track + " available"
            resolver = Mock(side_effect=ValueError(message))
            row = contract.resolve_tracks("gpumd", [track], resolver)[0]
            self.assertEqual(row["status"], "skipped")
            self.assertEqual(row["reason"], message)
            self.assertNotIn("source_sha", row)
            self.assertEqual(resolver.call_count, 1)  # No release fallback for prerelease.
        for error in (subprocess.CalledProcessError(1, ["gh", "api"]), OSError("network down"),
                      ValueError("cannot resolve tag"), ValueError("invalid JSON")):
            with self.subTest(error=error), self.assertRaises(type(error)):
                contract.resolve_tracks("cp2k", ["prerelease"], Mock(side_effect=error))
        with self.assertRaises(ValueError):
            contract.resolve_tracks("cp2k", ["development"],
                                    Mock(side_effect=ValueError("no latest-release available")))

    def test_existing_resolver_chooses_release_kind_and_published_date_not_list_order(self):
        releases = [
            {"draft": False, "prerelease": False, "published_at": "2026-05-01", "tag_name": "v2"},
            {"draft": False, "prerelease": True, "published_at": "2026-08-01", "tag_name": "v3-rc1"},
            {"draft": False, "prerelease": False, "published_at": "2025-01-01", "tag_name": "v1"},
            {"draft": True, "prerelease": False, "published_at": "2027-01-01", "tag_name": "v4"},
        ]
        def output(argv, **kwargs):
            if argv[0] == "gh":
                return json.dumps(releases)
            ref = argv[-2].removeprefix("refs/tags/")
            sha = {"master": "a", "v2": "b", "v3-rc1": "c"}[ref] * 40
            return sha + "\trefs/tags/" + ref + "\n"
        with patch.object(resolve_source.subprocess, "check_output", side_effect=output):
            rows = contract.resolve_tracks("gpumd", contract.TRACKS, resolve_source.resolve)
        self.assertEqual([row["source_ref"] for row in rows], ["master", "v3-rc1", "v2"])
        self.assertEqual([row["source_sha"] for row in rows], [letter * 40 for letter in "acb"])

    def test_invalid_track_requests_and_resolver_results_are_rejected(self):
        resolver = Mock(return_value=self.result("a"))
        for tracks in ([], "release", ["release", "release"], ["latest-release"], None):
            with self.subTest(tracks=tracks), self.assertRaises(ValueError):
                contract.resolve_tracks("cp2k", tracks, resolver)
        self.assertEqual(resolver.call_count, 0)
        for result in ({"sha": "short", "ref": "v1", "version": "v1"}, {}, None):
            with self.subTest(result=result), self.assertRaises(ValueError):
                contract.resolve_tracks("lammps", ["release"], Mock(return_value=result))


if __name__ == "__main__":
    unittest.main()
