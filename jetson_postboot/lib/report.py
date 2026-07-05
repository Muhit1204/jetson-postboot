"""Report rendering: terminal text plus identical .txt and .json copies in
./reports/ (PLAN 6.7). Exit-code contract: PASS lines are informational;
only WARN and ACTION findings drive exit code 1.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LEVEL_PASS = "PASS"
LEVEL_WARN = "WARN"
LEVEL_ACTION = "ACTION"
_LEVELS = (LEVEL_PASS, LEVEL_WARN, LEVEL_ACTION)


@dataclass
class Finding:
    level: str
    module: str
    message: str
    details: Optional[str] = None


class Report:
    def __init__(self, mode, tool_version):
        self.mode = mode
        self.tool_version = tool_version
        self.generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.findings = []

    def add(self, level, module, message, details=None):
        if level not in _LEVELS:
            raise ValueError("unknown finding level: {}".format(level))
        self.findings.append(Finding(level, module, message, details))

    def count(self, level):
        return sum(1 for f in self.findings if f.level == level)

    @property
    def has_findings(self):
        return any(f.level in (LEVEL_WARN, LEVEL_ACTION) for f in self.findings)

    def exit_code(self):
        return 1 if self.has_findings else 0

    def render_text(self):
        lines = [
            "jetson-postboot {}".format(self.tool_version),
            "mode: {}".format(self.mode),
            "generated: {}".format(self.generated),
            "",
        ]
        if not self.findings:
            lines.append("(no findings recorded)")
        else:
            current_module = None
            for finding in self.findings:
                if finding.module != current_module:
                    if current_module is not None:
                        lines.append("")
                    current_module = finding.module
                    lines.append("[{}]".format(current_module))
                lines.append("  {:6s} {}".format(finding.level, finding.message))
                if finding.details:
                    for detail_line in finding.details.splitlines():
                        lines.append("         {}".format(detail_line))
        lines.append("")
        lines.append("summary: {} pass, {} warn, {} action".format(
            self.count(LEVEL_PASS), self.count(LEVEL_WARN), self.count(LEVEL_ACTION)))
        lines.append("")
        return "\n".join(lines)

    def to_dict(self):
        return {
            "tool": "jetson-postboot",
            "version": self.tool_version,
            "mode": self.mode,
            "generated": self.generated,
            "findings": [
                {
                    "level": f.level,
                    "module": f.module,
                    "message": f.message,
                    "details": f.details,
                }
                for f in self.findings
            ],
            "summary": {
                "pass": self.count(LEVEL_PASS),
                "warn": self.count(LEVEL_WARN),
                "action": self.count(LEVEL_ACTION),
            },
        }

    def save(self, reports_dir):
        """Write report-<ts>.txt and .json; return both paths."""
        reports_dir = Path(reports_dir)
        reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        txt_path = reports_dir / "report-{}.txt".format(stamp)
        json_path = reports_dir / "report-{}.json".format(stamp)
        txt_path.write_text(self.render_text(), encoding="utf-8")
        json_path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return txt_path, json_path
