# Derived fixture variant: low-memavail

Copy of orin-nano-8gb (captured 2026-07-05) edited exactly like
orin-nano-8gb-untuned-default (see that README; same builder script),
plus two pressure edits:

- zramctl.txt: DATA raised to 6 x 450 MB = 2.51 GiB of stored zram data.
- meminfo.txt: MemAvailable lowered to 614400 kB (600 MiB).

600 MiB < 2.51 GiB + 512 MiB, so deactivating zram would not fit in RAM:
the PLAN 6.4 / GUARDRAILS 6 headroom invariant must refuse the swapoff
and offer the reboot-apply path instead. This is the crafted
low-MemAvailable variant PLAN section 7 calls for.
