import json
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

    def test_repo_injection_rejected(self):
        with self.assertRaises(ValueError):
            resolve("a/b; echo unsafe", "develop")
