"""Storage module: Tier 0 detection, Tier 3 advisory only, read-only forever.

Demoted from Tier 2 confirmed-apply on 2026-07-04 (GUARDRAILS/PLAN v1.1, at
Munta's request): this module never executes growpart, resize2fs, sgdisk -e,
or apt-get. It detects trailing unallocated space, checks the preconditions a
manual extend would need, and prints the exact command sequence for the user
to run themselves - the same pattern as boot_advisor. A guard test rejects
any call in this file that passes a `mutate` keyword to the runner.

Geometry comes from the sfdisk -d dump plus lsblk: the GPT secondary header
sits at the disk end exactly when the dump's last-lba reaches
disk_sectors - 34, so no sgdisk -p fixture is needed (see PROJECT_CONTEXT
D18).
"""

import json
import re

from jetson_postboot.lib.report import (LEVEL_ACTION, LEVEL_PASS,
                                        LEVEL_WARN, human_gib)

# PLAN 6.3: the finding fires above 1 GiB of trailing unallocated space.
_RECLAIM_THRESHOLD = 1024 ** 3
# GPT reserves 33 sectors for the secondary table + header; the last usable
# LBA of a full-size GPT is therefore total_sectors - 34.
_GPT_TAIL_SECTORS = 34

_PARTITION_RE = re.compile(r"^(/dev/(?:nvme\d+n\d+|mmcblk\d+))p(\d+)$")
_SIMPLE_PART_RE = re.compile(r"^(/dev/[a-z]+?)(\d+)$")


def split_partition(source):
    """('/dev/nvme0n1', 1) from '/dev/nvme0n1p1'; None for non-partitions."""
    match = _PARTITION_RE.match(source) or _SIMPLE_PART_RE.match(source)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def parse_sfdisk_dump(text):
    """Geometry from `sfdisk -d`: header fields plus a partition table dict
    keyed by device path, each entry holding integer start and size sectors."""
    dump = {"partitions": {}}
    for line in text.splitlines():
        header = re.match(r"^(label|device|unit)\s*:\s*(\S+)", line)
        if header:
            dump[header.group(1)] = header.group(2)
            continue
        number = re.match(r"^(first-lba|last-lba|sector-size)\s*:\s*(\d+)", line)
        if number:
            dump[number.group(1).replace("-", "_")] = int(number.group(2))
            continue
        part = re.match(r"^(/dev/\S+)\s*:\s*(.*)$", line)
        if part:
            fields = {}
            for key, value in re.findall(r"(\w+)=\s*([^,]+)", part.group(2)):
                fields[key] = value.strip()
            dump["partitions"][part.group(1)] = {
                "start": int(fields["start"]),
                "size": int(fields["size"]),
            }
    dump.setdefault("sector_size", 512)
    return dump


def _partition_end(dump, device):
    part = dump["partitions"][device]
    return part["start"] + part["size"] - 1


def trailing_gap_bytes(dump, root_device):
    """Bytes between the root partition's end and the last usable LBA."""
    return (dump["last_lba"] - _partition_end(dump, root_device)) * dump["sector_size"]


def partitions_after(dump, root_device):
    """Devices whose partitions start beyond the root partition's end."""
    root_end = _partition_end(dump, root_device)
    return sorted(device for device, part in dump["partitions"].items()
                  if part["start"] > root_end)


def header_at_end(dump, disk_size_bytes):
    """True when the GPT secondary header already sits at the disk end."""
    disk_sectors = disk_size_bytes // dump["sector_size"]
    return dump["last_lba"] >= disk_sectors - _GPT_TAIL_SECTORS


def advisory_sequence(disk, partnum, partition, header_at_end):
    """The exact manual command sequence for the user to run (PLAN 6.3).
    The tool itself never executes any of these (Tier 3)."""
    lines = [
        "Run these commands yourself, in order:",
        "  1. Back up the partition table first:",
        "     sudo sfdisk -d {} > partition-table-backup.txt".format(disk),
    ]
    step = 2
    if not header_at_end:
        lines += [
            "  {}. Move the GPT secondary header to the disk end:".format(step),
            "     sudo sgdisk -e {}".format(disk),
        ]
        step += 1
    lines += [
        "  {}. Grow the partition (needs cloud-guest-utils; if growpart is".format(step),
        "     missing: sudo apt-get install cloud-guest-utils):",
        "     sudo growpart {} {}".format(disk, partnum),
        "  {}. Grow the filesystem (online resize; safe while mounted):".format(step + 1),
        "     sudo resize2fs {}".format(partition),
        "Never run e2fsck on a mounted filesystem.",
    ]
    return "\n".join(lines)


def _lsblk_node(lsblk, name):
    stack = list(lsblk.get("blockdevices", []))
    while stack:
        node = stack.pop()
        if node.get("name") == name:
            return node
        stack.extend(node.get("children", []))
    return None


def check(runner, report):
    """Tier 0 detection + Tier 3 advisory findings (registry entry)."""
    source = runner.run(["findmnt", "-no", "SOURCE", "/"]).stdout.strip()
    split = split_partition(source)
    if split is None:
        report.add(LEVEL_WARN, "storage",
                   "root source {} is not a recognizable partition; "
                   "skipping extend advisory".format(source or "(unknown)"))
        return
    disk, partnum = split

    lsblk = json.loads(runner.run(
        ["lsblk", "-b", "-J", "-o",
         "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,PTTYPE"]).stdout)
    disk_node = _lsblk_node(lsblk, disk.split("/")[-1]) or {}
    root_node = _lsblk_node(lsblk, source.split("/")[-1]) or {}

    df_lines = runner.run(
        ["df", "-B1", "--output=source,size,used,avail,target", "/"]
    ).stdout.splitlines()
    fs_size = int(df_lines[1].split()[1]) if len(df_lines) > 1 else 0

    report.add(LEVEL_PASS, "storage",
               "disk {} {}, root partition {}, filesystem {}".format(
                   disk, human_gib(disk_node.get("size", 0)),
                   human_gib(root_node.get("size", 0)), human_gib(fs_size)))

    dump = parse_sfdisk_dump(
        runner.run(["sfdisk", "-d", disk], sudo=True).stdout)
    if source not in dump["partitions"]:
        report.add(LEVEL_WARN, "storage",
                   "root partition {} not found in the {} partition table; "
                   "skipping extend advisory".format(source, disk))
        return

    gap = trailing_gap_bytes(dump, source)
    if gap <= _RECLAIM_THRESHOLD:
        report.add(LEVEL_PASS, "storage",
                   "root partition already spans the disk "
                   "({} unallocated behind it)".format(human_gib(gap)))
        return

    blockers = partitions_after(dump, source)
    if blockers:
        report.add(LEVEL_WARN, "storage",
                   "unallocated space exists but cannot be reclaimed by "
                   "extending: partition {} sits after the root partition. "
                   "No command sequence applies.".format(blockers[0]))
        return
    if root_node.get("fstype") != "ext4":
        report.add(LEVEL_WARN, "storage",
                   "root filesystem is {} (not ext4); the advisory covers "
                   "ext4 only. No command sequence applies.".format(
                       root_node.get("fstype") or "unknown"))
        return

    report.add(
        LEVEL_ACTION, "storage",
        "{:.1f} GB ({}) of unallocated space sits behind the root "
        "partition and can be reclaimed by extending it (advisory only; "
        "the tool never runs these commands).".format(
            gap / 1e9, human_gib(gap)),
        details=advisory_sequence(
            disk, partnum, source,
            header_at_end(dump, disk_node.get("size", 0))))
