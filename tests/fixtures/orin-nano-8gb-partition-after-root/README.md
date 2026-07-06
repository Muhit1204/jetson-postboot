# Derived fixture variant: partition-after-root

Copy of orin-nano-8gb (captured 2026-07-05) with these edits and nothing
else, per PLAN.md section 7 (derived variants):

- sfdisk-dump.txt: nvme0n1p1 shrunk to 134217728 sectors (64 GiB) and a
  crafted nvme0n1p16 ("user-data", 10 GiB, start sector 200000000) added
  AFTER the root partition's end.
- lsblk.json: p1 size matched; nvme0n1p16 node added.
- df-root.txt: filesystem sized to the shrunken partition.

Exercises the storage advisory failed-precondition path: root is no longer
the last partition on the disk, so the module must explain that and print
NO command sequence (PLAN 6.3 acceptance).
