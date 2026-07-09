"""Builder for the Phase 2 derived fixture variants (run from
tests/fixtures/). Documented per PLAN 7: full copies of the captured base
set with single-purpose edits; zram tables are CRAFTED (synthetic shape,
see O7), not board ground truth.

- orin-nano-8gb-untuned-default: zram at the JetPack default (6 devices,
  ~3.6 GiB total, ~49% of RAM), nvzramconfig enabled, no file swap ->
  the full S1-S3 apply path fires.
- orin-nano-8gb-low-memavail: same, but ~2.5 GiB of stored zram data and
  MemAvailable lowered to 600 MiB -> the 512 MiB headroom invariant
  refuses S2 and offers the reboot-apply path.

Both manifests gain the Phase 2 mutation/undo command entries as
returncode-0/no-stdout records: mutation commands print nothing the tool
parses, so no output is invented (GUARDRAILS 5.3).
"""

import json
import re
import shutil
from pathlib import Path

BASE = Path("orin-nano-8gb")

MUTATIONS = [
    "sudo sysctl -w vm.swappiness=10",
    "sudo cp ./.work/99-jetson-postboot.conf /etc/sysctl.d/99-jetson-postboot.conf",
    "sudo systemctl disable nvzramconfig.service",
    "sudo systemctl stop nvzramconfig.service",
] + ["sudo swapoff /dev/zram{}".format(i) for i in range(6)] + [
    "sudo fallocate -l 8G /swapfile",
    "sudo chmod 600 /swapfile",
    "sudo mkswap /swapfile",
    "sudo swapon /swapfile",
    "sudo cp ./.work/fstab.new /etc/fstab",
    # undo path
    "sudo sysctl -w vm.swappiness=60",
    "sudo rm /etc/sysctl.d/99-jetson-postboot.conf",
    "sudo systemctl enable nvzramconfig.service",
    "sudo swapoff /swapfile",
    "sudo rm /swapfile",
]


def build(name):
    dst = Path(name)
    if dst.exists():
        shutil.rmtree(str(dst))
    shutil.copytree(str(BASE), str(dst))
    (dst / "CAPTURE.md").unlink()
    return dst


def zramctl_table(data_bytes):
    header = ("NAME       ALGORITHM DISKSIZE    DATA COMPR TOTAL "
              "STREAMS MOUNTPOINT")
    rows = ["/dev/zram{} lzo-rle   649068544 {} {} {} 6 [SWAP]".format(
        i, data_bytes, max(74, data_bytes // 3), max(12288, data_bytes // 3))
        for i in range(6)]
    return header + "\n" + "\n".join(rows) + "\n"


def swapon_table():
    header = "NAME       TYPE      SIZE USED PRIO"
    rows = ["/dev/zram{} partition 649068544 0 5".format(i) for i in range(6)]
    return header + "\n" + "\n".join(rows) + "\n"


def rewire(dst, data_bytes):
    (dst / "zramctl.txt").write_text(zramctl_table(data_bytes), encoding="utf-8")
    (dst / "swapon-show.txt").write_text(swapon_table(), encoding="utf-8")
    (dst / "nvzramconfig-enabled.txt").write_text("enabled\n", encoding="utf-8")
    (dst / "nvzramconfig-enabled.rc").write_text("0\n", encoding="utf-8")
    mpath = dst / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    manifest["commands"]["systemctl is-enabled nvzramconfig.service"] = {
        "stdout": "nvzramconfig-enabled.txt", "returncode": 0}
    for cmd in MUTATIONS:
        manifest["commands"][cmd] = {"returncode": 0}
    mpath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main():
    rewire(build("orin-nano-8gb-untuned-default"), 4096)

    low = build("orin-nano-8gb-low-memavail")
    rewire(low, 450000000)  # 6 x 450 MB = ~2.51 GiB stored zram data
    meminfo = (low / "meminfo.txt").read_text(encoding="utf-8")
    meminfo, count = re.subn(r"MemAvailable:\s+\d+ kB",
                             "MemAvailable:     614400 kB", meminfo)
    assert count == 1
    (low / "meminfo.txt").write_text(meminfo, encoding="utf-8")

    total = 649068544 * 6
    print("zram total {} bytes, ratio to MemTotal {:.3f}".format(
        total, total / 7990026240.0))
    print("low-memavail: data {} vs MemAvailable {}".format(
        450000000 * 6, 614400 * 1024))


if __name__ == "__main__":
    main()
