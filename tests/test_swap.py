"""Tests for modules/swap.py Tier 0 detection (Phase 1).

Detection parsers run against the captured orin-nano-8gb fixtures. That
board is already hand-tuned (zram removed, a 16 GiB /16GB.swap active,
nvzramconfig disabled) but still has swappiness 60, so the base fixture
exercises the swappiness finding. The populated-zramctl path will gain a
captured fixture with the untuned-default variant (PROJECT_CONTEXT O7);
until then its parser is covered by a synthetic util-linux-shaped table,
which tests parser mechanics, not Jetson ground truth.
"""

import unittest

from jetson_postboot.lib.report import LEVEL_ACTION, Report
from jetson_postboot.lib.runner import Runner
from jetson_postboot.modules import swap
from tests.support import REPO_ROOT, make_work_dir

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "orin-nano-8gb"


def fixture_text(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class SwaponParserTests(unittest.TestCase):
    def test_fixture_swapfile_parsed(self):
        entries = swap.parse_swapon(fixture_text("swapon-show.txt"))
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["name"], "/16GB.swap")
        self.assertEqual(entry["type"], "file")
        self.assertEqual(entry["size"], 17179865088)
        self.assertEqual(entry["used"], 0)

    def test_empty_output_means_no_swap(self):
        self.assertEqual(swap.parse_swapon(""), [])


class ZramctlParserTests(unittest.TestCase):
    def test_fixture_empty_output_means_no_devices(self):
        # Captured board has zram removed: zramctl printed nothing.
        self.assertEqual(swap.parse_zramctl(fixture_text("zramctl.txt")), [])
        self.assertEqual(swap.parse_zramctl(""), [])

    def test_synthetic_table_shape(self):
        # util-linux table shape only; replace with the untuned-default
        # captured fixture when it lands (PROJECT_CONTEXT O7).
        text = (
            "NAME       ALGORITHM DISKSIZE  DATA COMPR TOTAL STREAMS MOUNTPOINT\n"
            "/dev/zram0 lzo-rle   994050048     0     0     0       6 [SWAP]\n"
            "/dev/zram1 lzo-rle   994050048     0     0     0       6 [SWAP]\n"
        )
        devices = swap.parse_zramctl(text)
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0]["name"], "/dev/zram0")
        self.assertEqual(devices[0]["disksize"], 994050048)
        self.assertEqual(swap.zram_total(devices), 2 * 994050048)


class DefaultZramHeuristicTests(unittest.TestCase):
    def test_half_of_ram_is_flagged_as_jetpack_default(self):
        mem_total = 7802760 * 1024
        self.assertTrue(swap.zram_at_jetpack_default(mem_total // 2, mem_total))

    def test_zero_zram_is_not_at_default(self):
        self.assertFalse(swap.zram_at_jetpack_default(0, 7802760 * 1024))

    def test_small_zram_is_not_at_default(self):
        mem_total = 7802760 * 1024
        self.assertFalse(swap.zram_at_jetpack_default(mem_total // 8, mem_total))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.work = make_work_dir(self)
        self.runner = Runner(log_dir=self.work / "logs", fixture_dir=FIXTURES)
        self.report = Report(mode="simulate", tool_version="test")

    def test_swappiness_60_produces_action_finding(self):
        swap.check(self.runner, self.report)
        actions = [f for f in self.report.findings if f.level == LEVEL_ACTION]
        self.assertTrue(any("swappiness" in f.message for f in actions),
                        self.report.render_text())
        self.assertIn("60", self.report.render_text())
        self.assertEqual(self.report.exit_code(), 1)

    def test_existing_swapfile_and_disabled_zram_reported(self):
        swap.check(self.runner, self.report)
        text = self.report.render_text()
        self.assertIn("/16GB.swap", text)
        self.assertIn("nvzramconfig", text)
        self.assertIn("disabled", text)
        # zram absent on this board: must not fire the default-zram finding
        self.assertNotIn("JetPack default", text)


if __name__ == "__main__":
    unittest.main()
