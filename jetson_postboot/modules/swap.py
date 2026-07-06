"""Swap module: Tier 0 detection (Phase 1); Tier 1 apply/undo arrive in Phase 2.

Detection per PLAN 6.4: swapon --show --bytes, zramctl --bytes, vm.swappiness,
systemctl is-enabled nvzramconfig.service. The finding fires when swappiness
exceeds 10 or zram total sits at the JetPack default of about half of RAM.
"""

from jetson_postboot.checks.system_info import parse_meminfo
from jetson_postboot.lib.report import (LEVEL_ACTION, LEVEL_PASS,
                                        LEVEL_WARN, human_gib)

# PLAN 6.4 / GUARDRAILS 6: the profile's target swappiness; anything above
# triggers the finding.
RECOMMENDED_SWAPPINESS = 10
# JetPack sizes zram at about half of physical RAM; treat 40-60% as "at the
# default" to absorb rounding across boards.
_DEFAULT_ZRAM_BAND = (0.40, 0.60)


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
