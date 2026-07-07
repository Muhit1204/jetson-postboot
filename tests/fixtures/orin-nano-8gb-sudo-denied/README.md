# Derived fixture variant: sudo-denied

Copy of orin-nano-8gb (captured 2026-07-05) with these edits and nothing
else, per PLAN.md section 7 (derived variants):

- manifest.json: "sudo sfdisk -d /dev/nvme0n1" and "sudo nvpmodel -q" -> returncode 1 with
  stderr sudo-denied.txt; sfdisk-dump.txt and nvpmodel-q.txt removed (no stdout).
- sudo-denied.txt: real stderr captured on the board 2026-07-07 when
  sudo could not prompt for a password (non-interactive session).
- CAPTURE.md removed (applies to the base set only).

Exercises the storage check when the privileged partition-table read
fails: WARN saying the table could not be read (quoting sfdisk's stderr),
never the false claim that the root partition is missing from the table.
