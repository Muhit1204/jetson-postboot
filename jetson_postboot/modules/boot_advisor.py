"""Boot advisor: Tier 3, advisory only, read-only forever (GUARDRAILS 6).

This module must never request a mutation. A guard test rejects any call in
this file that passes a `mutate` keyword to the runner. Implemented in Phase 1
(PLAN.md 6.5): three verdicts from extlinux.conf vs mounted root, no writes.
"""
