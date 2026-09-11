import copy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
from md_tracking import identify_track_pair, resolve_track_pairs


class MdReleaseTrackTests(unittest.TestCase):
    @staticmethod
    def resolver(*, no_dp_rc=False, no_dp_release=False, same_sha=False):
        def resolve(repository, ref):
            if repository.endswith("deepmd-kit") and ((no_dp_rc and ref == "latest-prerelease") or
                                                       (no_dp_release and ref == "latest-release")):
                raise ValueError(f"no {ref} available")
            letter = ("a" if repository.endswith("deepmd-kit") else "b") if same_sha else {
                "master": "a", "develop": "b", "latest-prerelease": "c", "latest-release": "d"}[ref]
            actual_ref = {"latest-prerelease": "v2-rc1", "latest-release": "v1"}.get(ref, ref)
            return {"sha": letter * 40, "ref": actual_ref, "version": actual_ref}
        return Mock(side_effect=resolve)

    def test_three_channels_resolve_six_refs_and_deduplicate_identical_pair_triggers(self):
        resolver = self.resolver()
        result = resolve_track_pairs(resolver=resolver)
        self.assertEqual(resolver.call_count, 6)
        self.assertEqual(len(result["pairs"]), 9)
        self.assertEqual(result["skipped"], [])
        for pair in result["pairs"]:
            self.assertEqual(len(pair["triggers"]), 2)
            identities = identify_track_pair(pair, "e" * 64)
            self.assertEqual(identities["deepmd-kit"]["stack_digest"], identities["lammps"]["stack_digest"])
            self.assertIn(identities["lammps"]["partition"], ("4V100", "16V100", "8V100V0"))

    def test_lammps_rc_still_builds_when_deepmd_has_no_prerelease(self):
        result = resolve_track_pairs(resolver=self.resolver(no_dp_rc=True), targets=["4v100-avx512"])
        self.assertEqual(len(result["pairs"]), 3)
        mixed = next(pair for pair in result["pairs"] if pair["sources"]["lammps"]["track"] == "prerelease")
        self.assertEqual(mixed["sources"]["deepmd-kit"]["track"], "release")
        self.assertEqual(mixed["triggers"], [{"software": "lammps", "track": "prerelease",
                                            "companion_selection": "latest_release_fallback"}])
        identities = identify_track_pair(mixed, "e" * 64)
        self.assertIn("/deepmd-kit/release/", identities["deepmd-kit"]["install_prefix"])
        self.assertIn("/lammps/prerelease/", identities["lammps"]["install_prefix"])
        self.assertEqual(len(result["skipped"]), 1)

    def test_missing_both_partner_channels_explains_skip_without_substituting_development(self):
        result = resolve_track_pairs(["prerelease"], ["4v100-avx512"],
                                     self.resolver(no_dp_rc=True, no_dp_release=True))
        self.assertEqual(result["pairs"], [])
        self.assertEqual(len(result["skipped"]), 2)
        self.assertIn("neither prerelease nor a stable release", result["skipped"][1]["reason"])

    def test_same_commit_in_different_channels_is_not_deduplicated(self):
        result = resolve_track_pairs(targets=["4v100-avx512"], resolver=self.resolver(same_sha=True))
        self.assertEqual(len(result["pairs"]), 3)
        prefixes = {identify_track_pair(pair, "e" * 64)["lammps"]["install_prefix"] for pair in result["pairs"]}
        self.assertEqual(len(prefixes), 3)

    def test_network_and_nonabsence_errors_never_trigger_release_fallback(self):
        for error in (ValueError("cannot resolve latest-prerelease"), subprocess.CalledProcessError(1, ["gh"])):
            resolver = Mock(side_effect=error)
            with self.assertRaises(type(error)):
                resolve_track_pairs(resolver=resolver)
            self.assertEqual(resolver.call_count, 1)

    def test_invalid_tracks_targets_and_modified_plan_are_rejected(self):
        for tracks, targets in (([], ["4v100-avx512"]), (["release", "release"], ["4v100-avx512"]),
                                 (["release"], ["dsprhbm"]), (["release"], [])):
            with self.assertRaises(ValueError):
                resolve_track_pairs(tracks, targets, self.resolver())
        original = resolve_track_pairs(["release"], ["4v100-avx512"], self.resolver())["pairs"][0]
        for modify in (lambda pair: pair.update(schema=True), lambda pair: pair.update(version="wrong"),
                       lambda pair: pair["sources"]["lammps"].update(sha="f" * 40),
                       lambda pair: pair["triggers"][0].update(track="prerelease"),
                       lambda pair: pair["triggers"][0].update(companion_selection="latest_release_fallback")):
            changed = copy.deepcopy(original)
            modify(changed)
            with self.assertRaises(ValueError):
                identify_track_pair(changed, "e" * 64)


if __name__ == "__main__":
    unittest.main()
