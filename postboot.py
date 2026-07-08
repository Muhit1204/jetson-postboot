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
from jetson_postboot.checks import system_info
from jetson_postboot.lib.confirm import ask
from jetson_postboot.lib.report import Report
from jetson_postboot.lib.runner import Runner, RunnerError
from jetson_postboot.lib.state import StateStore
from jetson_postboot.modules import boot_advisor, mlstack, storage, swap

REPO_ROOT = Path(__file__).resolve().parent

# Apply/undo dispatch: implemented targets map to module callables taking
# (runner, report, ctx). No storage entry: storage is Tier 3 advisory-only
# per GUARDRAILS v1.1; no mlstack undo in v1 (PLAN 6.6).
_APPLY = {"swap": swap.apply, "mlstack": mlstack.apply}
_UNDO = {"swap": swap.undo}

# Tier 0 detection registry: (module name, check callable). Each callable
# takes (runner, report, ctx) where ctx carries run-scoped paths the modules
# may need (currently backups_dir, used by boot_advisor's verdict c).
_CHECKS = [
    ("system", lambda r, rep, ctx: system_info.check(r, rep)),
    ("storage", lambda r, rep, ctx: storage.check(r, rep)),
    ("swap", lambda r, rep, ctx: swap.check(r, rep)),
    ("boot", lambda r, rep, ctx: boot_advisor.check(
        r, rep, backups_dir=ctx["backups_dir"])),
    ("mlstack", lambda r, rep, ctx: mlstack.check(r, rep)),
]


def build_parser():
    parser = argparse.ArgumentParser(
        prog="postboot.py",
        description=(
            "Post-first-boot setup and repair assistant for NVIDIA Jetson "
            "devices. Default run is a read-only report."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", choices=sorted(_APPLY),
                       help="apply a confirmed profile")
    group.add_argument("--undo", choices=sorted(_UNDO),
                       help="restore recorded pre-change state")
    parser.add_argument("--dry-run", action="store_true",
                        help="print exact commands, change nothing")
    parser.add_argument("--simulate", metavar="FIXTURE_DIR",
                        help="answer every command from a fixture set")
    parser.add_argument("--swapfile-size", type=int, metavar="GIB",
                        default=swap.DEFAULT_SWAPFILE_GIB,
                        help="swapfile size in GiB for --apply swap "
                             "(default {})".format(swap.DEFAULT_SWAPFILE_GIB))
    parser.add_argument("--model", metavar="TAG",
                        default=mlstack.DEFAULT_MODEL,
                        help="model tag for --apply mlstack, e.g. qwen2.5:3b "
                             "(default {}; the pull is fit-checked against "
                             "this board's memory)".format(
                                 mlstack.DEFAULT_MODEL))
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
    echo = lambda line: stdout.write(line + "\n")  # noqa: E731
    runner = Runner(
        log_dir=root / "logs",
        fixture_dir=fixture_dir,
        dry_run=args.dry_run,
        downloads_dir=root / "downloads",
        work_dir=root / ".work",
        echo=echo,
    )
    target = _APPLY.get(args.apply) if args.apply else _UNDO.get(args.undo)
    report = Report(mode=runner.mode, tool_version=__version__)
    if target is not None:
        ctx = {
            "state": StateStore(root / "state" / "state.json"),
            "backups_dir": root / "backups",
            "work_dir": root / ".work",
            "downloads_dir": root / "downloads",
            "echo": echo,
            "confirm": lambda prompt: ask(prompt, output=echo),
            "dry_run": args.dry_run,
            "swapfile_size_gib": args.swapfile_size,
            "model": args.model,
            # Simulate answers the one allowed network fetch from the
            # fixture "files" map, keyed by URL (D28); real mode downloads.
            "fetch": ((lambda url: runner.read_file(url).encode("utf-8"))
                      if fixture_dir is not None else None),
        }
        target(runner, report, ctx)
    else:
        ctx = {"backups_dir": root / "backups"}
        for _name, check in _CHECKS:
            check(runner, report, ctx)
    stdout.write(report.render_text())
    txt_path, json_path = report.save(root / "reports")
    stdout.write("report saved: {}\n".format(txt_path))
    stdout.write("report saved: {}\n".format(json_path))
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
