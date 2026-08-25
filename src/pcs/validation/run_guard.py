"""Fail-closed guards for validation and replay runs.

The guard snapshots Git state and dependency hashes at construction and checks
them again at completion.  It also provides an atomic claim for partition
outputs so parallel jobs cannot target the same ticker/period.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable


class RunStatus(str, Enum):
    VALID = "VALID"
    STALE = "STALE — RERUN REQUIRED"
    BLOCKED = "BLOCKED"


class RunSafetyError(RuntimeError):
    """Raised when a run cannot safely claim or validate its result."""


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunSafetyError("Git metadata unavailable; validation is blocked") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ValidationRun:
    root: Path
    dependencies: tuple[Path, ...]
    output_paths: list[str] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    start_head: str = ""
    end_head: str = ""
    start_branch: str = ""
    start_worktree: str = ""
    started_at: str = ""
    dependency_hashes: dict[str, str] = field(default_factory=dict)
    status: RunStatus | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.dependencies = tuple(Path(p).resolve() for p in self.dependencies)
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.start_head = _git(self.root, "rev-parse", "HEAD")
        self.start_branch = _git(self.root, "branch", "--show-current")
        self.start_worktree = _git(self.root, "rev-parse", "--show-toplevel")
        self.dependency_hashes = self._hash_dependencies()

    def _hash_dependencies(self) -> dict[str, str]:
        result = {}
        for path in self.dependencies:
            if not path.is_file():
                raise RunSafetyError(f"dependency missing: {path}")
            result[str(path)] = _sha256(path)
        return result

    def add_output(self, path: str | Path) -> None:
        """Record an output path in the durable run metadata."""
        self.output_paths.append(str(Path(path).resolve()))

    def finish(self, metadata_path: str | Path | None = None, *, status: RunStatus = RunStatus.VALID) -> RunStatus:
        self.end_head = _git(self.root, "rev-parse", "HEAD")
        end_hashes = self._hash_dependencies()
        if self.end_head != self.start_head or end_hashes != self.dependency_hashes:
            self.status = RunStatus.STALE
        else:
            self.status = status
        if metadata_path is not None:
            self.write_metadata(metadata_path, end_hashes)
        return self.status

    def write_metadata(self, path: str | Path, end_hashes: dict[str, str] | None = None) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload.update(status=self.status.value if self.status else None,
                       start_head=self.start_head, end_head=self.end_head,
                       dependency_hashes_end=end_hashes or {})
        payload["root"] = str(self.root)
        payload["dependencies"] = [str(p) for p in self.dependencies]
        tmp = target.with_name(f".{target.name}.{self.run_id}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
        return target


def claim_output(lock_root: str | Path, ticker: str, year: int, quarter: int, run_id: str) -> Path:
    """Atomically claim one ticker/year/quarter output namespace."""
    root = Path(lock_root); root.mkdir(parents=True, exist_ok=True)
    target = root / f"symbol={str(ticker).upper()}" / f"year={year}" / f"quarter={quarter}.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8") as handle:
            json.dump({"run_id": run_id, "claimed_at": datetime.now(timezone.utc).isoformat()}, handle)
    except FileExistsError as exc:
        raise RunSafetyError(f"output already claimed: {target}") from exc
    return target
