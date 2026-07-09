# Derived fixture variant: untuned-default

Copy of orin-nano-8gb (captured 2026-07-05) with these edits and nothing
else, per PLAN.md section 7 (derived variants), built by
tests/fixtures/build_phase2_variants.py:

- zramctl.txt: CRAFTED six-device table at the JetPack default (6 x
  649068544 B = 3.6 GiB, 48.7% of MemTotal), tiny DATA (fresh boot).
  Synthetic util-linux shape, NOT board ground truth - the captured board
  is already tuned (see PROJECT_CONTEXT O7 for the real-capture option).
- swapon-show.txt: the six zram partitions, no file swap.
- nvzramconfig-enabled.txt/.rc: "enabled", rc 0 (manifest updated).
- manifest.json: Phase 2 mutation/undo commands added as returncode-0/
  no-stdout entries (nothing parsed from them, so no output is invented).

Models the out-of-the-box board: swappiness 60, zram at default, no
swapfile. Exercises the zram-at-default ACTION finding and the full
S1-S3 apply sequence (and its undo).
