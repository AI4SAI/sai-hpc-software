"""Execute workflow resolver snippets without network or cluster submission."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import release_contract as contract


class WorkflowSourceTests(unittest.TestCase):
    def execute(self, software, *, event="workflow_dispatch", tracks="development",
                targets="dsprhbm", resume="", missing=(), failure=None):
        name = "build.yml" if software == "abacus" else "cp2k.yml"
        workflow = (ROOT / ".github/workflows" / name).read_text()
        snippet = textwrap.dedent(workflow.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0])

        def resolve(repository, ref):
            if failure:
                raise failure
            if ref in missing:
                raise ValueError(f"no {ref} available")
            # Matching commits must remain different delivery channels.
            resolved_ref = "v1-rc1" if ref == "latest-prerelease" else "v1" if ref == "latest-release" else ref
            return {"sha": "a" * 40, "version": resolved_ref + "-" + "a" * 12, "ref": resolved_ref}

        resolver = Mock(side_effect=resolve)
        original = contract.resolve_tracks
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            environment = {"GITHUB_EVENT_NAME": event, "GITHUB_OUTPUT": str(output),
                           "RELEASE_TRACKS": tracks, "TARGETS": targets, "RESUME_RUN": resume}
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(contract, "resolve_tracks", side_effect=lambda name, selected:
                                 original(name, selected, resolver)), redirect_stdout(io.StringIO()):
                exec(compile(snippet, name, "exec"), {})
            self.resolver_calls = resolver.call_args_list
            return json.loads(output.read_text().removeprefix("builds="))

    def test_both_schedules_resolve_all_three_tracks_and_four_registered_partitions(self):
        for software in ("abacus", "cp2k"):
            with self.subTest(software=software):
                rows = self.execute(software, event="schedule", tracks="ignored", targets="a100")
                self.assertEqual(len(rows), 12)
                self.assertEqual({row["target"] for row in rows}, set(contract.SOFTWARE[software]["targets"]))
                self.assertEqual([row["track"] for row in rows[::4]], list(contract.TRACKS))
                self.assertEqual([call.args[1] for call in self.resolver_calls], [
                    contract.SOFTWARE[software]["development_ref"], "latest-prerelease", "latest-release"])
                self.assertTrue(all(row["source_sha"] == "a" * 40 for row in rows))
                self.assertTrue(all({"source_ref", "source_version", "repository", "status", "requested_ref"} <= row.keys()
                                    for row in rows))

    def test_dispatch_selected_tracks_preserve_order_and_full_records(self):
        for software in ("abacus", "cp2k"):
            rows = self.execute(software, tracks="release, prerelease", targets="8v100v0-avx512, dsprhbm")
            self.assertEqual([row["track"] for row in rows], ["release", "release", "prerelease", "prerelease"])
            self.assertEqual([row["source_ref"] for row in rows], ["v1", "v1", "v1-rc1", "v1-rc1"])
            self.assertEqual(rows[0]["source_version"], "v1-" + "a" * 12)

    def test_invalid_tracks_targets_and_ambiguous_resume_are_rejected(self):
        cases = [dict(tracks="develop"), dict(tracks="latest-release"), dict(tracks="master"),
                 dict(tracks="release,release"), dict(tracks=""), dict(targets="a100"),
                 dict(targets="DSPRHBM"), dict(targets=""),
                 dict(tracks="development,release", resume="old"),
                 dict(targets="dsprhbm,4v100-avx512", resume="old")]
        for software in ("abacus", "cp2k"):
            for arguments in cases:
                with self.subTest(software=software, arguments=arguments), self.assertRaises(ValueError):
                    self.execute(software, **arguments)
            self.assertEqual(len(self.execute(software, resume="old")), 1)

    def test_only_schedule_can_skip_precisely_absent_release_channel(self):
        for software in ("abacus", "cp2k"):
            rows = self.execute(software, event="schedule", missing=("latest-prerelease",))
            self.assertEqual({row["track"] for row in rows}, {"development", "release"})
            with self.assertRaisesRegex(ValueError, "no latest-prerelease available"):
                self.execute(software, tracks="prerelease", missing=("latest-prerelease",))
            with self.assertRaisesRegex(ValueError, "network unavailable"):
                self.execute(software, event="schedule", failure=ValueError("network unavailable"))

    def test_workflows_remove_free_ref_and_forward_every_resolved_field(self):
        for name in ("build.yml", "cp2k.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            self.assertNotIn("inputs.source_ref", text)
            self.assertIn("schedule:", text)
            self.assertIn("GH_TOKEN: ${{ github.token }}", text)
            for environment, field in (("SOURCE_REF", "source_ref"), ("RELEASE_TRACK", "track"),
                                       ("SOURCE_SHA", "source_sha"), ("SOFTWARE_VERSION", "source_version")):
                self.assertIn(environment + ": ${{ matrix.build." + field + " }}", text)


if __name__ == "__main__":
    unittest.main()
