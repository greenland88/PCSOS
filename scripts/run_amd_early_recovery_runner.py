"""Canonical AMD early-recovery entry point.

This phase is intentionally a real-data descriptive preflight.  The signal
predicate is not frozen, so it does not select contracts or run lifecycle.
The historical baseline script is not used as this population.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pcs.research.runner import ResearchRunner


def run() -> dict:
    return ResearchRunner.from_path(
        ROOT / "config" / "research" / "templates" / "new_entry.yaml"
    ).real_preflight()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
