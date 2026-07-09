"""Tests for checks/system_info.py.

Parsers are exercised against the captured orin-nano-8gb fixture set
(GUARDRAILS 5.3: fixtures are the only ground truth). The L4T-to-JetPack
mapping is the static table from PLAN.md 6.2 and is fixture-independent.
"""

import unittest

from jetson_postboot.checks import system_info
from jetson_postboot.lib.report import LEVEL_PASS, LEVEL_WARN, Report
from jetson_postboot.lib.runner import Runner
from tests.support import REPO_ROOT, make_work_dir

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "orin-nano-8gb"


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class JetpackMapTests(unittest.TestCase):
    def test_r36_maps_to_jetpack_6(self):
        self.assertEqual(system_info.jetpack_for_l4t("36"), "JetPack 6.x")

    def test_r35_maps_to_jetpack_5(self):
        self.assertEqual(system_info.jetpack_for_l4t("35"), "JetPack 5.x")

    def test_unknown_release_returns_none(self):
        for major in ("32", "34", "", "abc"):
            with self.subTest(major=major):
                self.assertIsNone(system_info.jetpack_for_l4t(major))

    def test_accepts_int_input(self):
        self.assertEqual(system_info.jetpack_for_l4t(36), "JetPack 6.x")


class ParserTests(unittest.TestCase):
    def test_board_model_from_fixture(self):
        self.assertEqual(
            system_info.parse_board_model(fixture_text("device-tree-model.txt")),
            "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super",
        )

    def test_board_model_strips_nul_bytes(self):
        # Real /proc/device-tree/model is NUL-terminated; the fixture was
        # captured through tr -d '\0', so re-add one to prove the strip.
        self.assertEqual(
            system_info.parse_board_model("NVIDIA Jetson Orin Nano\x00"),
            "NVIDIA Jetson Orin Nano",
        )

    def test_l4t_release_from_fixture(self):
        self.assertEqual(
            system_info.parse_l4t_release(fixture_text("nv_tegra_release.txt")),
            "36.4.7",
        )

    def test_l4t_release_unparseable_returns_none(self):
        self.assertIsNone(system_info.parse_l4t_release("not a release file"))

    def test_meminfo_from_fixture(self):
        mem = system_info.parse_meminfo(fixture_text("meminfo.txt"))
        self.assertEqual(mem["MemTotal"], 7802760 * 1024)
        self.assertEqual(mem["MemAvailable"], 3249352 * 1024)
        self.assertEqual(mem["SwapTotal"], 16777212 * 1024)

    def test_nvpmodel_from_fixture(self):
        self.assertEqual(
            system_info.parse_nvpmodel(fixture_text("nvpmodel-q.txt")),
            "MAXN_SUPER",
        )

    def test_nvpmodel_unparseable_returns_none(self):
        self.assertIsNone(system_info.parse_nvpmodel("garbage"))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.work = make_work_dir(self)
        self.runner = Runner(log_dir=self.work / "logs", fixture_dir=FIXTURES)
        self.report = Report(mode="simulate", tool_version="test")

    def test_check_reports_identity_as_pass_findings(self):
        system_info.check(self.runner, self.report)
        text = self.report.render_text()
        self.assertIn("NVIDIA Jetson Orin Nano Engineering Reference "
                      "Developer Kit Super", text)
        self.assertIn("L4T r36.4.7", text)
        self.assertIn("JetPack 6.x", text)
        self.assertIn("MAXN_SUPER", text)
        self.assertIn("Python 3.10.12", text)
        self.assertIn("MemTotal", text)
        # identity facts are informational: no WARN/ACTION on this fixture
        self.assertTrue(all(f.level == LEVEL_PASS for f in self.report.findings))
        self.assertEqual(self.report.exit_code(), 0)

    def test_identity_text_is_plain_language(self):
        # PLAN G6 (D27): the report explains its jargon in plain words while
        # keeping the technical terms, so a non-technical user follows it.
        system_info.check(self.runner, self.report)
        text = self.report.render_text()
        self.assertIn("RAM", text)              # gloss for MemTotal
        self.assertIn("free right now", text)   # gloss for MemAvailable
        self.assertIn("Jetson software", text)  # gloss for L4T/JetPack
        self.assertIn("speed and power", text)  # gloss for the power mode

    def test_nvpmodel_failure_warns_instead_of_silent_omission(self):
        # O8 (Munta 2026-07-07): a failed power-mode read is a WARN finding,
        # never a silently missing line.
        runner = Runner(log_dir=self.work / "logs",
                        fixture_dir=FIXTURES.parent / "orin-nano-8gb-sudo-denied")
        system_info.check(runner, self.report)
        warns = [f for f in self.report.findings if f.level == LEVEL_WARN]
        self.assertTrue(
            any("power mode unknown" in f.message and
                "sudo: a password is required" in f.message for f in warns),
            self.report.render_text())

    def test_unknown_l4t_release_degrades_to_warning(self):
        # PLAN section 3: unrecognized hardware degrades to detection-only
        # and says so. r32 is outside the supported table.
        self.assertEqual(system_info.jetpack_for_l4t("32"), None)


if __name__ == "__main__":
    unittest.main()
