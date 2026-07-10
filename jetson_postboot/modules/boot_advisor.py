"""Boot advisor: Tier 3, read-only forever (PLAN 6.5, GUARDRAILS 3.4).

Compares the root the bootloader is configured to use (APPEND root= in
/boot/extlinux/extlinux.conf) against the root actually mounted, and issues
one of three verdicts. It also reads the UEFI boot order (efibootmgr) and
warns when a removable-media entry (SD / MMC / USB) is still tried before
the entry the board actually started from - the classic post-migration trap
where a re-inserted SD card silently wins the next boot. It never writes to
/boot or the firmware; on a mismatch it prints the exact suggested fix and
saves a copy of extlinux.conf into ./backups/ for the user. A guard test
rejects any mutate= keyword here.
"""

import json
import re

from jetson_postboot.lib.backup import backup_text
from jetson_postboot.lib.report import LEVEL_PASS, LEVEL_WARN, human_gib

_EXTLINUX = "/boot/extlinux/extlinux.conf"
# README references this by name; PLAN 6.5 verdict (b).
_MIGRATION_ADVISORY = (
    "Root runs from the SD card while a larger NVMe drive sits idle. "
    "v1 never migrates automatically; follow the JetsonHacks migration "
    "procedure (jetsonhacks.com, 'Jetson Orin Nano boot from NVMe') "
    "and re-run this tool afterwards.")
# Firmware entry labels that identify removable / migration-source media.
# Word-bounded so drive model strings ("WD Green SN3000") never match.
_REMOVABLE_LABEL_RE = re.compile(r"\b(SD|MMC|eMMC|USB)\b", re.IGNORECASE)


def parse_configured_root(text):
    """('UUID', '52d1...') / ('PARTUUID', ...) / ('DEV', '/dev/...') from the
    first APPEND line carrying root=; None when no root= is configured."""
    match = re.search(r"^\s*APPEND\s+.*?\broot=(\S+)", text, re.MULTILINE)
    if not match:
        return None
    token = match.group(1)
    for kind in ("UUID", "PARTUUID"):
        if token.startswith(kind + "="):
            return kind, token[len(kind) + 1:]
    return "DEV", token


def resolve_root(runner, kind, value):
    """Device path for a configured root, via blkid when needed; None when
    the reference resolves to nothing (rc != 0 or empty output)."""
    if kind == "DEV":
        return value
    if kind == "UUID":
        result = runner.run(["blkid", "-U", value])
    else:  # PARTUUID
        result = runner.run(["blkid", "-t", "PARTUUID={}".format(value),
                             "-o", "device"])
    device = result.stdout.strip()
    return device if result.returncode == 0 and device else None


def suggested_append_line(append_line, mounted_device):
    """The APPEND line with only its root= token replaced."""
    return re.sub(r"\broot=\S+", "root={}".format(mounted_device),
                  append_line)


def parse_efibootmgr(text):
    """{'current': '0008', 'order': ['0008', ...], 'entries': {id: label}}
    from efibootmgr output. Missing pieces come back as None / empty, so
    callers can degrade to the plain reference listing."""
    current = re.search(r"^BootCurrent:\s*([0-9A-Fa-f]{4})", text, re.MULTILINE)
    order = re.search(r"^BootOrder:\s*(\S+)", text, re.MULTILINE)
    entries = dict(re.findall(r"^Boot([0-9A-Fa-f]{4})\*?\s+(.+?)\s*$",
                              text, re.MULTILINE))
    return {
        "current": current.group(1) if current else None,
        "order": order.group(1).split(",") if order else [],
        "entries": entries,
    }


def stale_entries_before_current(parsed):
    """(id, label) pairs for removable-media entries (SD / MMC / USB) that
    the saved boot order tries before the entry the board actually started
    from (BootCurrent). Non-empty means a re-inserted migration-source card
    would silently win the next boot."""
    current = parsed["current"]
    if current is None or current not in parsed["order"]:
        return []
    offenders = []
    for boot_id in parsed["order"]:
        if boot_id == current:
            break
        label = parsed["entries"].get(boot_id, "")
        if _REMOVABLE_LABEL_RE.search(label):
            offenders.append((boot_id, label))
    return offenders


def suggested_boot_order(parsed):
    """The saved order with the entry actually booted moved to the front."""
    current = parsed["current"]
    return [current] + [b for b in parsed["order"] if b != current]


def _append_line(text):
    match = re.search(r"^\s*APPEND\s+.*$", text, re.MULTILINE)
    return match.group(0) if match else None


def _disks(runner):
    lsblk = json.loads(runner.run(
        ["lsblk", "-b", "-J", "-o",
         "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,PTTYPE"]).stdout)
    return [d for d in lsblk.get("blockdevices", []) if d.get("type") == "disk"]


def check(runner, report, backups_dir=None):
    """Tier 3 boot verdict findings (registry entry)."""
    extlinux = runner.read_file(_EXTLINUX)
    mounted = runner.run(["findmnt", "-no", "SOURCE", "/"]).stdout.strip()

    configured = parse_configured_root(extlinux)
    if configured is None:
        report.add(LEVEL_WARN, "boot",
                   "no root= found in {}; cannot compare configured and "
                   "mounted root".format(_EXTLINUX))
        _efibootmgr(runner, report)
        return

    kind, value = configured
    resolved = resolve_root(runner, kind, value)

    if resolved == mounted:
        if mounted.startswith("/dev/mmcblk"):
            nvme_disks = [d for d in _disks(runner)
                          if d["name"].startswith("nvme")]
            if nvme_disks:
                report.add(
                    LEVEL_WARN, "boot",
                    "root {} is on the SD card while NVMe {} ({}) is "
                    "present.".format(mounted, nvme_disks[0]["name"],
                                      human_gib(nvme_disks[0]["size"])),
                    details=_MIGRATION_ADVISORY)
                _efibootmgr(runner, report)
                return
        report.add(LEVEL_PASS, "boot",
                   "configured root ({}={}) matches the mounted root {}: the "
                   "board is set to start from the same drive it actually "
                   "runs on, which is the normal, correct layout for this "
                   "devkit".format(kind, value, mounted))
    else:
        line = _append_line(extlinux) or ""
        details = [
            "configured: {}={} -> {}".format(
                kind, value, resolved or "(resolves to nothing)"),
            "mounted:    {}".format(mounted),
            "suggested extlinux line (edit {} yourself; this tool never "
            "writes to /boot):".format(_EXTLINUX),
            suggested_append_line(line, mounted).strip(),
        ]
        if backups_dir is not None:
            copy = backup_text("extlinux.conf", extlinux, backups_dir)
            details.append("current extlinux.conf saved to {}".format(copy))
        report.add(LEVEL_WARN, "boot",
                   "configured root and mounted root disagree",
                   details="\n".join(details))

    _efibootmgr(runner, report)


def _efibootmgr(runner, report):
    """Boot-order verdict from read-only efibootmgr output (PLAN 6.5).

    After an SD-to-NVMe migration the firmware's saved boot order often
    still tries the SD card first; the board then silently starts the old
    system whenever a card is inserted. This warns about that layout and
    prints the exact manual fix. The tool itself never changes the boot
    order: the runner allowlist rejects efibootmgr with any argument
    beyond -v, so this stays Tier 3 read-only forever.
    """
    if runner.run(["which", "efibootmgr"]).returncode != 0:
        return
    result = runner.run(["efibootmgr"])
    if result.returncode != 0 or not result.stdout.strip():
        return
    head = result.stdout.strip().splitlines()

    parsed = parse_efibootmgr(result.stdout)
    offenders = stale_entries_before_current(parsed)
    if offenders:
        offender_id, offender_label = offenders[0]
        current_label = parsed["entries"].get(parsed["current"], "")
        suggested = ",".join(suggested_boot_order(parsed))
        details = [
            "saved startup order: {}".format(",".join(parsed["order"])),
            "  tries {} ({}) before {} ({})".format(
                offender_id, offender_label,
                parsed["current"], current_label),
            "fix, option 1 - one command (this tool never runs it for you):",
            "  sudo efibootmgr -o {}".format(suggested),
            "fix, option 2 - in the startup menu: press Esc while the board",
            "  starts, then Boot Maintenance Manager > Boot Options >",
            "  Change Boot Order, and move '{}' to the top.".format(
                current_label),
        ]
        report.add(
            LEVEL_WARN, "boot",
            "the board started from '{}' this time, but the saved startup "
            "order still tries '{}' first. After migrating from an SD card, "
            "change the boot order so the new drive comes first - otherwise "
            "the board will silently start the old system whenever that "
            "card or stick is inserted.".format(current_label,
                                                offender_label),
            details="\n".join(details))
        return

    report.add(LEVEL_PASS, "boot",
               "boot menu entries (from efibootmgr, read-only - shown "
               "for reference, nothing here needs changing): {}".format(
                   head[0]),
               details="\n".join(head[1:6]))
