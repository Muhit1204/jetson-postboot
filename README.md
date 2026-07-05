# jetson-postboot

Post-first-boot setup and repair assistant for NVIDIA Jetson devices.
Primary target: Jetson Orin Nano Developer Kit 8 GB (JetPack 6.x).

Status: **Phase 0 scaffold complete.** Detection and apply modules land in
later phases; full documentation lands in Phase 5. See PLAN.md for the
roadmap and GUARDRAILS.md for the safety rules that bind every change.

## Requirements

- git
- Python 3.8 or newer
- a user account with sudo rights (never run the tool itself as root)

## Quickstart (current phase)

```
python3 postboot.py                                  # Tier 0 report (empty in Phase 0)
python3 postboot.py --simulate tests/fixtures/orin-nano-8gb
python3 -m unittest discover -s tests -t .           # test suite
```

## What this tool will never touch

No flashing, no firmware, no QSPI or UEFI variables, no writes to /boot or
extlinux.conf (boot issues are reported with exact manual steps instead),
no partition table or filesystem changes of any kind (undersized cloned
disks are reported with the exact manual extend commands instead), no
third-party Python dependencies. Everything the tool produces stays inside
this directory (./logs, ./backups, ./reports, ./state, ./downloads).
