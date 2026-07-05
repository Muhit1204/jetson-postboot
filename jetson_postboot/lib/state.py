"""./state/state.json: recorded pre-change values and backup paths, keyed by
module with timestamps (PLAN 6.7). Undo consumes the latest entry. Writes are
atomic (temp file + os.replace) so a crash never half-writes undo data.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class StateError(Exception):
    """state.json exists but cannot be parsed; refuse to guess undo data."""


class StateStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"modules": {}}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StateError("corrupt state file {}: {}".format(self.path, exc))
        data.setdefault("modules", {})
        return data

    def record(self, module, values, backups=None):
        """Append a pre-change snapshot for module; return the new entry."""
        data = self.load()
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "values": dict(values),
            "backups": [str(b) for b in (backups or [])],
        }
        data["modules"].setdefault(module, []).append(entry)
        self._save(data)
        return entry

    def latest(self, module):
        entries = self.load()["modules"].get(module, [])
        return entries[-1] if entries else None

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(str(temp), str(self.path))
