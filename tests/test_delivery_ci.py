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
            return json.loads(dict(line.split("=", 1) for line in output.read_text().splitlines())["builds"])

    def test_abacus_schedule_resolves_all_three_tracks_and_four_registered_partitions(self):
        for software in ("abacus",):
            with self.subTest(software=software):
                rows = self.execute(software, event="schedule", tracks=",".join(contract.TRACKS), targets=",".join(contract.PARTITIONS))
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
                rows = self.execute(software, event="schedule", tracks=",".join(contract.TRACKS), missing=("latest-prerelease",))
                self.assertEqual({row["track"] for row in rows}, {"development", "release"})
            self.assertEqual(self.execute(software, tracks="prerelease", missing=("latest-prerelease",)), [])
            rows = self.execute(software, tracks="development,prerelease,release", missing=("latest-prerelease",))
            self.assertEqual({row["track"] for row in rows}, {"development", "release"})
            with self.assertRaisesRegex(ValueError, "network unavailable"):
                self.execute(software, failure=ValueError("network unavailable"))

    def test_cp2k_reusable_workflow_accepts_schedule(self):
        text = (ROOT / ".github/workflows/cp2k.yml").read_text()
        self.assertIn("workflow_call:", text)
        self.assertNotIn("schedule:", text)
        self.assertEqual(len(self.execute("cp2k", event="schedule")), 1)

    def test_workflows_remove_free_ref_and_forward_every_resolved_field(self):
        for name in ("build.yml", "cp2k.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            self.assertNotIn("inputs.source_ref", text)
            self.assertNotIn("schedule:", text)
            self.assertIn("GH_TOKEN: ${{ github.token }}", text)
            self.assertIn('uses: ./.github/workflows/software-pair.yml', text)
            self.assertIn('build: ${{ toJSON(matrix.build) }}', text)
            pair = (ROOT / '.github/workflows/software-pair.yml').read_text()
            for environment, field in (("SOURCE_REF", "source_ref"), ("RELEASE_TRACK", "track"),
                                       ("SOURCE_SHA", "source_sha"), ("SOFTWARE_VERSION", "source_version")):
                self.assertIn(environment + ": ${{ fromJSON(inputs.build)." + field + " }}", pair)


class DailyWorkflowTests(unittest.TestCase):
    def test_software_partition_selection(self):
        from daily_plan import plan
        rows = plan()
        self.assertEqual(set(rows['abacus'].split(',')), set(contract.PARTITIONS))
        self.assertNotIn('dsprhbm', rows['deepmd_kit'])
        self.assertEqual(plan('lammps', targets='16v100-avx2')['deepmd_kit'], '16v100-avx2')
        for kwargs in ({'software': 'bad'}, {'software': 'gpumd', 'targets': 'dsprhbm'},
                       {'targets': 'dsprhbm,dsprhbm'}, {'tracks': 'nightly'}):
            with self.assertRaises(ValueError):
                plan(**kwargs)

    def test_reusable_workflows_and_result_propagation(self):
        import yaml
        data = yaml.safe_load((ROOT / '.github/workflows/daily.yml').read_text())
        jobs = data['jobs']
        self.assertEqual(set(jobs['result']['needs']), {'plan','abacus','cp2k','deepmd_kit','gpumd'})
        self.assertEqual(jobs['result']['if'], 'always()')
        for name in ('abacus', 'cp2k', 'deepmd_kit', 'gpumd'):
            workflow = ROOT / jobs[name]['uses'].removeprefix('./')
            called = yaml.safe_load(workflow.read_text())
            self.assertIn('workflow_call', called[True])
        self.assertNotIn('/dispatches', (ROOT / '.github/workflows/daily.yml').read_text())
        snippet = jobs['result']['steps'][0]['run'].split("python3 - <<'PY'\n")[1].rsplit('\nPY',1)[0]
        for outcome in ('success', 'failure', 'cancelled', 'skipped'):
            results = {name: {'result': 'skipped'} for name in ('abacus','cp2k','deepmd_kit','gpumd')}
            results.update(plan={'result': 'success', 'outputs': {'abacus':'dsprhbm'}}, abacus={'result':outcome})
            with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, RESULTS=json.dumps(results), GITHUB_STEP_SUMMARY=tmp+'/summary'):
                if outcome == 'success':
                    exec(snippet, {})
                else:
                    with self.assertRaises(SystemExit): exec(snippet, {})


if __name__ == '__main__':
    unittest.main()
