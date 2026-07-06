# Fixture capture instructions (Phase 0 gate task)

Run the block below **on the Jetson Orin Nano**, then copy the resulting
folder contents into this directory and fill in `manifest.json` from the
template at the bottom. Development never touches the hardware; these files
are the only ground truth the parsers are built against (GUARDRAILS 5.3).

Notes before you start:

- Assumes root is on `/dev/nvme0n1`. If your disk differs, change the
  `sfdisk` line and keep the changed command string in `manifest.json`
  exactly as you actually ran it (the manifest key must match what the tool
  will invoke).
- Some commands are EXPECTED to fail (e.g. `which ollama` before Ollama is
  installed). That is fine: their exit code lands in a `.rc` file, and you
  record it in the manifest using the object form
  `{"stdout": "<file>", "returncode": <rc>}`.
- Do not prettify or edit outputs. Raw bytes in, raw bytes stored.

## Capture block (run on the Jetson)

```bash
mkdir -p ~/postboot-fixture
cd ~/postboot-fixture

python3 --version > python3-version.txt 2>&1
cat /etc/nv_tegra_release > nv_tegra_release.txt
tr -d '\0' < /proc/device-tree/model > device-tree-model.txt
lsblk -b -J -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,PTTYPE > lsblk.json
sudo sfdisk -d /dev/nvme0n1 > sfdisk-dump.txt
df -B1 --output=source,size,used,avail,target / > df-root.txt
findmnt -no SOURCE / > findmnt-root.txt
swapon --show --bytes > swapon-show.txt
zramctl --bytes > zramctl.txt
cat /proc/sys/vm/swappiness > swappiness.txt
cat /proc/meminfo > meminfo.txt
cat /boot/extlinux/extlinux.conf > extlinux.conf.txt
systemctl is-enabled nvzramconfig.service > nvzramconfig-enabled.txt; echo $? > nvzramconfig-enabled.rc
nvcc --version > nvcc-version.txt 2>&1; echo $? > nvcc-version.rc
dpkg -l | grep -i cudnn > dpkg-cudnn.txt; echo $? > dpkg-cudnn.rc
ls -l /usr/local | grep -i cuda > usr-local-ls.txt
readlink -f /usr/local/cuda > usr-local-cuda-target.txt
sudo nvpmodel -q > nvpmodel-q.txt
which ollama > which-ollama.txt; echo $? > which-ollama.rc
```

`readlink -f /usr/local/cuda` is one addition beyond the PLAN.md section 7
list: the tool reads that symlink's target (not the directory contents), so
simulate mode needs the target string. `usr-local-ls.txt` is kept as
human-readable context only.

Then copy everything here, e.g. from your workstation:

```bash
scp -r <jetson-user>@<jetson-ip>:~/postboot-fixture/* tests/fixtures/orin-nano-8gb/
```

## manifest.json template

Replace this directory's placeholder `manifest.json` with the following,
filling `captured` with the date and each `returncode` from the matching
`.rc` file. `"commands"` keys are the exact strings the tool invokes
(including the `sudo` prefix where shown). `"files"` keys are absolute paths
the tool reads directly instead of shelling out to `cat`.

```json
{
  "board": "orin-nano-8gb",
  "captured": "YYYY-MM-DD",
  "commands": {
    "python3 --version": "python3-version.txt",
    "lsblk -b -J -o NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,PTTYPE": "lsblk.json",
    "sudo sfdisk -d /dev/nvme0n1": "sfdisk-dump.txt",
    "df -B1 --output=source,size,used,avail,target /": "df-root.txt",
    "findmnt -no SOURCE /": "findmnt-root.txt",
    "swapon --show --bytes": "swapon-show.txt",
    "zramctl --bytes": "zramctl.txt",
    "systemctl is-enabled nvzramconfig.service": {"stdout": "nvzramconfig-enabled.txt", "returncode": 0},
    "nvcc --version": {"stdout": "nvcc-version.txt", "returncode": 0},
    "dpkg -l": "dpkg-cudnn.txt",
    "sudo nvpmodel -q": "nvpmodel-q.txt",
    "which ollama": {"stdout": "which-ollama.txt", "returncode": 1}
  },
  "files": {
    "/etc/nv_tegra_release": "nv_tegra_release.txt",
    "/proc/device-tree/model": "device-tree-model.txt",
    "/proc/sys/vm/swappiness": "swappiness.txt",
    "/proc/meminfo": "meminfo.txt",
    "/boot/extlinux/extlinux.conf": "extlinux.conf.txt",
    "/usr/local/cuda": "usr-local-cuda-target.txt"
  }
}
```

The `"dpkg -l"` entry points at the grep-filtered capture: the tool runs
plain `dpkg -l` and filters for cudnn in Python, so a filtered fixture is a
valid subset. Keep the `.rc` files in the directory; they document where the
returncodes came from.

Derived variants (low-MemAvailable, partition-after-root, root-on-SD,
root-mismatch) are Phase 1/2 work: they will be created by copying this set
and editing single values, each documented in its own folder.

## Phase 1 additions (captured 2026-07-05, on the board, unprivileged)

Three entries beyond the original list, needed by boot_advisor:

```bash
blkid -U <root-uuid-from-extlinux> > blkid-uuid-root.txt
which efibootmgr > which-efibootmgr.txt; echo $? > which-efibootmgr.rc
efibootmgr > efibootmgr.txt
```

The blkid manifest key embeds this board's actual root UUID because the
tool constructs the command from the UUID it parses out of extlinux.conf;
simulate-mode key equality then proves the parse was right.
