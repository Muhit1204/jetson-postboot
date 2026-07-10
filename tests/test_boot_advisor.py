"""Tests for modules/boot_advisor.py (Tier 3, read-only forever).

Four fixture sets drive the verdicts (PLAN 6.5 acceptance):
- orin-nano-8gb (captured): configured root matches mounted root -> (a).
- orin-nano-8gb-root-on-sd (derived): root on SD, larger NVMe idle -> (b),
  migration advisory.
- orin-nano-8gb-root-mismatch (derived): configured UUID resolves nowhere
  -> (c), suggested extlinux line + extlinux.conf copied to backups.
- orin-nano-8gb-bootorder-stale (derived): root on NVMe but the firmware
  boot order still tries an SD entry first -> stale-boot-order WARN with
  the exact efibootmgr -o line, nothing executed.
tests/test_guards.py asserts this module never passes mutate= to the runner.
"""

import json
import unittest

from jetson_postboot.lib.report import LEVEL_PASS, LEVEL_WARN, Report
from jetson_postboot.lib.runner import Runner
from jetson_postboot.modules import boot_advisor
from tests.support import REPO_ROOT, make_work_dir

FIXTURES = REPO_ROOT / "tests" / "fixtures"
BASE = FIXTURES / "orin-nano-8gb"


def fixture_text(name):
    return (BASE / name).read_text(encoding="utf-8")


class ConfiguredRootParserTests(unittest.TestCase):
    def test_fixture_uuid_form(self):
        kind, value = boot_advisor.parse_configured_root(
            fixture_text("extlinux.conf.txt"))
        self.assertEqual(kind, "UUID")
        self.assertEqual(value, "52d10c20-4c4e-4ea4-8a00-d5ccdc19c122")

    def test_device_form(self):
        text = "LABEL primary\n  APPEND ${cbootargs} root=/dev/nvme0n1p1 rw\n"
        self.assertEqual(boot_advisor.parse_configured_root(text),
                         ("DEV", "/dev/nvme0n1p1"))

    def test_partuuid_form(self):
        text = "  APPEND quiet root=PARTUUID=abcd-12 rw rootwait\n"
        self.assertEqual(boot_advisor.parse_configured_root(text),
                         ("PARTUUID", "abcd-12"))

    def test_missing_root_returns_none(self):
        self.assertIsNone(boot_advisor.parse_configured_root("TIMEOUT 30\n"))


class SuggestedLineTests(unittest.TestCase):
    def test_root_token_replaced_others_kept(self):
        line = ("      APPEND ${cbootargs} root=UUID=dead-beef rw rootwait "
                "rootfstype=ext4 console=tty0")
        suggested = boot_advisor.suggested_append_line(line, "/dev/nvme0n1p1")
        self.assertIn("root=/dev/nvme0n1p1", suggested)
        self.assertNotIn("dead-beef", suggested)
        self.assertIn("rootfstype=ext4", suggested)


STALE_EFIBOOTMGR = """\
BootCurrent: 0008
Timeout: 5 seconds
BootOrder: 0001,0008,0004
Boot0000* Enter Setup
Boot0001* UEFI SD Device
Boot0004* UEFI HTTPv4 (MAC:4CBB47C8BEB6)
Boot0008* UEFI WD Green SN3000 500GB 254587801140 1
"""


class EfibootmgrParserTests(unittest.TestCase):
    def test_captured_fixture_parses(self):
        parsed = boot_advisor.parse_efibootmgr(fixture_text("efibootmgr.txt"))
        self.assertEqual(parsed["current"], "0008")
        self.assertEqual(parsed["order"][0], "0008")
        self.assertEqual(len(parsed["order"]), 8)
        self.assertIn("WD Green SN3000", parsed["entries"]["0008"])

    def test_missing_pieces_degrade_to_none_and_empty(self):
        parsed = boot_advisor.parse_efibootmgr("Timeout: 5 seconds\n")
        self.assertIsNone(parsed["current"])
        self.assertEqual(parsed["order"], [])
        self.assertEqual(parsed["entries"], {})

    def test_captured_fixture_has_no_stale_entries(self):
        # BootCurrent leads BootOrder on the captured board: healthy.
        parsed = boot_advisor.parse_efibootmgr(fixture_text("efibootmgr.txt"))
        self.assertEqual(boot_advisor.stale_entries_before_current(parsed), [])

    def test_sd_entry_before_current_is_detected(self):
        parsed = boot_advisor.parse_efibootmgr(STALE_EFIBOOTMGR)
        self.assertEqual(boot_advisor.stale_entries_before_current(parsed),
                         [("0001", "UEFI SD Device")])

    def test_non_removable_entries_before_current_are_ignored(self):
        # PXE/HTTP network entries time out and fall through; only SD/MMC/USB
        # media can silently boot a stale system.
        text = STALE_EFIBOOTMGR.replace("0001,0008,0004", "0004,0008,0001")
        parsed = boot_advisor.parse_efibootmgr(text)
        self.assertEqual(boot_advisor.stale_entries_before_current(parsed), [])

    def test_drive_model_letters_do_not_false_match(self):
        # "WD Green SN3000" must not trip the SD/MMC/USB label matcher.
        self.assertIsNone(boot_advisor._REMOVABLE_LABEL_RE.search(
            "UEFI WD Green SN3000 500GB 254587801140 1"))

    def test_suggested_order_moves_current_first(self):
        parsed = boot_advisor.parse_efibootmgr(STALE_EFIBOOTMGR)
        self.assertEqual(boot_advisor.suggested_boot_order(parsed),
                         ["0008", "0001", "0004"])


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.work = make_work_dir(self)

    def make_runner(self, commands, files=None):
        fixture = self.work / "fixture"
        fixture.mkdir(exist_ok=True)
        manifest = {"commands": {}, "files": files or {}}
        for i, (cmd, stdout) in enumerate(commands.items()):
            if stdout is None:
                manifest["commands"][cmd] = {"returncode": 2}
            else:
                name = "out-{}.txt".format(i)
                (fixture / name).write_text(stdout, encoding="utf-8")
                manifest["commands"][cmd] = name
        (fixture / "manifest.json").write_text(json.dumps(manifest),
                                               encoding="utf-8")
        return Runner(log_dir=self.work / "logs", fixture_dir=fixture)

    def test_dev_form_needs_no_lookup(self):
        runner = self.make_runner({})
        self.assertEqual(
            boot_advisor.resolve_root(runner, "DEV", "/dev/nvme0n1p1"),
            "/dev/nvme0n1p1")

    def test_partuuid_resolved_via_blkid_token_match(self):
        runner = self.make_runner(
            {"blkid -t PARTUUID=abcd-12 -o device": "/dev/sda1\n"})
        self.assertEqual(
            boot_advisor.resolve_root(runner, "PARTUUID", "abcd-12"),
            "/dev/sda1")

    def test_unresolvable_uuid_returns_none(self):
        runner = self.make_runner({"blkid -U dead-beef": None})
        self.assertIsNone(boot_advisor.resolve_root(runner, "UUID", "dead-beef"))


class CheckTests(unittest.TestCase):
    def run_check(self, fixture_dir, backups_dir=None):
        work = make_work_dir(self)
        runner = Runner(log_dir=work / "logs", fixture_dir=fixture_dir)
        report = Report(mode="simulate", tool_version="test")
        boot_advisor.check(runner, report, backups_dir=backups_dir)
        return report, work

    def test_verdict_a_consistent_layout(self):
        report, _work = self.run_check(BASE)
        text = report.render_text()
        self.assertEqual(report.exit_code(), 0, text)
        self.assertIn("matches", text)
        self.assertIn("/dev/nvme0n1p1", text)

    def test_verdict_a_text_is_plain_language(self):
        # PLAN G6 (D27): say plainly that this layout is normal and correct.
        report, _work = self.run_check(BASE)
        self.assertIn("normal, correct", report.render_text())

    def test_verdict_a_includes_readonly_efibootmgr(self):
        report, _work = self.run_check(BASE)
        text = report.render_text()
        self.assertIn("BootOrder", text)
        self.assertIn("read-only", text)

    def test_verdict_b_root_on_sd_migration_advisory(self):
        report, _work = self.run_check(FIXTURES / "orin-nano-8gb-root-on-sd")
        text = report.render_text()
        warns = [f for f in report.findings if f.level == LEVEL_WARN]
        self.assertTrue(any("JetsonHacks" in (f.details or "") + f.message
                            for f in warns), text)
        self.assertIn("mmcblk1p1", text)

    def test_verdict_c_mismatch_suggests_line_and_backs_up(self):
        work_backups = make_work_dir(self) / "backups"
        report, _work = self.run_check(
            FIXTURES / "orin-nano-8gb-root-mismatch", backups_dir=work_backups)
        text = report.render_text()
        warns = [f for f in report.findings if f.level == LEVEL_WARN]
        self.assertTrue(warns, text)
        self.assertIn("root=/dev/nvme0n1p1", text)  # the suggested line
        copies = list(work_backups.glob("*extlinux.conf*"))
        self.assertEqual(len(copies), 1)
        self.assertIn("99999999-aaaa", copies[0].read_text(encoding="utf-8"))

    def test_stale_boot_order_warns_with_exact_fix(self):
        report, _work = self.run_check(
            FIXTURES / "orin-nano-8gb-bootorder-stale")
        text = report.render_text()
        warns = [f for f in report.findings if f.level == LEVEL_WARN]
        stale = [f for f in warns if "startup order" in f.message]
        self.assertEqual(len(stale), 1, text)
        self.assertIn("UEFI SD Device", stale[0].message)
        details = stale[0].details or ""
        # exact manual fix: current entry moved to the front, order preserved
        self.assertIn("sudo efibootmgr -o "
                      "0008,0001,0004,0003,0002,0005,0000,0006,0007", details)
        self.assertIn("never runs it", details)
        self.assertIn("Change Boot Order", details)
        # verdict (a) still passes on this fixture: root really is on NVMe
        self.assertIn("matches", text)

    def test_verdicts_never_go_below_warn(self):
        # Tier 3: advisory findings are WARN at most, never ACTION, because
        # the tool offers no apply path for boot configuration.
        for fixture in (BASE, FIXTURES / "orin-nano-8gb-root-on-sd",
                        FIXTURES / "orin-nano-8gb-root-mismatch",
                        FIXTURES / "orin-nano-8gb-bootorder-stale"):
            report, _work = self.run_check(fixture)
            for finding in report.findings:
                self.assertIn(finding.level, (LEVEL_PASS, LEVEL_WARN),
                              "{}: {}".format(fixture.name, finding))


if __name__ == "__main__":
    unittest.main()
