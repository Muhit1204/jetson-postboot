"""Single gateway for every external command and system file read.

GUARDRAILS section 2: no other module may touch subprocess; a guard test
enforces it. The runner validates every command against the binary allowlist
plus per-binary argument constraints, then dispatches by behaviour:

- real: execute via subprocess, capture stdout/stderr/returncode.
- dry-run: mutations are printed and skipped with a zero-rc placeholder;
  reads still execute, because a truthful printed command sequence requires
  real detection first.
- simulate: every command and file read is answered from a fixture manifest;
  a missing entry raises SimulationMissError, which keeps fixtures complete.

Every invocation, in every behaviour, is appended to ./logs/run-<ts>.log.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


class RunnerError(Exception):
    """Base class for runner failures."""


class CommandNotAllowedError(RunnerError):
    """Command falls outside the GUARDRAILS section 2 allowlist."""


class SimulationMissError(RunnerError):
    """Simulate mode has no fixture manifest entry for the request."""


@dataclass
class CommandResult:
    argv: List[str]  # full argv including any sudo prefix
    returncode: int
    stdout: str
    stderr: str
    mode: str  # "real", "simulate", or "dry-run"
    executed: bool  # True only when a real process actually ran


_READ_ONLY_SIMPLE = frozenset({
    "lsblk", "findmnt", "df", "blkid", "zramctl", "nvcc", "dpkg", "which",
    "python3", "sha256sum",
})
_SYSTEMCTL_READ = frozenset({"status", "is-enabled"})
_SYSTEMCTL_MUTATE = frozenset({"enable", "disable", "stop"})
_SYSTEMCTL_UNIT = "nvzramconfig.service"
# GUARDRAILS 3.3 item 3: the only swapfile this tool may ever manage.
_SWAPFILE = "/swapfile"
# GUARDRAILS v1.2: cp writes staged ./.work/ files onto exactly these two
# targets; rm removes exactly the conf file and the swapfile. /etc/fstab is
# edited (via cp after the single-tagged-line assertion), never removed.
_SYSCTL_CONF = "/etc/sysctl.d/99-jetson-postboot.conf"
_CP_DESTINATIONS = frozenset({_SYSCTL_CONF, "/etc/fstab"})
_RM_TARGETS = frozenset({_SYSCTL_CONF, _SWAPFILE})


def _reject(argv, why):
    raise CommandNotAllowedError('"{}" rejected: {}'.format(" ".join(argv), why))


def _require(condition, argv, why):
    if not condition:
        _reject(argv, why)


def _validate(argv, mutate, downloads_dir, work_dir):
    """Enforce the allowlist. Raises CommandNotAllowedError on any violation.

    Constraints tighter than GUARDRAILS are deliberate scope locks for v1
    (e.g. sysctl -w limited to vm.swappiness, chmod locked to the swapfile).
    Loosening any of them means editing this function AND its test, which is
    the intended friction.
    """
    binary, args = argv[0], argv[1:]
    if binary == "sudo":
        _reject(argv, "pass sudo=True to Runner.run instead of a sudo argv prefix")
    if binary in _READ_ONLY_SIMPLE:
        _require(not mutate, argv, "read-only binary invoked with mutate=True")
        return
    if binary == "sfdisk":
        _require("-d" in args or "--dump" in args, argv, "sfdisk allowed in dump mode only")
        _require(not mutate, argv, "sfdisk dump is read-only")
        return
    if binary == "sgdisk":
        # GUARDRAILS v1.1: print only. sgdisk -e was removed with the storage
        # module's demotion to Tier 3 advisory-only.
        flags = {a for a in args if a.startswith("-")}
        _require(bool(flags) and flags <= {"-p", "--print"}, argv,
                 "sgdisk allowed with -p/--print only (GUARDRAILS v1.1)")
        _require(not mutate, argv, "sgdisk print is read-only")
        return
    if binary == "sysctl":
        if "-w" in args:
            _require(mutate, argv, "sysctl -w is a mutation; call with mutate=True")
            assignments = [a for a in args if "=" in a]
            _require(bool(assignments), argv, "sysctl -w needs key=value")
            for assignment in assignments:
                _require(assignment.startswith("vm.swappiness="), argv,
                         "only vm.swappiness may be written in v1")
        else:
            _require(not mutate, argv, "sysctl reads are read-only")
        return
    if binary == "systemctl":
        positional = [a for a in args if not a.startswith("-")]
        _require(bool(positional), argv, "systemctl needs a subcommand")
        subcommand = positional[0]
        if subcommand in _SYSTEMCTL_READ:
            _require(not mutate, argv, "systemctl {} is read-only".format(subcommand))
        elif subcommand in _SYSTEMCTL_MUTATE:
            _require(mutate, argv,
                     "systemctl {} is a mutation; call with mutate=True".format(subcommand))
            _require(positional[1:] == [_SYSTEMCTL_UNIT], argv,
                     "systemctl {} may target {} only".format(subcommand, _SYSTEMCTL_UNIT))
        else:
            _reject(argv, "systemctl subcommand '{}' not allowlisted".format(subcommand))
        return
    if binary == "swapon":
        if "--show" in args:
            _require(not mutate, argv, "swapon --show is read-only")
        else:
            _require(mutate, argv, "swapon activation is a mutation; call with mutate=True")
            targets = [a for a in args if not a.startswith("-")]
            _require(targets == [_SWAPFILE], argv, "swapon may activate /swapfile only")
        return
    if binary == "swapoff":
        _require(mutate, argv, "swapoff is a mutation; call with mutate=True")
        targets = [a for a in args if not a.startswith("-")]
        _require(bool(targets), argv, "swapoff needs a target")
        for target in targets:
            _require(target == _SWAPFILE or target.startswith("/dev/zram"), argv,
                     "swapoff may target /swapfile or zram devices only")
        return
    if binary == "nvpmodel":
        _require(args == ["-q"], argv, "nvpmodel allowed in query mode only")
        _require(not mutate, argv, "nvpmodel -q is read-only")
        return
    if binary == "efibootmgr":
        _require(set(args) <= {"-v"}, argv, "efibootmgr allowed read-only (no args or -v)")
        _require(not mutate, argv, "efibootmgr is read-only in this tool")
        return
    if binary == "fallocate":
        _require(mutate, argv, "fallocate is a mutation; call with mutate=True")
        _require(bool(args) and args[-1] == _SWAPFILE, argv,
                 "fallocate may target /swapfile only")
        return
    if binary == "chmod":
        _require(mutate, argv, "chmod is a mutation; call with mutate=True")
        _require(args == ["600", _SWAPFILE], argv,
                 "chmod allowed as 'chmod 600 /swapfile' only")
        return
    if binary == "mkswap":
        _require(mutate, argv, "mkswap is a mutation; call with mutate=True")
        _require(args == [_SWAPFILE], argv, "mkswap may target /swapfile only")
        return
    # growpart, resize2fs, and apt-get were removed from the allowlist in
    # GUARDRAILS v1.1: storage extension is Tier 3 advisory-only in v1, so
    # those binaries fall through to the final rejection below.
    if binary == "sh":
        _require(mutate, argv, "sh is a mutation; call with mutate=True")
        _require(len(args) == 1, argv, "sh takes exactly one script path")
        if downloads_dir is None:
            _reject(argv, "runner has no downloads directory configured")
        script = Path(args[0]).resolve()
        try:
            script.relative_to(Path(downloads_dir).resolve())
        except ValueError:
            _reject(argv, "sh may execute scripts from ./downloads/ only")
        return
    if binary == "cp":
        _require(mutate, argv, "cp is a mutation; call with mutate=True")
        _require(len(args) == 2 and not args[0].startswith("-"), argv,
                 "cp takes exactly a staged source and a destination")
        _require(args[1] in _CP_DESTINATIONS, argv,
                 "cp may write {} only".format(" or ".join(sorted(_CP_DESTINATIONS))))
        if work_dir is None:
            _reject(argv, "runner has no work directory configured")
        source = Path(args[0]).resolve()
        try:
            source.relative_to(Path(work_dir).resolve())
        except ValueError:
            _reject(argv, "cp source must be staged under ./.work/")
        return
    if binary == "rm":
        _require(mutate, argv, "rm is a mutation; call with mutate=True")
        _require(len(args) == 1 and not args[0].startswith("-"), argv,
                 "rm takes exactly one target and no flags")
        _require(args[0] in _RM_TARGETS, argv,
                 "rm may remove {} only".format(" or ".join(sorted(_RM_TARGETS))))
        return
    if binary == "ollama":
        _require(mutate, argv, "ollama commands are treated as mutations in v1")
        return
    _reject(argv, "binary not in the GUARDRAILS allowlist")


class Runner:
    def __init__(self, log_dir, fixture_dir=None, dry_run=False,
                 downloads_dir=None, work_dir=None, echo=None):
        self._log_dir = Path(log_dir)
        self._fixture_dir = Path(fixture_dir) if fixture_dir is not None else None
        self._dry_run = bool(dry_run)
        self._downloads_dir = Path(downloads_dir) if downloads_dir is not None else None
        self._work_dir = Path(work_dir) if work_dir is not None else None
        self._echo = echo if echo is not None else print
        self._log_path = None  # type: Optional[Path]
        self._manifest = self._load_manifest() if self._fixture_dir is not None else None

    @property
    def mode(self):
        parts = []
        if self._fixture_dir is not None:
            parts.append("simulate")
        if self._dry_run:
            parts.append("dry-run")
        return "+".join(parts) if parts else "real"

    def run(self, argv, sudo=False, mutate=False):
        """Validate and dispatch one command. argv never includes sudo."""
        if not argv:
            raise RunnerError("empty argv")
        argv = [str(a) for a in argv]
        _validate(argv, mutate, self._downloads_dir, self._work_dir)
        full = (["sudo"] + argv) if sudo else list(argv)
        command = " ".join(full)
        if mutate and self._dry_run:
            self._log("DRY-RUN", command)
            self._echo("DRY-RUN would execute: {}".format(command))
            return CommandResult(full, 0, "", "", "dry-run", False)
        if self._manifest is not None:
            return self._simulate(full, command)
        return self._execute(full, command)

    def read_file(self, path):
        """Read a system file (the cat-equivalent read path from GUARDRAILS)."""
        path = str(path)
        if self._manifest is not None:
            entry = self._manifest["files"].get(path)
            if entry is None:
                self._log("SIM-MISS", "read {}".format(path))
                raise SimulationMissError("no fixture entry for file: {}".format(path))
            self._log("SIMULATE", "read {}".format(path), 0)
            return (self._fixture_dir / entry).read_text(encoding="utf-8")
        self._log("REAL", "read {}".format(path))
        return Path(path).read_text(encoding="utf-8")

    def file_exists(self, path):
        """True when the system file exists. Real: os.path.exists. Simulate:
        membership in the manifest "files" map, so fixtures express absence
        by omission (unlike read_file, where a miss is a fixture gap)."""
        path = str(path)
        if self._manifest is not None:
            exists = path in self._manifest["files"]
            self._log("SIMULATE", "exists {} -> {}".format(path, exists))
            return exists
        self._log("REAL", "exists {}".format(path))
        return os.path.exists(path)

    def read_link(self, path):
        """Resolve a symlink target. Real: os.readlink. Simulate: the files
        manifest entry holds the captured readlink output (trailing newline
        stripped)."""
        path = str(path)
        if self._manifest is not None:
            entry = self._manifest["files"].get(path)
            if entry is None:
                self._log("SIM-MISS", "readlink {}".format(path))
                raise SimulationMissError("no fixture entry for link: {}".format(path))
            self._log("SIMULATE", "readlink {}".format(path), 0)
            return (self._fixture_dir / entry).read_text(encoding="utf-8").rstrip("\n")
        self._log("REAL", "readlink {}".format(path))
        return os.readlink(path)

    def _load_manifest(self):
        manifest_path = self._fixture_dir / "manifest.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise RunnerError("fixture manifest not found: {}".format(manifest_path))
        except json.JSONDecodeError as exc:
            raise RunnerError("fixture manifest is not valid JSON: {}: {}".format(
                manifest_path, exc))
        data.setdefault("commands", {})
        data.setdefault("files", {})
        return data

    def _portable_key(self, command):
        """Manifest keys must not embed machine-specific absolute paths:
        rewrite the configured work/downloads dirs to their repo-relative
        spellings before lookup."""
        for directory, spelling in ((self._work_dir, "./.work"),
                                    (self._downloads_dir, "./downloads")):
            if directory is not None:
                command = command.replace(str(Path(directory).resolve()), spelling)
        return command

    def _simulate(self, full, command):
        command = self._portable_key(command)
        entry = self._manifest["commands"].get(command)
        if entry is None:
            self._log("SIM-MISS", command)
            raise SimulationMissError("no fixture entry for command: {}".format(command))
        if isinstance(entry, str):
            stdout_file, returncode, stderr_file = entry, 0, None
        else:
            stdout_file = entry.get("stdout")
            returncode = int(entry.get("returncode", 0))
            stderr_file = entry.get("stderr")
        stdout = (self._fixture_dir / stdout_file).read_text(encoding="utf-8") if stdout_file else ""
        stderr = (self._fixture_dir / stderr_file).read_text(encoding="utf-8") if stderr_file else ""
        self._log("SIMULATE", command, returncode)
        return CommandResult(full, returncode, stdout, stderr, "simulate", False)

    def _execute(self, full, command):
        try:
            completed = subprocess.run(full, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            # Shell convention for command-not-found; lets checks report an
            # absent optional binary (nvcc, efibootmgr, ...) as a finding
            # instead of crashing the run.
            self._log("REAL", command, 127)
            return CommandResult(full, 127, "",
                                 "{}: command not found".format(full[0]),
                                 "real", False)
        self._log("REAL", command, completed.returncode)
        return CommandResult(full, completed.returncode, completed.stdout or "",
                             completed.stderr or "", "real", True)

    def _log(self, event, detail, returncode=None):
        if self._log_path is None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            self._log_path = self._log_dir / "run-{}.log".format(stamp)
        suffix = "" if returncode is None else " | rc={}".format(returncode)
        line = "{} | {} | {}{}\n".format(
            datetime.now(timezone.utc).isoformat(), event, detail, suffix)
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
