import json
import subprocess
from unittest import TestCase
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
from resolve_source import resolve

class ResolveTests(TestCase):
    def test_annotated_tag_uses_peeled_commit(self):
        refs = "a" * 40 + "\trefs/tags/v1\n" + "b" * 40 + "\trefs/tags/v1^{}\n"
        with patch("resolve_source.subprocess.check_output", return_value=refs):
            self.assertEqual(resolve("a/b", "v1")["sha"], "b" * 40)

    def test_pre_release_excludes_drafts_and_stable(self):
        releases = [
            {"draft": False, "prerelease": False, "published_at": "2026-09-08", "tag_name": "v3"},
            {"draft": True, "prerelease": True, "published_at": "2026-09-08", "tag_name": "v4"},
            {"draft": False, "prerelease": True, "published_at": "2026-09-07", "tag_name": "v2-rc"},
        ]
        refs = "c" * 40 + "\trefs/tags/v2-rc\n"
        with patch("resolve_source.subprocess.check_output", side_effect=[json.dumps(releases), refs]):
            result = resolve("a/b", "latest-prerelease")
        self.assertEqual(result["ref"], "v2-rc")
        self.assertEqual(result["sha"], "c" * 40)
        self.assertEqual(result["version"], "v2-rc-" + "c" * 12)

    def test_release_tag_beats_same_named_branch_and_annotated_tag_object(self):
        releases = [{"draft": False, "prerelease": False, "published_at": "2026-09-08",
                     "tag_name": "v3.10.0"}]
        refs = ("a" * 40 + "\trefs/heads/v3.10.0\n" +
                "b" * 40 + "\trefs/tags/v3.10.0\n" +
                "c" * 40 + "\trefs/tags/v3.10.0^{}\n")
        with patch("resolve_source.subprocess.check_output", side_effect=[json.dumps(releases), refs]):
            result = resolve("a/b", "latest-release")
        self.assertEqual(result, {"ref": "v3.10.0", "sha": "c" * 40,
                                  "version": "v3.10.0-" + "c" * 12})
        with patch("resolve_source.subprocess.check_output", return_value=refs):
            explicit = resolve("a/b", "v3.10.0")
        self.assertEqual(explicit["sha"], "a" * 40)
        self.assertEqual(explicit["version"], "v3.10.0-" + "a" * 12)

    def test_paginated_release_selection_finds_stable_after_100_prereleases(self):
        first = [{"draft": False, "prerelease": True, "published_at": "2026-09-08",
                  "tag_name": "v4-rc" + str(index)} for index in range(100)]
        second = [{"draft": False, "prerelease": False, "published_at": "2026-08-01", "tag_name": "v3"},
                  {"draft": False, "prerelease": False, "published_at": "2025-08-01", "tag_name": "v2"}]
        refs = "d" * 40 + "\trefs/tags/v3\n"
        pages = json.dumps(first) + "\n\n" + json.dumps(second) + "\n"
        with patch("resolve_source.subprocess.check_output", side_effect=[pages, refs]) as run:
            result = resolve("a/b", "latest-release")
        self.assertEqual(result["ref"], "v3")
        self.assertEqual(result["sha"], "d" * 40)
        command = run.call_args_list[0].args[0]
        self.assertIn("--paginate", command)
        self.assertNotIn("--slurp", command)  # gh 2.45 compatibility.

    def test_prerelease_on_later_page_is_selected_independently_of_stable(self):
        pages = [[{"draft": False, "prerelease": False, "published_at": "2027-01-01", "tag_name": "v5"}],
                 [{"draft": False, "prerelease": True, "published_at": "2026-07-01", "tag_name": "v4-rc2"},
                  {"draft": False, "prerelease": True, "published_at": "2026-06-01", "tag_name": "v4-rc1"}]]
        refs = "e" * 40 + "\trefs/tags/v4-rc2\n"
        with patch("resolve_source.subprocess.check_output", side_effect=["\n".join(map(json.dumps, pages)), refs]):
            self.assertEqual(resolve("a/b", "latest-prerelease")["ref"], "v4-rc2")

    def test_release_cannot_fall_back_to_branch_or_sha_named_tag(self):
        for tag in ("v1", "a" * 40):
            pages = [{"draft": False, "prerelease": False, "published_at": "2026-09-08", "tag_name": tag}]
            branch_only = "b" * 40 + "\trefs/heads/" + tag + "\n"
            with self.subTest(tag=tag), patch("resolve_source.subprocess.check_output", side_effect=[json.dumps(pages), branch_only]):
                with self.assertRaisesRegex(ValueError, "cannot resolve"):
                    resolve("a/b", "latest-release")

    def test_ordinary_branch_and_exact_sha_keep_the_existing_result_shape(self):
        with patch("resolve_source.subprocess.check_output", return_value="f" * 40 + "\trefs/heads/develop\n"):
            self.assertEqual(resolve("a/b", "develop"),
                             {"ref": "develop", "sha": "f" * 40, "version": "develop-" + "f" * 12})
        with patch("resolve_source.subprocess.check_output", return_value=""):
            self.assertEqual(resolve("a/b", "a" * 40)["sha"], "a" * 40)

    def test_only_empty_channels_report_absence_and_api_errors_propagate(self):
        for channel in ("latest-release", "latest-prerelease"):
            with patch("resolve_source.subprocess.check_output", return_value="[]\n[]\n"):
                with self.assertRaisesRegex(ValueError, "^no " + channel + " available$"):
                    resolve("a/b", channel)
        for reply in ('{"message":"API rate limit exceeded"}', '[{"draft":false}]', '[]\n{}', 'not json', '', '[]\n{"truncated":'):
            with self.subTest(reply=reply), patch("resolve_source.subprocess.check_output", return_value=reply):
                with self.assertRaises(ValueError) as raised:
                    resolve("a/b", "latest-release")
                self.assertNotEqual(str(raised.exception), "no latest-release available")
        failure = subprocess.CalledProcessError(1, ["gh", "api", "--paginate"])
        with patch("resolve_source.subprocess.check_output", side_effect=failure):
            with self.assertRaises(subprocess.CalledProcessError):
                resolve("a/b", "latest-release")

    def test_repo_injection_rejected(self):
        with self.assertRaises(ValueError):
            resolve("a/b; echo unsafe", "develop")
