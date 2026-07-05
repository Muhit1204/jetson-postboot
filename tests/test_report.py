"""Tests for lib/report.py: terminal, txt, and json report rendering."""

import json
import unittest

from jetson_postboot.lib.report import LEVEL_ACTION, LEVEL_PASS, LEVEL_WARN, Report
from tests.support import make_work_dir


class ReportTests(unittest.TestCase):
    def make_report(self):
        return Report(mode="simulate", tool_version="0.1.0.dev0")

    def test_empty_report_is_valid_and_clean(self):
        report = self.make_report()
        text = report.render_text()
        self.assertIn("jetson-postboot 0.1.0.dev0", text)
        self.assertIn("mode: simulate", text)
        self.assertIn("(no findings recorded)", text)
        self.assertIn("summary: 0 pass, 0 warn, 0 action", text)
        self.assertEqual(report.exit_code(), 0)

    def test_pass_only_exits_zero(self):
        report = self.make_report()
        report.add(LEVEL_PASS, "system", "Board: NVIDIA Jetson Orin Nano")
        self.assertEqual(report.exit_code(), 0)
        self.assertIn("PASS", report.render_text())

    def test_warn_or_action_exits_one(self):
        for level in (LEVEL_WARN, LEVEL_ACTION):
            with self.subTest(level=level):
                report = self.make_report()
                report.add(level, "swap", "swappiness is 60")
                self.assertEqual(report.exit_code(), 1)

    def test_findings_grouped_by_module_with_details(self):
        report = self.make_report()
        report.add(LEVEL_WARN, "swap", "swappiness is 60", details="recommended: 10")
        report.add(LEVEL_PASS, "storage", "no trailing free space")
        text = report.render_text()
        self.assertIn("[swap]", text)
        self.assertIn("[storage]", text)
        self.assertIn("recommended: 10", text)

    def test_invalid_level_rejected(self):
        with self.assertRaises(ValueError):
            self.make_report().add("FATAL", "swap", "boom")

    def test_save_writes_txt_and_json(self):
        work = make_work_dir(self)
        report = self.make_report()
        report.add(LEVEL_ACTION, "storage", "430 GiB reclaimable")
        txt_path, json_path = report.save(work / "reports")
        self.assertTrue(txt_path.is_file())
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(data["tool"], "jetson-postboot")
        self.assertEqual(data["mode"], "simulate")
        self.assertEqual(data["summary"]["action"], 1)
        self.assertEqual(data["findings"][0]["module"], "storage")
        self.assertEqual(data["findings"][0]["level"], "ACTION")


if __name__ == "__main__":
    unittest.main()
