"""Swap module: Tier 0 detection (Phase 1); Tier 1 apply/undo (Phase 2).

Detection per PLAN 6.4: swapon --show --bytes, zramctl --bytes, vm.swappiness,
systemctl is-enabled nvzramconfig.service. The finding fires when swappiness
exceeds 10 or zram total sits at the JetPack default of about half of RAM.

Apply (PLAN 6.4, GUARDRAILS 5.6/6): S1 swappiness now + persisted, S2 zram
off behind the 512 MiB headroom invariant (refusal offers the reboot-apply
path), S3 /swapfile on an NVMe root with the tagged fstab line - skipped
when an adequate file-backed swap is already active (Munta 2026-07-07).
Every step: show the exact commands, back up, confirm, execute, record the
pre-change values into state.json (cumulatively after each executed step, so
a crash never loses undo data for completed steps). Undo consumes the latest
record and touches nothing the apply run did not change.
"""

from pathlib import Path

from jetson_postboot.checks.system_info import parse_meminfo
from jetson_postboot.lib.backup import backup_text
from jetson_postboot.lib.report import (LEVEL_ACTION, LEVEL_PASS,
                                        LEVEL_WARN, human_gib)
from jetson_postboot.lib.runner import RunnerError

# PLAN 6.4 / GUARDRAILS 6: the profile's target swappiness; anything above
# triggers the finding.
RECOMMENDED_SWAPPINESS = 10
# JetPack sizes zram at about half of physical RAM; treat 40-60% as "at the
# default" to absorb rounding across boards.
_DEFAULT_ZRAM_BAND = (0.40, 0.60)

# GUARDRAILS 3.3 item 2: the one fstab line this tool may add or remove.
FSTAB_TAG = "# jetson-postboot"
FSTAB_LINE = "/swapfile none swap sw 0 0 " + FSTAB_TAG

# PLAN A2 default, confirmed by Munta 2026-07-07; --swapfile-size overrides.
DEFAULT_SWAPFILE_GIB = 8
GIB = 1 << 30
# GUARDRAILS 6: MemAvailable must exceed in-use zram data by this margin
# before any swapoff.
_HEADROOM_BYTES = 512 * 1024 * 1024
SYSCTL_CONF = "/etc/sysctl.d/99-jetson-postboot.conf"
SYSCTL_CONF_CONTENT = ("# jetson-postboot: persist vm.swappiness "
                       "(restore with --undo swap)\n"
                       "vm.swappiness={}\n".format(RECOMMENDED_SWAPPINESS))
SWAPFILE = "/swapfile"


class FstabEditError(Exception):
    """A staged fstab edit violates the single-tagged-line invariant."""


def fstab_add_line(text):
    """Return text with FSTAB_LINE appended. Refuses if a tagged line is
    already present, so apply can never stack duplicates."""
    if any(FSTAB_TAG in line for line in text.splitlines()):
        raise FstabEditError("fstab already carries a {} line".format(FSTAB_TAG))
    if text and not text.endswith("\n"):
        text += "\n"
    return text + FSTAB_LINE + "\n"


def fstab_remove_line(text):
    """Return text with the single tagged line removed. Refuses unless
    exactly one tagged line exists."""
    lines = text.splitlines()
    tagged = [line for line in lines if FSTAB_TAG in line]
    if len(tagged) != 1:
        raise FstabEditError(
            "expected exactly one {} line, found {}".format(FSTAB_TAG, len(tagged)))
    return "".join(line + "\n" for line in lines if FSTAB_TAG not in line)


def assert_fstab_single_line_diff(backup_text, staged_text):
    """GUARDRAILS v1.2 invariant, checked independently of how the staged
    text was built: the staged file must differ from the backup by exactly
    one line, and that line must carry FSTAB_TAG. Returns "added" or
    "removed"; raises FstabEditError on any other difference, which aborts
    the cp onto /etc/fstab."""
    backup_lines = backup_text.splitlines()
    staged_lines = staged_text.splitlines()
    if len(staged_lines) == len(backup_lines) + 1:
        longer, shorter, verdict = staged_lines, backup_lines, "added"
    elif len(backup_lines) == len(staged_lines) + 1:
        longer, shorter, verdict = backup_lines, staged_lines, "removed"
    else:
        raise FstabEditError(
            "staged fstab must differ from the backup by exactly one line")
    index = 0
    while index < len(shorter) and shorter[index] == longer[index]:
        index += 1
    changed = longer[index]
    if longer[:index] + longer[index + 1:] != shorter:
        raise FstabEditError("staged fstab differs beyond a single line")
    if FSTAB_TAG not in changed:
        raise FstabEditError(
            "the changed fstab line does not carry the {} tag".format(FSTAB_TAG))
    return verdict


def parse_swapon(text):
    """Rows of `swapon --show --bytes` as dicts; [] when no swap is active."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    entries = []
    for line in lines[1:]:  # skip NAME TYPE SIZE USED PRIO header
        fields = line.split()
        if len(fields) < 5:
            continue
        entries.append({
            "name": fields[0],
            "type": fields[1],
            "size": int(fields[2]),
            "used": int(fields[3]),
            "prio": int(fields[4]),
        })
    return entries


def parse_zramctl(text):
    """Rows of `zramctl --bytes` as dicts; [] when no zram devices exist
    (the captured board prints nothing at all then)."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].split()
    devices = []
    for line in lines[1:]:
        fields = line.split()
        row = dict(zip((h.lower() for h in header), fields))
        devices.append({
            "name": row.get("name", ""),
            "disksize": int(row.get("disksize", 0)),
            "data": int(row.get("data", 0)),
        })
    return devices


def zram_total(devices):
    return sum(device["disksize"] for device in devices)


def zram_at_jetpack_default(total, mem_total):
    """True when zram totals roughly half of RAM (the JetPack default)."""
    if total <= 0 or mem_total <= 0:
        return False
    low, high = _DEFAULT_ZRAM_BAND
    return low <= total / mem_total <= high


def check(runner, report):
    """Tier 0 swap findings (registry entry)."""
    swappiness = int(runner.read_file("/proc/sys/vm/swappiness").strip())
    if swappiness > RECOMMENDED_SWAPPINESS:
        report.add(
            LEVEL_ACTION, "swap",
            "vm.swappiness is {} (recommended {}). The swap profile "
            "(--apply swap, Phase 2) sets and persists it.".format(
                swappiness, RECOMMENDED_SWAPPINESS))
    else:
        report.add(LEVEL_PASS, "swap",
                   "vm.swappiness is {}".format(swappiness))

    devices = parse_zramctl(runner.run(["zramctl", "--bytes"]).stdout)
    mem = parse_meminfo(runner.read_file("/proc/meminfo"))
    total = zram_total(devices)
    if not devices:
        report.add(LEVEL_PASS, "swap", "no zram devices active")
    elif zram_at_jetpack_default(total, mem.get("MemTotal", 0)):
        report.add(
            LEVEL_ACTION, "swap",
            "zram total {} is at the JetPack default (about half of RAM); "
            "compressed swap competes with the working set and with GPU "
            "unified-memory allocations, which cannot be swapped.".format(
                human_gib(total)))
    else:
        report.add(LEVEL_PASS, "swap",
                   "zram total {} across {} device(s)".format(
                       human_gib(total), len(devices)))

    enabled = runner.run(
        ["systemctl", "is-enabled", "nvzramconfig.service"])
    state = enabled.stdout.strip() or "unknown"
    report.add(LEVEL_PASS, "swap",
               "nvzramconfig.service: {}".format(state))

    for entry in parse_swapon(runner.run(["swapon", "--show", "--bytes"]).stdout):
        level = LEVEL_PASS if entry["type"] == "file" else LEVEL_WARN
        report.add(level, "swap",
                   "active swap: {} ({}, {})".format(
                       entry["name"], entry["type"], human_gib(entry["size"])))


def _stage(work_dir, name, content):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    staged = work_dir / name
    staged.write_text(content, encoding="utf-8")
    return staged


def _run_step(runner, report, ctx, title, prompt, commands):
    """One confirmed mutation step (GUARDRAILS 5.6): echo the exact command
    sequence, then dry-run (runner prints and skips), or confirm + execute.
    Returns True only when the commands actually ran; a nonzero rc raises so
    the run stops instead of continuing on a half-applied system."""
    echo = ctx["echo"]
    echo("")
    echo(title)
    for argv in commands:
        echo("    sudo " + " ".join(argv))
    if ctx["dry_run"]:
        for argv in commands:
            runner.run(argv, sudo=True, mutate=True)
        return False
    if not ctx["confirm"]("{} Proceed?".format(title)):
        report.add(LEVEL_WARN, "swap", "{} declined; nothing changed".format(title))
        return False
    for argv in commands:
        result = runner.run(argv, sudo=True, mutate=True)
        if result.returncode != 0:
            raise RunnerError("'sudo {}' failed (rc={}): {}".format(
                " ".join(argv), result.returncode, result.stderr.strip()))
    return True


def apply(runner, report, ctx):
    """Tier 1 swap profile (--apply swap): S1 swappiness, S2 zram off,
    S3 NVMe swapfile. ctx carries state (StateStore), backups_dir, work_dir,
    echo, confirm, dry_run, and optionally swapfile_size_gib."""
    size_gib = int(ctx.get("swapfile_size_gib") or DEFAULT_SWAPFILE_GIB)
    work_dir = Path(ctx["work_dir"])
    backups_dir = ctx["backups_dir"]
    dry_run = ctx["dry_run"]

    swappiness = int(runner.read_file("/proc/sys/vm/swappiness").strip())
    devices = parse_zramctl(runner.run(["zramctl", "--bytes"]).stdout)
    mem = parse_meminfo(runner.read_file("/proc/meminfo"))
    swaps = parse_swapon(runner.run(["swapon", "--show", "--bytes"]).stdout)
    service = runner.run(
        ["systemctl", "is-enabled", "nvzramconfig.service"]).stdout.strip() or "unknown"
    root_source = runner.run(["findmnt", "-no", "SOURCE", "/"]).stdout.strip()

    values = {}
    backups = []

    def commit(step_values, step_backups=()):
        # Cumulative re-record after every executed step: the latest entry
        # always holds complete undo data for everything done so far.
        values.update(step_values)
        backups.extend(str(b) for b in step_backups)
        ctx["state"].record("swap", values, backups)

    # --- S1: vm.swappiness -------------------------------------------------
    if swappiness == RECOMMENDED_SWAPPINESS:
        report.add(LEVEL_PASS, "swap",
                   "S1 skipped: vm.swappiness already {}".format(swappiness))
    else:
        conf_existed = runner.file_exists(SYSCTL_CONF)
        step_backups = []
        if conf_existed and not dry_run:
            step_backups.append(backup_text(
                "99-jetson-postboot.conf", runner.read_file(SYSCTL_CONF),
                backups_dir))
        staged = _stage(work_dir, "99-jetson-postboot.conf", SYSCTL_CONF_CONTENT)
        ctx["echo"]("staged {} content:".format(staged))
        for line in SYSCTL_CONF_CONTENT.splitlines():
            ctx["echo"]("    " + line)
        if _run_step(
                runner, report, ctx,
                "S1: set vm.swappiness={} now and persist it via {}.".format(
                    RECOMMENDED_SWAPPINESS, SYSCTL_CONF),
                "S1: set and persist vm.swappiness={}.".format(
                    RECOMMENDED_SWAPPINESS),
                [["sysctl", "-w",
                  "vm.swappiness={}".format(RECOMMENDED_SWAPPINESS)],
                 ["cp", str(staged), SYSCTL_CONF]]):
            commit({"prior_swappiness": swappiness,
                    "sysctl_conf_written": True,
                    "sysctl_conf_existed": conf_existed}, step_backups)
            report.add(LEVEL_PASS, "swap",
                       "S1 applied: vm.swappiness {} -> {}, persisted".format(
                           swappiness, RECOMMENDED_SWAPPINESS))

    # --- S2: zram off ------------------------------------------------------
    service_enabled = service == "enabled"
    if not devices and not service_enabled:
        report.add(LEVEL_PASS, "swap",
                   "S2 skipped: no zram devices and nvzramconfig.service "
                   "is {}".format(service))
    else:
        zram_data = sum(device["data"] for device in devices)
        headroom_ok = (not devices or
                       mem.get("MemAvailable", 0) >= zram_data + _HEADROOM_BYTES)
        if not headroom_ok:
            report.add(
                LEVEL_WARN, "swap",
                "S2 refused: MemAvailable {} cannot absorb the {} of in-use "
                "zram data plus the 512 MiB safety margin (GUARDRAILS 6). "
                "Offering the reboot-apply path: disable "
                "nvzramconfig.service now, and zram is gone after the next "
                "reboot.".format(human_gib(mem.get("MemAvailable", 0)),
                                 human_gib(zram_data)))
            if service_enabled and _run_step(
                    runner, report, ctx,
                    "S2 (reboot-apply): disable nvzramconfig.service; zram "
                    "disappears at the next reboot.",
                    "S2 (reboot-apply): disable nvzramconfig.service.",
                    [["systemctl", "disable", "nvzramconfig.service"]]):
                commit({"nvzramconfig_was": service})
                report.add(LEVEL_PASS, "swap",
                           "S2 (reboot-apply) applied: nvzramconfig.service "
                           "disabled; reboot to drop zram")
        else:
            commands = []
            if service_enabled:
                commands.append(["systemctl", "disable", "nvzramconfig.service"])
                commands.append(["systemctl", "stop", "nvzramconfig.service"])
            commands.extend(["swapoff", device["name"]] for device in devices)
            if _run_step(
                    runner, report, ctx,
                    "S2: disable zram (service off, swapoff {} device(s); "
                    "MemAvailable {} covers the {} in use).".format(
                        len(devices), human_gib(mem.get("MemAvailable", 0)),
                        human_gib(zram_data)),
                    "S2: disable nvzramconfig and swapoff zram.",
                    commands):
                step_values = {"nvzramconfig_was": service}
                if devices:
                    step_values["zram_devices"] = [d["name"] for d in devices]
                commit(step_values)
                report.add(LEVEL_PASS, "swap", "S2 applied: zram disabled")

    # --- S3: NVMe swapfile -------------------------------------------------
    requested = size_gib * GIB
    file_swaps = [s for s in swaps if s["type"] == "file"]
    adequate = [s for s in file_swaps if s["size"] >= requested]
    if not root_source.startswith("/dev/nvme"):
        report.add(
            LEVEL_WARN, "swap",
            "S3 skipped: root is on {} (not NVMe); a swapfile there would "
            "wear the flash. S1 applies alone on SD-rooted boards.".format(
                root_source or "(unknown)"))
    elif adequate:
        report.add(
            LEVEL_PASS, "swap",
            "S3 skipped: active file-backed swap {} ({}) already covers the "
            "requested {} GiB; the tool never replaces swap it did not "
            "create".format(adequate[0]["name"], human_gib(adequate[0]["size"]),
                            size_gib))
    elif runner.file_exists(SWAPFILE):
        report.add(
            LEVEL_WARN, "swap",
            "S3 skipped: {} already exists but is not active swap; resolve "
            "it manually before applying".format(SWAPFILE))
    else:
        fstab_old = runner.read_file("/etc/fstab")
        fstab_new = fstab_add_line(fstab_old)
        # GUARDRAILS v1.2 invariant, independent of how fstab_new was built.
        assert_fstab_single_line_diff(fstab_old, fstab_new)
        staged = _stage(work_dir, "fstab.new", fstab_new)
        ctx["echo"]("staged {} adds exactly one line to /etc/fstab:".format(staged))
        ctx["echo"]("    " + FSTAB_LINE)
        step_backups = []
        if not dry_run:
            step_backups.append(backup_text("fstab", fstab_old, backups_dir))
        if _run_step(
                runner, report, ctx,
                "S3: create a {} GiB swapfile at {} and persist it in "
                "/etc/fstab.".format(size_gib, SWAPFILE),
                "S3: create and activate the {} GiB {}.".format(
                    size_gib, SWAPFILE),
                [["fallocate", "-l", "{}G".format(size_gib), SWAPFILE],
                 ["chmod", "600", SWAPFILE],
                 ["mkswap", SWAPFILE],
                 ["swapon", SWAPFILE],
                 ["cp", str(staged), "/etc/fstab"]]):
            commit({"swapfile_created": True, "fstab_line_added": True,
                    "swapfile_size_gib": size_gib}, step_backups)
            report.add(
                LEVEL_PASS, "swap",
                "S3 applied: {} GiB swapfile active at {}".format(
                    size_gib, SWAPFILE),
                details="A swapfile on NVMe preserves physical RAM for the "
                        "working set and for GPU unified-memory allocations, "
                        "which cannot be swapped, while still providing "
                        "overflow capacity.")


def undo(runner, report, ctx):
    """Restore the recorded pre-apply state (--undo swap) from the latest
    state.json entry. Touches only what apply recorded as changed."""
    entry = ctx["state"].latest("swap")
    if entry is None:
        report.add(LEVEL_WARN, "swap",
                   "nothing to undo: state.json has no recorded swap apply")
        return
    values = entry["values"]
    work_dir = Path(ctx["work_dir"])
    backups_dir = ctx["backups_dir"]

    if values.get("swapfile_created"):
        if _run_step(runner, report, ctx,
                     "Undo S3: deactivate and remove {}.".format(SWAPFILE),
                     "Undo S3: swapoff and remove {}.".format(SWAPFILE),
                     [["swapoff", SWAPFILE], ["rm", SWAPFILE]]):
            report.add(LEVEL_PASS, "swap", "undo: {} removed".format(SWAPFILE))

    if values.get("fstab_line_added"):
        fstab_old = runner.read_file("/etc/fstab")
        if FSTAB_TAG not in fstab_old:
            report.add(LEVEL_PASS, "swap",
                       "undo: tagged fstab line already absent; nothing to "
                       "remove")
        else:
            fstab_new = fstab_remove_line(fstab_old)
            assert_fstab_single_line_diff(fstab_old, fstab_new)
            staged = _stage(work_dir, "fstab.new", fstab_new)
            ctx["echo"]("staged {} removes exactly one line from "
                        "/etc/fstab:".format(staged))
            ctx["echo"]("    " + FSTAB_LINE)
            if not ctx["dry_run"]:
                backup_text("fstab", fstab_old, backups_dir)
            if _run_step(runner, report, ctx,
                         "Undo S3: remove the tagged line from /etc/fstab.",
                         "Undo S3: remove the tagged /etc/fstab line.",
                         [["cp", str(staged), "/etc/fstab"]]):
                report.add(LEVEL_PASS, "swap",
                           "undo: tagged fstab line removed")

    if values.get("nvzramconfig_was") == "enabled":
        if _run_step(runner, report, ctx,
                     "Undo S2: re-enable nvzramconfig.service (zram returns "
                     "at the next reboot).",
                     "Undo S2: re-enable nvzramconfig.service.",
                     [["systemctl", "enable", "nvzramconfig.service"]]):
            report.add(LEVEL_PASS, "swap",
                       "undo: nvzramconfig.service re-enabled; reboot to "
                       "restore zram")

    prior = values.get("prior_swappiness")
    if prior is not None:
        commands = [["sysctl", "-w", "vm.swappiness={}".format(prior)]]
        restore_conf = None
        if values.get("sysctl_conf_written"):
            if values.get("sysctl_conf_existed"):
                backup = next((b for b in entry.get("backups", [])
                               if "99-jetson-postboot.conf" in b), None)
                if backup and Path(backup).is_file():
                    restore_conf = _stage(
                        work_dir, "99-jetson-postboot.conf",
                        Path(backup).read_text(encoding="utf-8"))
                    commands.append(["cp", str(restore_conf), SYSCTL_CONF])
                else:
                    report.add(LEVEL_WARN, "swap",
                               "undo: prior {} existed but its backup was "
                               "not found; restore it manually".format(
                                   SYSCTL_CONF))
            else:
                commands.append(["rm", SYSCTL_CONF])
        if _run_step(runner, report, ctx,
                     "Undo S1: restore vm.swappiness={} and the prior "
                     "sysctl.d state.".format(prior),
                     "Undo S1: restore vm.swappiness={}.".format(prior),
                     commands):
            report.add(LEVEL_PASS, "swap",
                       "undo: vm.swappiness restored to {}".format(prior))
