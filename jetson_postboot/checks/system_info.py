"""Tier 0 system identity checks: board model, L4T/JetPack, memory, power mode.

Phase 1 status: file parsers (device-tree model, nv_tegra_release, meminfo,
nvpmodel) are blocked until the orin-nano-8gb fixture set is captured
(GUARDRAILS 5.3 forbids inventing Jetson output). The mapping below is the
static table from PLAN.md 6.2 and is fixture-independent.
"""

# PLAN.md 6.2: L4T major release to JetPack generation.
_L4T_TO_JETPACK = {
    "36": "JetPack 6.x",
    "35": "JetPack 5.x",
}


def jetpack_for_l4t(l4t_major):
    """Map an L4T major release (e.g. '36') to its JetPack generation.

    Returns None for releases outside the supported table; callers degrade
    to detection-only per PLAN.md section 3.
    """
    return _L4T_TO_JETPACK.get(str(l4t_major))
