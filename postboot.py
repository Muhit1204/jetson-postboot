#!/usr/bin/env python3
"""jetson-postboot entry point: argument parsing and orchestration.

Command surface (PLAN 4): default Tier 0 report; --apply/--undo per module;
--dry-run prints exact commands without mutating; --simulate answers every
command from a fixture directory. Exit codes: 0 no findings, 1 findings
reported, 2 internal error.
"""

import argparse
import sys
import traceback
from pathlib import Path

from jetson_postboot import __version__
from jetson_postboot.lib.report import Report
from jetson_postboot.lib.runner import Runner, RunnerError

REPO_ROOT = Path(__file__).resolve().parent

# Registry of when each apply/undo target lands; Phase 1+ replaces entries
# with real module wiring. No storage entry: storage is Tier 3 advisory-only
# per GUARDRAILS v1.1 and has no apply mode.
_APPLY_PHASE = {"swap": "Phase 2", "mlstack": "Phase 3"}
_UNDO_PHASE = {"swap": "Phase 2"}

# Tier 0 detection registry: (module name, check callable). Each callable
# takes (runner, report) and appends findings. Populated as Phase 1 parsers
# land; empty registry renders a valid empty report.
_CHECKS = []


def build_parser():
    parser = argparse.ArgumentParser(
        prog="postboot.py",
        description=(
            "Post-first-boot setup and repair assistant for NVIDIA Jetson "
            "devices. Default run is a read-only report."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", choices=sorted(_APPLY_PHASE),
                       help="apply a confirmed profile")
    group.add_argument("--undo", choices=sorted(_UNDO_PHASE),
                       help="restore recorded pre-change state")
    parser.add_argument("--dry-run", action="store_true",
                        help="print exact commands, change nothing")
    parser.add_argument("--simulate", metavar="FIXTURE_DIR",
                        help="answer every command from a fixture set")
    return parser


def main(argv=None, stdout=None, stderr=None, root=None):
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    root = Path(root) if root is not None else REPO_ROOT
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse prints its own usage message
        return int(exc.code) if exc.code else 0
    try:
        return _dispatch(args, stdout, stderr, root)
    except RunnerError as exc:
        stderr.write("error: {}\n".format(exc))
        return 2
    except Exception:
        stderr.write(traceback.format_exc())
        return 2


def _dispatch(args, stdout, stderr, root):
    fixture_dir = None
    if args.simulate:
        fixture_dir = Path(args.simulate)
        if not (fixture_dir / "manifest.json").is_file():
            stderr.write(
                "error: no manifest.json in fixture directory {}\n".format(fixture_dir))
            return 2
    runner = Runner(
        log_dir=root / "logs",
        fixture_dir=fixture_dir,
        dry_run=args.dry_run,
        downloads_dir=root / "downloads",
        echo=lambda line: stdout.write(line + "\n"),
    )
    if args.apply:
        stderr.write("error: --apply {} is not implemented yet (arrives in {}).\n".format(
            args.apply, _APPLY_PHASE[args.apply]))
        return 2
    if args.undo:
        stderr.write("error: --undo {} is not implemented yet (arrives in {}).\n".format(
            args.undo, _UNDO_PHASE[args.undo]))
        return 2
    report = Report(mode=runner.mode, tool_version=__version__)
    for _name, check in _CHECKS:
        check(runner, report)
    stdout.write(report.render_text())
    txt_path, json_path = report.save(root / "reports")
    stdout.write("report saved: {}\n".format(txt_path))
    stdout.write("report saved: {}\n".format(json_path))
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
