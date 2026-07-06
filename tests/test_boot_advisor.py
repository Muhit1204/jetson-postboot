"""Tests for modules/boot_advisor.py (Tier 3, read-only forever).

Three fixture sets drive the three verdicts (PLAN 6.5 acceptance):
- orin-nano-8gb (captured): configured root matches mounted root -> (a).
- orin-nano-8gb-root-on-sd (derived): root on SD, larger NVMe idle -> (b),
  migration advisory.
- orin-nano-8gb-root-mismatch (derived): configured UUID resolves nowhere
  -> (c), suggested extlinux line + extlinux.conf copied to backups.
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

    def test_verdicts_never_go_below_warn(self):
        # Tier 3: advisory findings are WARN at most, never ACTION, because
        # the tool offers no apply path for boot configuration.
        for fixture in (BASE, FIXTURES / "orin-nano-8gb-root-on-sd",
                        FIXTURES / "orin-nano-8gb-root-mismatch"):
            report, _work = self.run_check(fixture)
            for finding in report.findings:
                self.assertIn(finding.level, (LEVEL_PASS, LEVEL_WARN),
                              "{}: {}".format(fixture.name, finding))


if __name__ == "__main__":
    unittest.main()
