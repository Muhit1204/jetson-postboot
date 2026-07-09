"""Tier 0 system identity checks: board model, L4T/JetPack, memory, power mode.

All parsers are developed against the captured orin-nano-8gb fixture set
(GUARDRAILS 5.3). Everything here is informational PASS output except an
unrecognized L4T release, which warns that the tool degrades to
detection-only (PLAN section 3).
"""

import re

from jetson_postboot.lib.report import LEVEL_PASS, LEVEL_WARN, human_gib

# PLAN.md 6.2: L4T major release to JetPack generation.
_L4T_TO_JETPACK = {
    "36": "JetPack 6.x",
    "35": "JetPack 5.x",
}

_MEMINFO_KEYS = ("MemTotal", "MemAvailable", "SwapTotal")


def jetpack_for_l4t(l4t_major):
    """Map an L4T major release (e.g. '36') to its JetPack generation.

    Returns None for releases outside the supported table; callers degrade
    to detection-only per PLAN.md section 3.
    """
    return _L4T_TO_JETPACK.get(str(l4t_major))


def parse_board_model(text):
    """Board name from /proc/device-tree/model (NUL-terminated on hardware)."""
    return text.replace("\x00", "").strip()


def parse_l4t_release(text):
    """'36.4.7' from the '# R36 (release), REVISION: 4.7, ...' header line.

    Returns None when the header is unrecognizable.
    """
    match = re.search(r"#\s*R(\d+)\s*\(release\),\s*REVISION:\s*([\d.]+)", text)
    if not match:
        return None
    return "{}.{}".format(match.group(1), match.group(2))


def parse_meminfo(text):
    """MemTotal/MemAvailable/SwapTotal from /proc/meminfo, in bytes."""
    values = {}
    for line in text.splitlines():
        match = re.match(r"(\w+):\s+(\d+)\s*kB", line)
        if match and match.group(1) in _MEMINFO_KEYS:
            values[match.group(1)] = int(match.group(2)) * 1024
    return values


def parse_nvpmodel(text):
    """Power mode name from 'NV Power Mode: <name>' (nvpmodel -q output)."""
    match = re.search(r"NV Power Mode:\s*(\S+)", text)
    return match.group(1) if match else None


def check(runner, report):
    """Tier 0 identity findings for the report (registry entry)."""
    board = parse_board_model(runner.read_file("/proc/device-tree/model"))
    report.add(LEVEL_PASS, "system", "board: {}".format(board or "unknown"))

    l4t = parse_l4t_release(runner.read_file("/etc/nv_tegra_release"))
    if l4t is None:
        report.add(LEVEL_WARN, "system",
                   "could not parse /etc/nv_tegra_release; detection-only mode")
    else:
        jetpack = jetpack_for_l4t(l4t.split(".")[0])
        if jetpack is None:
            report.add(LEVEL_WARN, "system",
                       "L4T r{} is outside the supported table; "
                       "detection-only mode".format(l4t))
        else:
            report.add(LEVEL_PASS, "system",
                       "Jetson software: L4T r{} ({})".format(l4t, jetpack))

    mem = parse_meminfo(runner.read_file("/proc/meminfo"))
    if mem:
        report.add(LEVEL_PASS, "system",
                   "memory: {} of RAM total (MemTotal), {} free right now "
                   "(MemAvailable), {} of swap space (SwapTotal)".format(
                       human_gib(mem.get("MemTotal", 0)),
                       human_gib(mem.get("MemAvailable", 0)),
                       human_gib(mem.get("SwapTotal", 0))))

    mode_result = runner.run(["nvpmodel", "-q"], sudo=True)
    if mode_result.returncode != 0:
        report.add(LEVEL_WARN, "system",
                   "power mode unknown (nvpmodel -q failed: {})".format(
                       mode_result.stderr.strip() or
                       "rc={}".format(mode_result.returncode)))
    else:
        mode = parse_nvpmodel(mode_result.stdout)
        if mode:
            report.add(LEVEL_PASS, "system",
                       "power mode: {} (this sets how much speed and power "
                       "the board uses)".format(mode))

    python_result = runner.run(["python3", "--version"])
    report.add(LEVEL_PASS, "system", python_result.stdout.strip())
