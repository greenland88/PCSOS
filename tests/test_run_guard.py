import json
from pathlib import Path

import pytest

from pcs.validation.run_guard import RunSafetyError, RunStatus, ValidationRun, claim_output


def test_dependency_change_invalidates_run(tmp_path, monkeypatch):
    dep = tmp_path / "module.py"; dep.write_text("v=1")
    monkeypatch.setattr("pcs.validation.run_guard._git", lambda *_: "head")
    run = ValidationRun(tmp_path, (dep,))
    dep.write_text("v=2")
    assert run.finish(tmp_path / "run.json") == RunStatus.STALE


def test_head_change_invalidates_run(tmp_path, monkeypatch):
    dep = tmp_path / "module.py"; dep.write_text("v=1")
    heads = iter(["a", "branch", "worktree", "b"])
    monkeypatch.setattr("pcs.validation.run_guard._git", lambda *_: next(heads))
    run = ValidationRun(tmp_path, (dep,))
    assert run.finish() == RunStatus.STALE


def test_duplicate_partition_claim_is_rejected(tmp_path):
    claim_output(tmp_path, "AMD", 2026, 3, "one")
    with pytest.raises(RunSafetyError, match="already claimed"):
        claim_output(tmp_path, "AMD", 2026, 3, "two")


def test_metadata_contains_run_and_dependency_state(tmp_path, monkeypatch):
    dep = tmp_path / "module.py"; dep.write_text("v=1")
    monkeypatch.setattr("pcs.validation.run_guard._git", lambda *_: "head")
    run = ValidationRun(tmp_path, (dep,)); path = tmp_path / "run.json"
    assert run.finish(path) == RunStatus.VALID
    data = json.loads(path.read_text())
    assert data["run_id"] == run.run_id
    assert data["start_head"] == data["end_head"] == "head"
    assert data["dependency_hashes"]


def test_worktree_and_output_are_recorded(tmp_path, monkeypatch):
    dep = tmp_path / "module.py"; dep.write_text("v=1")
    monkeypatch.setattr("pcs.validation.run_guard._git", lambda _root, *args: {
        ("rev-parse", "HEAD"): "head",
        ("branch", "--show-current"): "isolated-task",
        ("rev-parse", "--show-toplevel"): str(tmp_path),
    }[args])
    run = ValidationRun(tmp_path, (dep,)); run.add_output(tmp_path / "out.parquet")
    path = tmp_path / "run.json"; run.finish(path)
    data = json.loads(path.read_text())
    assert data["start_branch"] == "isolated-task"
    assert data["output_paths"] == [str((tmp_path / "out.parquet").resolve())]


def test_git_unavailable_blocks_creation(tmp_path, monkeypatch):
    monkeypatch.setattr("pcs.validation.run_guard._git", lambda *_: (_ for _ in ()).throw(RunSafetyError("no git")))
    with pytest.raises(RunSafetyError, match="no git"):
        ValidationRun(tmp_path, ())
