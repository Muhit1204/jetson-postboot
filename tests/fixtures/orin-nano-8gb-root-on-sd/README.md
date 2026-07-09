# Derived fixture variant: root-on-SD

Copy of orin-nano-8gb (captured 2026-07-05) with these edits and nothing
else, per PLAN.md section 7 (derived variants):

- findmnt-root.txt and blkid-uuid-root.txt: root now resolves to
  /dev/mmcblk1p1 (a 64 GB SD card).
- lsblk.json: mmcblk1 disk added holding the mounted root; nvme0n1p1
  unmounted (the 500 GB NVMe idles).

Exercises boot_advisor verdict (b): root on SD while a larger NVMe is
present -> WARN with the migration advisory (JetsonHacks procedure),
nothing executed.
