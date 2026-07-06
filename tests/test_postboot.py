"""End-to-end CLI tests for postboot.py, run in-process (no subprocess).

Phase 0 acceptance: --simulate against a fixture directory produces a valid
empty report and exit code 0; --apply/--undo refuse cleanly until their phase.
"""

import contextlib
import io
import json
import unittest
from pathlib import Path
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

    def test_simulate_empty_fixture_fails_fixture_completeness(self):
        # Phase 1+: checks run for real, so an empty manifest must raise
        # SimulationMissError (GUARDRAILS 5.3 fixture completeness), not
        # render an empty report as it did in Phase 0.
        code, _out, err = self.run_cli("--simulate", str(self.fixture))
        self.assertEqual(code, 2)
        self.assertIn("no fixture entry", err)

    def test_simulate_writes_report_files(self):
        base = Path(__file__).resolve().parent / "fixtures" / "orin-nano-8gb"
        self.run_cli("--simulate", str(base))
        reports = self.work / "reports"
        self.assertEqual(len(list(reports.glob("report-*.txt"))), 1)
        json_files = list(reports.glob("report-*.json"))
        self.assertEqual(len(json_files), 1)
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        self.assertTrue(data["findings"])

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
        base = Path(__file__).resolve().parent / "fixtures" / "orin-nano-8gb"
        code, out, _err = self.run_cli("--simulate", str(base), "--dry-run")
        self.assertEqual(code, 1)  # findings on the captured board
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
        def stub(runner, report, ctx):
            report.add(LEVEL_ACTION, "stub", "needs work")

        with mock.patch.object(postboot, "_CHECKS", [("stub", stub)]):
            code, out, _err = self.run_cli("--simulate", str(self.fixture))
        self.assertEqual(code, 1)
        self.assertIn("[stub]", out)
        self.assertIn("ACTION", out)
        self.assertIn("needs work", out)

    def test_check_reads_through_simulate_runner(self):
        def stub(runner, report, ctx):
            result = runner.run(["zramctl", "--bytes"])
            report.add(LEVEL_PASS, "stub", result.stdout)

        with mock.patch.object(postboot, "_CHECKS", [("stub", stub)]):
            code, out, _err = self.run_cli("--simulate", str(self.fixture))
        self.assertEqual(code, 0)
        self.assertIn("payload-zram", out)

    def test_checks_receive_context_with_backups_dir(self):
        seen = {}

        def stub(runner, report, ctx):
            seen.update(ctx)

        with mock.patch.object(postboot, "_CHECKS", [("stub", stub)]):
            self.run_cli("--simulate", str(self.fixture))
        self.assertEqual(seen["backups_dir"], self.work / "backups")

    def test_simulation_miss_in_check_exits_two(self):
        def stub(runner, report, ctx):
            runner.run(["findmnt", "-no", "SOURCE", "/"])

        with mock.patch.object(postboot, "_CHECKS", [("stub", stub)]):
            code, _out, err = self.run_cli("--simulate", str(self.fixture))
        self.assertEqual(code, 2)
        self.assertIn("no fixture entry", err)


class Phase1IntegrationTests(unittest.TestCase):
    """PLAN section 8, Phase 1 acceptance against the captured base fixture:
    swappiness 60 flagged, boot verdict (a), CUDA stack summarized, exit
    code 1 with findings. The zram-default and reclaimable-space acceptance
    numbers live in derived-variant tests (test_swap, test_storage) because
    the captured board is already tuned (PROJECT_CONTEXT O7)."""

    FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "orin-nano-8gb")

    def run_cli(self, *argv):
        work = make_work_dir(self)
        out, err = io.StringIO(), io.StringIO()
        code = postboot.main(list(argv), stdout=out, stderr=err, root=work)
        return code, out.getvalue(), err.getvalue()

    def test_full_simulate_report_meets_phase1_acceptance(self):
        code, out, err = self.run_cli("--simulate", str(self.FIXTURE))
        self.assertEqual(code, 1, err + out)  # findings present
        # system identity
        self.assertIn("NVIDIA Jetson Orin Nano", out)
        self.assertIn("L4T r36.4.7 (JetPack 6.x)", out)
        self.assertIn("MAXN_SUPER", out)
        # swap: swappiness 60 flagged as the actionable finding
        self.assertIn("vm.swappiness is 60", out)
        self.assertIn("ACTION", out)
        # storage: this board's root already spans the disk
        self.assertIn("spans", out)
        # boot verdict (a)
        self.assertIn("matches mounted root /dev/nvme0n1p1", out)
        # CUDA stack summarized
        self.assertIn("cuda-12.6", out)
        self.assertIn("libcudnn9-cuda-12", out)

    def test_all_five_modules_report(self):
        _code, out, _err = self.run_cli("--simulate", str(self.FIXTURE))
        for module in ("[system]", "[storage]", "[swap]", "[boot]", "[mlstack]"):
            self.assertIn(module, out)


if __name__ == "__main__":
    unittest.main()
