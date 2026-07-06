"""Boot advisor: Tier 3, read-only forever (PLAN 6.5, GUARDRAILS 3.4).

Compares the root the bootloader is configured to use (APPEND root= in
/boot/extlinux/extlinux.conf) against the root actually mounted, and issues
one of three verdicts. It never writes to /boot; on a mismatch it prints the
exact suggested extlinux line and saves a copy of extlinux.conf into
./backups/ for the user. A guard test rejects any mutate= keyword here.
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
                   "configured root ({}={}) matches mounted root {}; "
                   "expected layout".format(kind, value, mounted))
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
    """Include read-only efibootmgr output when the binary exists."""
    if runner.run(["which", "efibootmgr"]).returncode != 0:
        return
    result = runner.run(["efibootmgr"])
    if result.returncode == 0 and result.stdout.strip():
        head = result.stdout.strip().splitlines()
        report.add(LEVEL_PASS, "boot",
                   "efibootmgr (read-only): {}".format(head[0]),
                   details="\n".join(head[1:6]))
