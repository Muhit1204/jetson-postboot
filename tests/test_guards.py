"""Guard tests enforcing GUARDRAILS.md structural rules across the whole tree.

- stdlib-only imports everywhere (tool, tests, helpers) - GUARDRAILS 2.
- subprocess importable only by lib/runner.py (the single gateway) - GUARDRAILS 2.
- tempfile banned: scratch space is ./.work/ only - GUARDRAILS 3.1.
- boot_advisor may never pass a mutate keyword to anything - GUARDRAILS 6 Tier 3.
"""

import ast
import unittest

from tests.support import REPO_ROOT

# Approved standard-library roots. Extending this set is a conscious act:
# GUARDRAILS section 2 allows stdlib only, and tempfile stays out because it
# writes outside the repository (GUARDRAILS 3.1).
ALLOWED_STDLIB = frozenset({
    "argparse", "ast", "contextlib", "dataclasses", "datetime", "enum",
    "errno", "functools", "hashlib", "io", "json", "os", "pathlib",
    "platform", "re", "shlex", "shutil", "stat", "string", "subprocess",
    "sys", "textwrap", "time", "traceback", "types", "typing", "unittest",
    "uuid",
})
LOCAL_ROOTS = frozenset({"jetson_postboot", "postboot", "tests"})
SCAN_TREES = ("jetson_postboot", "tests")


def source_files():
    files = [REPO_ROOT / "postboot.py"]
    for tree in SCAN_TREES:
        files.extend(sorted((REPO_ROOT / tree).rglob("*.py")))
    return [f for f in files if f.is_file()]


def import_roots(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.append(node.module.split(".")[0])
    return roots


def mutate_keyword_lines(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "mutate":
                    lines.append("line {}".format(node.lineno))
    return lines


class GuardTests(unittest.TestCase):
    def test_source_tree_present(self):
        files = set(source_files())
        self.assertIn(REPO_ROOT / "postboot.py", files)
        self.assertIn(REPO_ROOT / "jetson_postboot" / "lib" / "runner.py", files)

    def test_stdlib_only_everywhere(self):
        offenders = []
        for path in source_files():
            for root in import_roots(path):
                if root not in ALLOWED_STDLIB and root not in LOCAL_ROOTS:
                    offenders.append(
                        "{}: {}".format(path.relative_to(REPO_ROOT), root)
                    )
        self.assertEqual(
            offenders, [], "non-approved imports found:\n" + "\n".join(offenders)
        )

    def test_subprocess_only_inside_runner(self):
        allowed = REPO_ROOT / "jetson_postboot" / "lib" / "runner.py"
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in source_files()
            if path != allowed and "subprocess" in import_roots(path)
        ]
        self.assertEqual(offenders, [])

    def test_tempfile_banned(self):
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in source_files()
            if "tempfile" in import_roots(path)
        ]
        self.assertEqual(offenders, [])

    def test_boot_advisor_has_no_mutation_keywords(self):
        path = REPO_ROOT / "jetson_postboot" / "modules" / "boot_advisor.py"
        offenders = mutate_keyword_lines(path)
        self.assertEqual(
            offenders, [], "boot_advisor must never pass mutate= to the runner"
        )

    def test_storage_has_no_mutation_keywords(self):
        # v1.1: storage demoted from Tier 2 apply to Tier 3 advisory-only
        # (GUARDRAILS/PLAN changelog, 2026-07-04). Same guard as boot_advisor.
        path = REPO_ROOT / "jetson_postboot" / "modules" / "storage.py"
        offenders = mutate_keyword_lines(path)
        self.assertEqual(
            offenders, [], "storage is advisory-only: must never pass mutate= to the runner"
        )


if __name__ == "__main__":
    unittest.main()
