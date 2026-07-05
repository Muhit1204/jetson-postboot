"""Tests for lib/runner.py: the single subprocess gateway.

Covers the GUARDRAILS section 2 allowlist (binary plus per-binary argument
constraints), the three behaviours (real, dry-run, simulate), fixture manifest
lookup, and per-invocation logging.
"""

import json
import types
import unittest
from unittest import mock

from jetson_postboot.lib import runner as runner_mod
from jetson_postboot.lib.runner import (
    CommandNotAllowedError,
    Runner,
    RunnerError,
    SimulationMissError,
)
from tests.support import make_work_dir


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.work = make_work_dir(self)
        self.log_dir = self.work / "logs"
        self.downloads = self.work / "downloads"
        self.downloads.mkdir()
        self.echoed = []

    def make_runner(self, fixture_dir=None, dry_run=False):
        return Runner(
            log_dir=self.log_dir,
            fixture_dir=fixture_dir,
            dry_run=dry_run,
            downloads_dir=self.downloads,
            echo=self.echoed.append,
        )

    def make_fixture(self, commands=None, files=None):
        fdir = self.work / "fixture"
        fdir.mkdir(exist_ok=True)
        manifest = {"commands": {}, "files": {}}
        for index, (cmd, spec) in enumerate(sorted((commands or {}).items())):
            fname = "c{}.txt".format(index)
            if isinstance(spec, str):
                (fdir / fname).write_text(spec, encoding="utf-8")
                manifest["commands"][cmd] = fname
            else:
                (fdir / fname).write_text(spec.get("stdout", ""), encoding="utf-8")
                manifest["commands"][cmd] = {
                    "stdout": fname,
                    "returncode": spec.get("returncode", 0),
                }
        for index, (path, content) in enumerate(sorted((files or {}).items())):
            fname = "f{}.txt".format(index)
            (fdir / fname).write_text(content, encoding="utf-8")
            manifest["files"][path] = fname
        (fdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return fdir


class AllowlistTests(RunnerTestCase):
    def test_rejected_commands(self):
        runner = self.make_runner(dry_run=True)
        cases = [
            (["curl", "https://example.com"], False),
            (["rm", "-rf", "/"], True),
            (["sudo", "lsblk"], False),
            (["lsblk"], True),
            (["sysctl", "-w", "vm.swappiness=10"], False),
            (["sysctl", "-w", "kernel.panic=1"], True),
            (["systemctl", "disable", "ssh.service"], True),
            (["systemctl", "start", "nvzramconfig.service"], True),
            (["systemctl", "daemon-reload"], True),
            (["sfdisk", "/dev/nvme0n1"], False),
            (["sgdisk", "-e", "/dev/nvme0n1"], False),
            (["sgdisk", "-e", "/dev/nvme0n1"], True),
            (["sgdisk", "--zap-all", "/dev/nvme0n1"], True),
            (["efibootmgr", "-o", "0001"], False),
            (["apt-get", "install", "vim"], True),
            (["apt-get", "install", "-y", "cloud-guest-utils"], True),
            (["swapoff", "/dev/sda1"], True),
            (["swapon", "/dev/sda1"], True),
            (["chmod", "777", "/swapfile"], True),
            (["chmod", "600", "/etc/shadow"], True),
            (["fallocate", "-l", "8G", "/etc/passwd"], True),
            (["mkswap", "/dev/sda1"], True),
            (["growpart", "nvme0n1", "1"], True),
            (["growpart", "/dev/nvme0n1", "1"], True),
            (["resize2fs", "/dev/nvme0n1p1"], True),
            (["sh", "/etc/evil.sh"], True),
            (["nvpmodel", "-m", "0"], False),
        ]
        for argv, mutate in cases:
            with self.subTest(argv=argv, mutate=mutate):
                with self.assertRaises(CommandNotAllowedError):
                    runner.run(argv, mutate=mutate)

    def test_allowed_mutations_pass_validation_dry_run(self):
        runner = self.make_runner(dry_run=True)
        script = self.downloads / "ollama-install.sh"
        script.write_text("echo hi\n", encoding="utf-8")
        cases = [
            ["sysctl", "-w", "vm.swappiness=10"],
            ["systemctl", "disable", "nvzramconfig.service"],
            ["systemctl", "stop", "nvzramconfig.service"],
            ["swapoff", "/dev/zram0"],
            ["swapoff", "/swapfile"],
            ["swapon", "/swapfile"],
            ["fallocate", "-l", "8G", "/swapfile"],
            ["chmod", "600", "/swapfile"],
            ["mkswap", "/swapfile"],
            ["sh", str(script)],
            ["ollama", "pull", "qwen2.5:3b"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                result = runner.run(argv, sudo=True, mutate=True)
                self.assertFalse(result.executed)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.mode, "dry-run")
        self.assertEqual(len(self.echoed), len(cases))
        self.assertIn("sudo sysctl -w vm.swappiness=10", self.echoed[0])

    def test_allowed_reads_pass_validation_in_simulate(self):
        commands = {
            "lsblk -b -J -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,PTTYPE": "payload-lsblk",
            "sudo sfdisk -d /dev/nvme0n1": "payload-sfdisk",
            "findmnt -no SOURCE /": "payload-findmnt",
            "swapon --show --bytes": "payload-swapon",
            "systemctl is-enabled nvzramconfig.service": "payload-enabled",
            "sudo nvpmodel -q": "payload-nvpmodel",
            "efibootmgr -v": "payload-efi",
            "sgdisk -p /dev/nvme0n1": "payload-sgdisk",
        }
        fdir = self.make_fixture(commands=commands)
        runner = self.make_runner(fixture_dir=fdir)
        specs = [
            (["lsblk", "-b", "-J", "-o", "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,PTTYPE"], False),
            (["sfdisk", "-d", "/dev/nvme0n1"], True),
            (["findmnt", "-no", "SOURCE", "/"], False),
            (["swapon", "--show", "--bytes"], False),
            (["systemctl", "is-enabled", "nvzramconfig.service"], False),
            (["nvpmodel", "-q"], True),
            (["efibootmgr", "-v"], False),
            (["sgdisk", "-p", "/dev/nvme0n1"], False),
        ]
        for argv, sudo in specs:
            with self.subTest(argv=argv):
                result = runner.run(argv, sudo=sudo)
                self.assertTrue(result.stdout.startswith("payload-"))
                self.assertFalse(result.executed)


class SimulateTests(RunnerTestCase):
    def test_returns_fixture_stdout(self):
        fdir = self.make_fixture(commands={"zramctl --bytes": "payload-zram"})
        runner = self.make_runner(fixture_dir=fdir)
        result = runner.run(["zramctl", "--bytes"])
        self.assertEqual(result.stdout, "payload-zram")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.executed)
        self.assertEqual(result.mode, "simulate")

    def test_sudo_prefix_is_part_of_fixture_key(self):
        fdir = self.make_fixture(commands={"sudo sfdisk -d /dev/nvme0n1": "payload-sfdisk"})
        runner = self.make_runner(fixture_dir=fdir)
        result = runner.run(["sfdisk", "-d", "/dev/nvme0n1"], sudo=True)
        self.assertEqual(result.stdout, "payload-sfdisk")

    def test_unknown_command_raises(self):
        fdir = self.make_fixture()
        runner = self.make_runner(fixture_dir=fdir)
        with self.assertRaises(SimulationMissError):
            runner.run(["findmnt", "-no", "SOURCE", "/"])

    def test_nonzero_returncode_entry(self):
        fdir = self.make_fixture(
            commands={"which ollama": {"stdout": "", "returncode": 1}}
        )
        runner = self.make_runner(fixture_dir=fdir)
        result = runner.run(["which", "ollama"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")

    def test_mutation_in_simulate_uses_fixture(self):
        fdir = self.make_fixture(
            commands={"sudo systemctl disable nvzramconfig.service": ""}
        )
        runner = self.make_runner(fixture_dir=fdir)
        result = runner.run(
            ["systemctl", "disable", "nvzramconfig.service"], sudo=True, mutate=True
        )
        self.assertFalse(result.executed)
        self.assertEqual(result.returncode, 0)

    def test_dry_run_wins_over_simulate_for_mutations(self):
        fdir = self.make_fixture()
        runner = self.make_runner(fixture_dir=fdir, dry_run=True)
        result = runner.run(
            ["sysctl", "-w", "vm.swappiness=10"], sudo=True, mutate=True
        )
        self.assertEqual(result.mode, "dry-run")
        self.assertEqual(len(self.echoed), 1)

    def test_read_file_from_fixture_and_miss(self):
        fdir = self.make_fixture(files={"/proc/meminfo": "payload-meminfo"})
        runner = self.make_runner(fixture_dir=fdir)
        self.assertEqual(runner.read_file("/proc/meminfo"), "payload-meminfo")
        with self.assertRaises(SimulationMissError):
            runner.read_file("/etc/nv_tegra_release")

    def test_missing_manifest_raises(self):
        empty = self.work / "empty-fixture"
        empty.mkdir()
        with self.assertRaises(RunnerError):
            self.make_runner(fixture_dir=empty)


class RealModeTests(RunnerTestCase):
    def test_real_execution_goes_through_subprocess(self):
        runner = self.make_runner()
        fake = types.SimpleNamespace(returncode=0, stdout="payload", stderr="")
        with mock.patch.object(runner_mod.subprocess, "run", return_value=fake) as spy:
            result = runner.run(["lsblk", "-b"])
        spy.assert_called_once()
        self.assertEqual(spy.call_args[0][0], ["lsblk", "-b"])
        self.assertTrue(result.executed)
        self.assertEqual(result.stdout, "payload")
        self.assertEqual(result.mode, "real")

    def test_sudo_prepended_in_real_mode(self):
        runner = self.make_runner()
        fake = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(runner_mod.subprocess, "run", return_value=fake) as spy:
            runner.run(["nvpmodel", "-q"], sudo=True)
        self.assertEqual(spy.call_args[0][0], ["sudo", "nvpmodel", "-q"])

    def test_dry_run_mutation_never_touches_subprocess(self):
        runner = self.make_runner(dry_run=True)
        with mock.patch.object(
            runner_mod.subprocess, "run", side_effect=AssertionError("must not execute")
        ):
            result = runner.run(["mkswap", "/swapfile"], sudo=True, mutate=True)
        self.assertFalse(result.executed)

    def test_read_file_real(self):
        sample = self.work / "sample.txt"
        sample.write_text("payload-file", encoding="utf-8")
        runner = self.make_runner()
        self.assertEqual(runner.read_file(sample), "payload-file")


class ReadLinkTests(RunnerTestCase):
    def test_simulate_returns_stripped_target(self):
        fdir = self.make_fixture(files={"/some/link": "payload-target\n"})
        runner = self.make_runner(fixture_dir=fdir)
        self.assertEqual(runner.read_link("/some/link"), "payload-target")

    def test_simulate_miss_raises(self):
        fdir = self.make_fixture()
        runner = self.make_runner(fixture_dir=fdir)
        with self.assertRaises(SimulationMissError):
            runner.read_link("/usr/local/cuda")

    def test_real_uses_os_readlink(self):
        runner = self.make_runner()
        with mock.patch.object(
            runner_mod.os, "readlink", return_value="payload-target"
        ) as spy:
            self.assertEqual(runner.read_link("/some/link"), "payload-target")
        spy.assert_called_once_with("/some/link")


class LoggingTests(RunnerTestCase):
    def test_every_invocation_logged(self):
        fdir = self.make_fixture(commands={"zramctl --bytes": "payload"})
        runner = self.make_runner(fixture_dir=fdir)
        runner.run(["zramctl", "--bytes"])
        logs = list(self.log_dir.glob("run-*.log"))
        self.assertEqual(len(logs), 1)
        content = logs[0].read_text(encoding="utf-8")
        self.assertIn("zramctl --bytes", content)
        self.assertIn("SIMULATE", content)

    def test_dry_run_logged(self):
        runner = self.make_runner(dry_run=True)
        runner.run(["chmod", "600", "/swapfile"], sudo=True, mutate=True)
        logs = list(self.log_dir.glob("run-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("DRY-RUN", logs[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
