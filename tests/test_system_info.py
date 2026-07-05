"""Tests for checks/system_info.py.

Phase 1 note: file parsers (board model, nv_tegra_release, meminfo) are
blocked until the orin-nano-8gb fixture set is captured (GUARDRAILS 5.3).
The L4T-to-JetPack mapping is the static table from PLAN.md 6.2 and is
fixture-independent.
"""

import unittest

from jetson_postboot.checks import system_info


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


if __name__ == "__main__":
    unittest.main()
