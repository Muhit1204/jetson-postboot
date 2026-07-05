"""End-to-end CLI tests for postboot.py, run in-process (no subprocess).

Phase 0 acceptance: --simulate against a fixture directory produces a valid
empty report and exit code 0; --apply/--undo refuse cleanly until their phase.
"""

import contextlib
import io
import json
import unittest
from unittest import mock

import postboot
from jetson_postboot.lib.report import LEVEL_ACTION, LEVEL_PASS
from tests.support import make_work_dir


class PostbootCliTests(unittest.TestCase):
    def setUp(self):
        self.work = make_work_dir(self)
        self.fixture = self.work / "fixture"
        self.fixture.mkdir()
        (self.fixture / "manifest.json").write_text(
            '{"commands": {}, "files": {}}', encoding="utf-8"
        )

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = postboot.main(list(argv), stdout=out, stderr=err, root=self.work)
        return code, out.getvalue(), err.getvalue()

    def test_simulate_empty_fixture_produces_clean_report(self):
        code, out, err = self.run_cli("--simulate", str(self.fixture))
        self.assertEqual(code, 0, err)
        self.assertIn("jetson-postboot", out)
        self.assertIn("mode: simulate", out)
        self.assertIn("summary: 0 pass, 0 warn, 0 action", out)

    def test_simulate_writes_report_files(self):
        self.run_cli("--simulate", str(self.fixture))
        reports = self.work / "reports"
        self.assertEqual(len(list(reports.glob("report-*.txt"))), 1)
        json_files = list(reports.glob("report-*.json"))
        self.assertEqual(len(json_files), 1)
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        self.assertEqual(data["findings"], [])

    def test_real_mode_phase0_runs_without_commands(self):
        code, out, _err = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("mode: real", out)

    def test_apply_not_implemented_yet(self):
        code, _out, err = self.run_cli(
            "--simulate", str(self.fixture), "--apply", "swap"
        )
        self.assertEqual(code, 2)
        self.assertIn("not implemented", err)
        self.assertIn("Phase 2", err)

    def test_apply_storage_is_no_longer_a_valid_choice(self):
        # v1.1: storage is Tier 3 advisory-only forever, no apply mode exists.
        # argparse must reject "storage" before our dispatch code ever runs
        # (proven by our custom stderr staying empty: argparse writes its
        # error to real stderr, not the stderr= we pass into main()).
        with contextlib.redirect_stderr(io.StringIO()):
            code, _out, err = self.run_cli("--apply", "storage")
        self.assertEqual(code, 2)
        self.assertEqual(err, "")

    def test_undo_not_implemented_yet(self):
        code, _out, err = self.run_cli(
            "--simulate", str(self.fixture), "--undo", "swap"
        )
        self.assertEqual(code, 2)
        self.assertIn("not implemented", err)

    def test_simulate_dir_without_manifest_fails_cleanly(self):
        code, _out, err = self.run_cli("--simulate", str(self.work / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("manifest.json", err)

    def test_apply_and_undo_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()):
            code, _out, _err = self.run_cli("--apply", "swap", "--undo", "swap")
        self.assertEqual(code, 2)

    def test_dry_run_flag_reflected_in_mode(self):
        code, out, _err = self.run_cli("--simulate", str(self.fixture), "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("mode: simulate+dry-run", out)


class CheckRegistryTests(unittest.TestCase):
    """Phase 1 orchestration: registered checks run against the runner and
    their findings drive report content and exit code."""

    def setUp(self):
        self.work = make_work_dir(self)
        self.fixture = self.work / "fixture"
        self.fixture.mkdir()
        (self.fixture / "zram.txt").write_text("payload-zram", encoding="utf-8")
        (self.fixture / "manifest.json").write_text(
            json.dumps({"commands": {"zramctl --bytes": "zram.txt"}, "files": {}}),
            encoding="utf-8",
        )

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        code = postboot.main(list(argv), stdout=out, stderr=err, root=self.work)
        return code, out.getvalue(), err.getvalue()

    def test_action_finding_drives_exit_one(self):
        def stub(runner, report):
            report.add(LEVEL_ACTION, "stub", "needs work")

        with mock.patch.object(postboot, "_CHECKS", [("stub", stub)]):
            code, out, _err = self.run_cli("--simulate", str(self.fixture))
        self.assertEqual(code, 1)
        self.assertIn("[stub]", out)
        self.assertIn("ACTION", out)
        self.assertIn("needs work", out)

    def test_check_reads_through_simulate_runner(self):
        def stub(runner, report):
            result = runner.run(["zramctl", "--bytes"])
            report.add(LEVEL_PASS, "stub", result.stdout)

        with mock.patch.object(postboot, "_CHECKS", [("stub", stub)]):
            code, out, _err = self.run_cli("--simulate", str(self.fixture))
        self.assertEqual(code, 0)
        self.assertIn("payload-zram", out)

    def test_simulation_miss_in_check_exits_two(self):
        def stub(runner, report):
            runner.run(["findmnt", "-no", "SOURCE", "/"])

        with mock.patch.object(postboot, "_CHECKS", [("stub", stub)]):
            code, _out, err = self.run_cli("--simulate", str(self.fixture))
        self.assertEqual(code, 2)
        self.assertIn("no fixture entry", err)


if __name__ == "__main__":
    unittest.main()
