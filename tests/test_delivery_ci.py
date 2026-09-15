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

    def test_abacus_schedule_resolves_all_three_tracks_and_four_registered_partitions(self):
        for software in ("abacus",):
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

    def test_both_triggers_skip_precisely_absent_release_channels(self):
        for software in ("abacus", "cp2k"):
            if software == "abacus":
                rows = self.execute(software, event="schedule", missing=("latest-prerelease",))
                self.assertEqual({row["track"] for row in rows}, {"development", "release"})
            self.assertEqual(self.execute(software, tracks="prerelease", missing=("latest-prerelease",)), [])
            rows = self.execute(software, tracks="development,prerelease,release", missing=("latest-prerelease",))
            self.assertEqual({row["track"] for row in rows}, {"development", "release"})
            with self.assertRaisesRegex(ValueError, "network unavailable"):
                self.execute(software, failure=ValueError("network unavailable"))

    def test_cp2k_workflow_only_manual_candidates_and_schedule_is_absent(self):
        text = (ROOT / ".github/workflows/cp2k.yml").read_text()
        self.assertNotIn("schedule:", text)
        self.assertNotIn("event_name == 'schedule'", text)
        self.assertIn("needs.resolve-source.outputs.builds != '[]'", text)
        for event in ("schedule", "push", "pull_request"):
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, "manual candidates"):
                self.execute("cp2k", event=event)

    def test_workflows_remove_free_ref_and_forward_every_resolved_field(self):
        for name in ("build.yml", "cp2k.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            self.assertNotIn("inputs.source_ref", text)
            self.assertNotIn("schedule:", text)
            self.assertIn("GH_TOKEN: ${{ github.token }}", text)
            for environment, field in (("SOURCE_REF", "source_ref"), ("RELEASE_TRACK", "track"),
                                       ("SOURCE_SHA", "source_sha"), ("SOFTWARE_VERSION", "source_version")):
                self.assertIn(environment + ": ${{ matrix.build." + field + " }}", text)


class DailyWorkflowTests(unittest.TestCase):
    def execute(self, index=0, **environment):
        workflow = (ROOT / '.github/workflows/daily.yml').read_text()
        snippet = textwrap.dedent(workflow.split("python3 - <<'PY'\n")[index + 1].split('\n          PY', 1)[0])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output'
            summary = Path(temporary) / 'summary'
            env = dict(SOFTWARE='all', TRACKS=','.join(contract.TRACKS), TARGETS='all',
                       RETRY_RELEASES='', GITHUB_EVENT_NAME='schedule', GITHUB_OUTPUT=str(output),
                       GITHUB_STEP_SUMMARY=str(summary))
            env.update(environment)
            with patch.dict(os.environ, env, clear=True), patch('subprocess.run') as run, redirect_stdout(io.StringIO()):
                exec(compile(snippet, 'daily.yml', 'exec'), {})
            self.calls = run.call_args_list
            self.summary = summary.read_text() if summary.exists() else ''
            return json.loads(output.read_text().removeprefix('requests=')) if output.exists() else None

    def test_daily_dispatches_five_programs_as_four_independent_workflows(self):
        rows = self.execute()
        self.assertEqual([row['workflow'] for row in rows], ['build.yml', 'cp2k.yml', 'deepmd-lammps.yml', 'gpumd.yml'])
        self.assertEqual(len({row['ref'] for row in rows}), 4)
        for row in rows:
            self.assertEqual(row['inputs']['tracks'], ','.join(contract.TRACKS))
            self.assertEqual(row['inputs']['retry_releases'], 'false')
            self.assertEqual(row['inputs']['targets'].split(','), list(contract.SOFTWARE[row['software']]['targets']))
        self.assertEqual(rows[2]['inputs']['software'], 'all')
        self.assertEqual(rows[2]['inputs']['build_candidates'], 'true')
        self.assertNotIn('build_candidates', rows[3]['inputs'])

    def test_manual_single_software_and_retry_are_forwarded(self):
        for software in contract.SOFTWARE:
            with self.subTest(software=software):
                rows = self.execute(SOFTWARE=software, TARGETS='4v100-avx512', TRACKS='release',
                                    GITHUB_EVENT_NAME='workflow_dispatch', RETRY_RELEASES='true')
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['inputs']['tracks'], 'release')
                self.assertEqual(rows[0]['inputs']['targets'], '4v100-avx512')
                self.assertEqual(rows[0]['inputs']['retry_releases'], 'true')
                if software in ('deepmd-kit', 'lammps'):
                    self.assertEqual(rows[0]['inputs']['software'], software)
        self.assertTrue(all(row['inputs']['retry_releases'] == 'false' for row in self.execute(RETRY_RELEASES='true')))

    def test_cpu_selection_omits_gpu_only_programs_and_invalid_inputs_fail(self):
        self.assertEqual([row['software'] for row in self.execute(TARGETS='dsprhbm')], ['abacus', 'cp2k'])
        for env in ({'SOFTWARE': 'other'}, {'SOFTWARE': 'lammps', 'TARGETS': 'dsprhbm'},
                    {'TARGETS': '4V100'}, {'TARGETS': 'a100'}, {'TARGETS': 'dsprhbm,dsprhbm'},
                    {'TRACKS': 'release,release'}, {'TRACKS': ''}, {'TRACKS': 'main'}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                self.execute(**env)

    def test_dispatch_uses_only_this_repo_and_reports_submission_not_completion(self):
        request = self.execute(SOFTWARE='gpumd')[0]
        self.execute(index=1, REQUEST=json.dumps(request))
        call = self.calls[0]
        self.assertEqual(call.args[0], ['gh', 'api', '--method', 'POST',
            'repos/AI4SAI/sai-hpc-software/actions/workflows/gpumd.yml/dispatches', '--input', '-'])
        self.assertEqual(json.loads(call.kwargs['input']), {'ref': request['ref'], 'inputs': request['inputs']})
        self.assertTrue(call.kwargs['check'])
        self.assertIn('Dispatch accepted only', self.summary)
        workflow = (ROOT / '.github/workflows/daily.yml').read_text()
        self.assertIn('actions: write', workflow)
        self.assertNotIn('secrets.REMOTE_SSH', workflow)
        self.assertNotIn('cancel-in-progress: true', workflow)


if __name__ == "__main__":
    unittest.main()
