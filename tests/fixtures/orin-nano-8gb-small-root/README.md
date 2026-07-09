# Derived fixture variant: small-root

Copy of orin-nano-8gb (captured 2026-07-05) with these edits and nothing
else, per PLAN.md section 7 (derived variants):

- sfdisk-dump.txt: nvme0n1p1 size 973715456 -> 134217728 sectors (64 GiB),
  simulating a root cloned from a 64 GB SD card onto the 500 GB NVMe.
  Trailing gap becomes 839497743 sectors = 429,822,844,416 bytes
  (429.8 GB / 400.3 GiB) - the PLAN section 8 "about 430 GB reclaimable"
  acceptance case.
- lsblk.json: nvme0n1p1 size -> 68719476736 bytes to match.
- df-root.txt: filesystem sized ~63 GiB with plausible used/avail.

Exercises the storage advisory ACTION path: all preconditions hold, the
exact manual growpart/resize2fs sequence is printed.
