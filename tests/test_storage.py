"""Tests for modules/storage.py: Tier 0 detection + Tier 3 advisory only.

Three fixture sets drive the three outcomes (PLAN 6.3 acceptance):
- orin-nano-8gb (captured): root already spans the disk -> PASS, no advisory.
- orin-nano-8gb-small-root (derived): ~430 GB reclaimable -> ACTION with the
  exact manual command sequence.
- orin-nano-8gb-partition-after-root (derived): failed precondition -> WARN
  explaining which one, no command sequence.
The module never mutates; tests/test_guards.py enforces that structurally.
"""

import unittest

from jetson_postboot.lib.report import (LEVEL_ACTION, LEVEL_PASS,
                                        LEVEL_WARN, Report)
from jetson_postboot.lib.runner import Runner
from jetson_postboot.modules import storage
from tests.support import REPO_ROOT, make_work_dir

FIXTURES = REPO_ROOT / "tests" / "fixtures"
BASE = FIXTURES / "orin-nano-8gb"


def fixture_text(name):
    return (BASE / name).read_text(encoding="utf-8")


class SplitPartitionTests(unittest.TestCase):
    def test_nvme_naming(self):
        self.assertEqual(storage.split_partition("/dev/nvme0n1p1"),
                         ("/dev/nvme0n1", 1))

    def test_mmcblk_naming(self):
        self.assertEqual(storage.split_partition("/dev/mmcblk0p2"),
                         ("/dev/mmcblk0", 2))

    def test_sd_naming(self):
        self.assertEqual(storage.split_partition("/dev/sda3"), ("/dev/sda", 3))

    def test_non_partition_returns_none(self):
        self.assertIsNone(storage.split_partition("/dev/nvme0n1"))


class SfdiskDumpParserTests(unittest.TestCase):
    def test_base_fixture_geometry(self):
        dump = storage.parse_sfdisk_dump(fixture_text("sfdisk-dump.txt"))
        self.assertEqual(dump["last_lba"], 976773134)
        self.assertEqual(dump["sector_size"], 512)
        self.assertEqual(len(dump["partitions"]), 15)
        p1 = dump["partitions"]["/dev/nvme0n1p1"]
        self.assertEqual(p1["start"], 3057664)
        self.assertEqual(p1["size"], 973715456)


class TrailingGapTests(unittest.TestCase):
    def test_base_fixture_gap_is_under_one_gib(self):
        dump = storage.parse_sfdisk_dump(fixture_text("sfdisk-dump.txt"))
        gap = storage.trailing_gap_bytes(dump, "/dev/nvme0n1p1")
        self.assertEqual(gap, 15 * 512)  # root ends 15 sectors shy of last-lba


class AdvisoryTextTests(unittest.TestCase):
    def test_sequence_includes_sgdisk_only_when_header_misplaced(self):
        with_move = storage.advisory_sequence(
            "/dev/nvme0n1", 1, "/dev/nvme0n1p1", header_at_end=False)
        without = storage.advisory_sequence(
            "/dev/nvme0n1", 1, "/dev/nvme0n1p1", header_at_end=True)
        self.assertIn("sgdisk -e /dev/nvme0n1", with_move)
        self.assertNotIn("sgdisk -e", without)
        for text in (with_move, without):
            self.assertIn("sfdisk -d /dev/nvme0n1", text)
            self.assertIn("growpart /dev/nvme0n1 1", text)
            self.assertIn("resize2fs /dev/nvme0n1p1", text)
            self.assertIn("e2fsck", text)  # the never-on-mounted warning
            self.assertIn("cloud-guest-utils", text)


class CheckTests(unittest.TestCase):
    def run_check(self, fixture_dir):
        work = make_work_dir(self)
        runner = Runner(log_dir=work / "logs", fixture_dir=fixture_dir)
        report = Report(mode="simulate", tool_version="test")
        storage.check(runner, report)
        return report

    def test_base_fixture_root_spans_disk(self):
        report = self.run_check(BASE)
        self.assertEqual(report.exit_code(), 0, report.render_text())
        text = report.render_text()
        self.assertIn("spans", text)
        self.assertTrue(all(f.level == LEVEL_PASS for f in report.findings))

    def test_small_root_fires_advisory_with_exact_sequence(self):
        report = self.run_check(FIXTURES / "orin-nano-8gb-small-root")
        text = report.render_text()
        actions = [f for f in report.findings if f.level == LEVEL_ACTION]
        self.assertEqual(len(actions), 1, text)
        self.assertIn("429.8 GB", actions[0].message)
        details = actions[0].details or ""
        self.assertIn("sudo sfdisk -d /dev/nvme0n1", details)
        self.assertIn("sudo growpart /dev/nvme0n1 1", details)
        self.assertIn("sudo resize2fs /dev/nvme0n1p1", details)
        self.assertNotIn("sgdisk -e", details)  # header already at disk end
        self.assertEqual(report.exit_code(), 1)

    def test_sfdisk_failure_reports_unreadable_table_not_absence(self):
        report = self.run_check(FIXTURES / "orin-nano-8gb-sudo-denied")
        text = report.render_text()
        warns = [f for f in report.findings if f.level == LEVEL_WARN]
        self.assertTrue(
            any("could not read" in f.message and "sudo: a password is required"
                in f.message for f in warns), text)
        self.assertNotIn("not found in", text)

    def test_partition_after_root_fails_precondition_no_commands(self):
        report = self.run_check(FIXTURES / "orin-nano-8gb-partition-after-root")
        text = report.render_text()
        warns = [f for f in report.findings if f.level == LEVEL_WARN]
        self.assertTrue(any("nvme0n1p16" in f.message for f in warns), text)
        self.assertNotIn("growpart", text)
        self.assertNotIn("resize2fs", text)


if __name__ == "__main__":
    unittest.main()
