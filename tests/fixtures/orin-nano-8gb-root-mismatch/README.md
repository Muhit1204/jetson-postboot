# Derived fixture variant: root-mismatch

Copy of orin-nano-8gb (captured 2026-07-05) with these edits and nothing
else, per PLAN.md section 7 (derived variants):

- extlinux.conf.txt: APPEND root=UUID= changed to a UUID that resolves to
  nothing (99999999-aaaa-bbbb-cccc-000000000000).
- manifest.json: the blkid -U key follows the new UUID and answers with an
  empty stdout and returncode 2 (blkid's not-found behaviour).

Exercises boot_advisor verdict (c): configured root and mounted root
disagree -> WARN with the exact suggested extlinux line, extlinux.conf
copied into ./backups/, nothing executed.
