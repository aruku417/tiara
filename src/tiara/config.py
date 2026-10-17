"""Per-repository settings, read from `.tiara.json` at the repository root (all keys optional).

{
  "base": "origin/main",                      // branch your PRs target
  "regenerate": ["dist/*", "*.generated.ts"], // never merged: keep the base side and tell you to regenerate
  "id_rules": {"$.jobs.*.steps": ["id", "name"]}, // how to match list elements in JSON/YAML
  "insert_both": true                         // keep both sides when both only inserted at the same spot
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

FILE = ".tiara.json"


@dataclass
class Config:
    base: str = "origin/main"
    regenerate: list[str] = field(default_factory=list)
    id_rules: dict[str, list[str]] = field(default_factory=dict)
    insert_both: bool = True

    @staticmethod
    def load(root: str | Path) -> "Config":
        path = Path(root) / FILE
        if not path.exists():
            return Config()
        doc = json.loads(path.read_text(encoding="utf-8"))
        known = set(Config.__dataclass_fields__)
        unknown = set(doc) - known
        if unknown:
            raise ValueError(f"{FILE}: unknown keys {sorted(unknown)}")
        return Config(**doc)
