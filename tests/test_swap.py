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

from jetson_postboot.lib.report import LEVEL_ACTION, LEVEL_PASS, LEVEL_WARN, Report
from jetson_postboot.lib.runner import Runner
from jetson_postboot.lib.state import StateStore
from jetson_postboot.modules import swap
from tests.support import REPO_ROOT, make_work_dir

ALL_FIXTURES = REPO_ROOT / "tests" / "fixtures"
FIXTURES = ALL_FIXTURES / "orin-nano-8gb"


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


class FstabHelpersTests(unittest.TestCase):
    """GUARDRAILS v1.2 invariant: an fstab cp is permitted only after the
    staged text is shown to differ from the backup by exactly the one
    "# jetson-postboot"-tagged line."""

    BASE = ("# /etc/fstab: static file system information.\n"
            "UUID=52d10c20 / ext4 defaults 0 1\n")

    def test_add_appends_tagged_line_once(self):
        new = swap.fstab_add_line(self.BASE)
        added = [l for l in new.splitlines() if l.endswith(swap.FSTAB_TAG)]
        self.assertEqual(added, [swap.FSTAB_LINE])
        self.assertTrue(new.startswith(self.BASE))
        self.assertTrue(new.endswith("\n"))

    def test_add_handles_missing_trailing_newline(self):
        new = swap.fstab_add_line(self.BASE.rstrip("\n"))
        self.assertIn("\n" + swap.FSTAB_LINE + "\n", new)

    def test_add_refuses_when_tag_already_present(self):
        with self.assertRaises(swap.FstabEditError):
            swap.fstab_add_line(self.BASE + swap.FSTAB_LINE + "\n")

    def test_remove_strips_exactly_the_tagged_line(self):
        with_line = swap.fstab_add_line(self.BASE)
        self.assertEqual(swap.fstab_remove_line(with_line), self.BASE)

    def test_remove_refuses_when_tag_absent(self):
        with self.assertRaises(swap.FstabEditError):
            swap.fstab_remove_line(self.BASE)

    def test_assert_accepts_single_tagged_add_and_remove(self):
        with_line = swap.fstab_add_line(self.BASE)
        self.assertEqual(
            swap.assert_fstab_single_line_diff(self.BASE, with_line), "added")
        self.assertEqual(
            swap.assert_fstab_single_line_diff(with_line, self.BASE), "removed")

    def test_assert_rejects_everything_else(self):
        with_line = swap.fstab_add_line(self.BASE)
        cases = [
            (self.BASE, self.BASE),  # no difference
            (self.BASE, self.BASE + "/dev/sda1 none swap sw 0 0\n"),  # untagged add
            (self.BASE, self.BASE.replace("ext4", "xfs")),  # modified line
            (self.BASE, with_line + swap.FSTAB_LINE + "\n"),  # two lines added
            (self.BASE,  # tagged add plus an unrelated edit
             swap.fstab_add_line(self.BASE.replace("defaults", "ro"))),
            (self.BASE, ""),  # everything removed
        ]
        for old, new in cases:
            with self.subTest(new=new):
                with self.assertRaises(swap.FstabEditError):
                    swap.assert_fstab_single_line_diff(old, new)


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

    def test_detection_text_is_plain_language(self):
        # PLAN G6 (D27): swappiness and the zram service explained in words.
        swap.check(self.runner, self.report)
        text = self.report.render_text()
        self.assertIn("Swappiness controls", text)
        self.assertIn("switches zram on at boot", text)

    def test_existing_swapfile_and_disabled_zram_reported(self):
        swap.check(self.runner, self.report)
        text = self.report.render_text()
        self.assertIn("/16GB.swap", text)
        self.assertIn("nvzramconfig", text)
        self.assertIn("disabled", text)
        # zram absent on this board: must not fire the default-zram finding
        self.assertNotIn("JetPack default", text)


class ApplyUndoTestCase(unittest.TestCase):
    """Shared harness: runs swap.apply/undo against a fixture, spying on
    every runner.run argv and collecting echoed lines."""

    def setUp(self):
        self.work = make_work_dir(self)
        self.echoed = []
        self.calls = []

    def make_ctx(self, confirms, dry_run=False, size_gib=8):
        answers = list(confirms)
        self.confirm_prompts = []

        def confirm(prompt):
            self.confirm_prompts.append(prompt)
            return answers.pop(0) if answers else False

        self.state = StateStore(self.work / "state" / "state.json")
        return {
            "state": self.state,
            "backups_dir": self.work / "backups",
            "work_dir": self.work / ".work",
            "echo": self.echoed.append,
            "confirm": confirm,
            "dry_run": dry_run,
            "swapfile_size_gib": size_gib,
        }

    def make_runner(self, fixture_dir, dry_run=False):
        runner = Runner(log_dir=self.work / "logs", fixture_dir=fixture_dir,
                        dry_run=dry_run, work_dir=self.work / ".work",
                        echo=self.echoed.append)
        original = runner.run

        def spying_run(argv, **kwargs):
            self.calls.append(list(argv))
            return original(argv, **kwargs)

        runner.run = spying_run
        return runner

    def run_apply(self, fixture, confirms, dry_run=False, size_gib=8):
        runner = self.make_runner(fixture, dry_run=dry_run)
        report = Report(mode="simulate", tool_version="test")
        ctx = self.make_ctx(confirms, dry_run=dry_run, size_gib=size_gib)
        swap.apply(runner, report, ctx)
        return report

    def run_undo(self, fixture, confirms, dry_run=False):
        runner = self.make_runner(fixture, dry_run=dry_run)
        report = Report(mode="simulate", tool_version="test")
        ctx = self.make_ctx(confirms, dry_run=dry_run)
        swap.undo(runner, report, ctx)
        return report

    def issued(self, *prefix):
        prefix = list(prefix)
        return [argv for argv in self.calls if argv[:len(prefix)] == prefix]


class ApplyTests(ApplyUndoTestCase):
    def test_tuned_board_applies_s1_skips_s2_s3(self):
        report = self.run_apply(FIXTURES, confirms=[True])
        text = report.render_text()
        self.assertTrue(self.issued("sysctl", "-w", "vm.swappiness=10"), text)
        self.assertTrue(self.issued("cp"), text)
        # S2: no zram and service disabled; S3: 16 GiB /16GB.swap adequate
        self.assertFalse(self.issued("systemctl", "disable"))
        self.assertFalse(self.issued("fallocate"))
        self.assertIn("/16GB.swap", text)
        values = self.state.latest("swap")["values"]
        self.assertEqual(values["prior_swappiness"], 60)
        self.assertNotIn("swapfile_created", values)

    def test_untuned_board_applies_full_s1_s2_s3(self):
        report = self.run_apply(
            ALL_FIXTURES / "orin-nano-8gb-untuned-default",
            confirms=[True, True, True])
        text = report.render_text()
        for prefix in (["sysctl", "-w", "vm.swappiness=10"],
                       ["systemctl", "disable", "nvzramconfig.service"],
                       ["systemctl", "stop", "nvzramconfig.service"],
                       ["fallocate", "-l", "8G", "/swapfile"],
                       ["chmod", "600", "/swapfile"],
                       ["mkswap", "/swapfile"],
                       ["swapon", "/swapfile"]):
            self.assertTrue(self.issued(*prefix), (prefix, text))
        self.assertEqual(len(self.issued("swapoff")), 6)
        self.assertEqual(len(self.issued("cp")), 2)  # sysctl.d conf + fstab
        staged_fstab = (self.work / ".work" / "fstab.new").read_text(
            encoding="utf-8")
        self.assertIn(swap.FSTAB_LINE, staged_fstab)
        backups = list((self.work / "backups").iterdir())
        self.assertTrue(any("fstab" in b.name for b in backups), backups)
        values = self.state.latest("swap")["values"]
        self.assertEqual(values["prior_swappiness"], 60)
        self.assertEqual(values["nvzramconfig_was"], "enabled")
        self.assertEqual(values["zram_devices"],
                         ["/dev/zram{}".format(i) for i in range(6)])
        self.assertTrue(values["swapfile_created"])
        self.assertTrue(values["fstab_line_added"])

    def test_low_memavailable_refuses_swapoff_offers_reboot_apply(self):
        report = self.run_apply(
            ALL_FIXTURES / "orin-nano-8gb-low-memavail",
            confirms=[True, True, True])
        text = report.render_text()
        warns = [f for f in report.findings if f.level == LEVEL_WARN]
        self.assertTrue(any("512 MiB" in f.message for f in warns), text)
        self.assertIn("reboot", text.lower())
        # reboot-apply path disables the service but never swapoffs zram
        self.assertTrue(self.issued("systemctl", "disable",
                                    "nvzramconfig.service"))
        self.assertFalse(self.issued("swapoff"))

    def test_dry_run_prints_full_sequence_without_prompt_or_state(self):
        self.run_apply(ALL_FIXTURES / "orin-nano-8gb-untuned-default",
                       confirms=[], dry_run=True)
        joined = "\n".join(self.echoed)
        for command in ("sysctl -w vm.swappiness=10",
                        "systemctl disable nvzramconfig.service",
                        "systemctl stop nvzramconfig.service",
                        "swapoff /dev/zram0",
                        "swapoff /dev/zram5",
                        "fallocate -l 8G /swapfile",
                        "chmod 600 /swapfile",
                        "mkswap /swapfile",
                        "swapon /swapfile"):
            self.assertIn(command, joined)
        self.assertIn("/etc/fstab", joined)
        self.assertEqual(self.confirm_prompts, [])
        self.assertIsNone(self.state.latest("swap"))

    def test_declined_step_mutates_nothing_and_warns(self):
        report = self.run_apply(FIXTURES, confirms=[False])
        self.assertFalse(self.issued("sysctl", "-w"))
        self.assertFalse(self.issued("cp"))
        warns = [f for f in report.findings if f.level == LEVEL_WARN]
        self.assertTrue(any("declined" in f.message for f in warns),
                        report.render_text())


class UndoTests(ApplyUndoTestCase):
    def test_undo_after_full_apply_restores_recorded_values(self):
        fixture = ALL_FIXTURES / "orin-nano-8gb-untuned-default"
        self.run_apply(fixture, confirms=[True, True, True])
        self.calls = []
        report = self.run_undo(fixture, confirms=[True, True, True, True])
        text = report.render_text()
        self.assertTrue(self.issued("sysctl", "-w", "vm.swappiness=60"), text)
        self.assertTrue(self.issued("systemctl", "enable",
                                    "nvzramconfig.service"), text)
        self.assertTrue(self.issued("swapoff", "/swapfile"), text)
        self.assertTrue(self.issued("rm", "/swapfile"), text)
        self.assertTrue(self.issued(
            "rm", "/etc/sysctl.d/99-jetson-postboot.conf"), text)
        # fixture fstab never gained the tagged line (simulate is stateless),
        # so undo reports it absent instead of copying a bad edit
        self.assertFalse(self.issued("cp"))
        self.assertIn("already absent", text)

    def test_undo_removes_tagged_fstab_line_when_present(self):
        import json as json_mod
        fixture = self.work / "fixture-applied"
        fixture.mkdir()
        fstab_with_line = swap.fstab_add_line(
            (FIXTURES / "fstab.txt").read_text(encoding="utf-8"))
        (fixture / "fstab.txt").write_text(fstab_with_line, encoding="utf-8")
        manifest = {
            "commands": {
                "sudo sysctl -w vm.swappiness=60": {"returncode": 0},
                "sudo rm /etc/sysctl.d/99-jetson-postboot.conf": {"returncode": 0},
                "sudo swapoff /swapfile": {"returncode": 0},
                "sudo rm /swapfile": {"returncode": 0},
                "sudo cp ./.work/fstab.new /etc/fstab": {"returncode": 0},
            },
            "files": {"/etc/fstab": "fstab.txt"},
        }
        (fixture / "manifest.json").write_text(
            json_mod.dumps(manifest), encoding="utf-8")
        ctx = self.make_ctx([])
        ctx["state"].record("swap", {
            "prior_swappiness": 60,
            "sysctl_conf_written": True,
            "sysctl_conf_existed": False,
            "swapfile_created": True,
            "fstab_line_added": True,
        })
        report = self.run_undo(fixture, confirms=[True, True, True, True])
        text = report.render_text()
        self.assertTrue(self.issued("cp"), text)
        staged = (self.work / ".work" / "fstab.new").read_text(encoding="utf-8")
        self.assertNotIn(swap.FSTAB_TAG, staged)

    def test_undo_without_recorded_state_warns(self):
        report = self.run_undo(FIXTURES, confirms=[])
        warns = [f for f in report.findings if f.level == LEVEL_WARN]
        self.assertTrue(any("nothing" in f.message.lower() for f in warns),
                        report.render_text())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
