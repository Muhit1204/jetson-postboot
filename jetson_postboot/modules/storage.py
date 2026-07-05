"""Storage module: Tier 0 detection, Tier 3 advisory only, read-only forever.

Demoted from Tier 2 confirmed-apply on 2026-07-04 (GUARDRAILS/PLAN v1.1, at
Munta's request): this module never executes growpart, resize2fs, sgdisk -e,
or apt-get. It detects trailing unallocated space, checks the preconditions a
manual extend would need, and prints the exact command sequence for the user
to run themselves - the same pattern as boot_advisor. A guard test rejects
any call in this file that passes a `mutate` keyword to the runner.

Implemented in Phase 1 (PLAN.md 6.3).
"""
