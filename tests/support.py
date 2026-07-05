"""Shared helpers for the unittest suite.

GUARDRAILS 3.1: all test scratch space lives under ./.work/ inside the
repository. tempfile and system temp directories are banned (guard test).
"""

import shutil
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = REPO_ROOT / ".work" / "tests"


def make_work_dir(testcase):
    """Create a per-test scratch directory under ./.work/tests/, auto-removed."""
    safe_id = testcase.id().replace(":", "_")[-80:]
    path = WORK_ROOT / "{}-{}".format(safe_id, uuid.uuid4().hex[:8])
    path.mkdir(parents=True, exist_ok=False)
    testcase.addCleanup(shutil.rmtree, str(path), ignore_errors=True)
    return path
