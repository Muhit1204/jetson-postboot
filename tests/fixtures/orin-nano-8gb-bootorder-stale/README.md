# Derived fixture variant: bootorder-stale

Copy of orin-nano-8gb (captured 2026-07-05) with these edits and nothing
else, per PLAN.md section 7 (derived variants):

- efibootmgr.txt: a crafted `Boot0001* UEFI SD Device` entry added, and
  BootOrder changed to try 0001 before 0008 (the NVMe entry the board
  actually started from; BootCurrent stays 0008).

Exercises the boot advisor's stale-boot-order warning: root runs from the
NVMe (verdict (a) still passes), but the firmware's saved startup order
still tries the SD card first - the classic post-migration trap where a
re-inserted card silently wins the next boot. Expected: WARN with the exact
`sudo efibootmgr -o ...` line and the startup-menu alternative, nothing
executed.
