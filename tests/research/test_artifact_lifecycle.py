import json
from dataclasses import replace
from pathlib import Path

import pytest

from pcs.research.research_framework import load_spec
from pcs.research.runner import ResearchRunner


def _runner(tmp_path):
    spec = replace(load_spec("config/research/templates/new_entry.yaml"),
                   research_id="lifecycle_test", ticker="AMD")
    return ResearchRunner(spec, output_dir=tmp_path)


def test_synthetic_fixture_cannot_write_research_outputs(tmp_path):
    runner = _runner(tmp_path)
    with pytest.raises(PermissionError, match="SYNTHETIC_FIXTURE"):
        runner.persist(runner.preflight(), filename="synthetic.json")
    assert not (tmp_path / "lifecycle_test").exists()


def test_invalid_manifest_is_stale(tmp_path):
    runner = _runner(tmp_path)
    root = tmp_path / "lifecycle_test"
    root.mkdir()
    (root / "preflight.json").write_text(json.dumps({"data_source": "PCS_CANONICAL_DATA"}))
    runner.write_artifact_manifest(["preflight.json"], data_version="test", population_semantics="MAXIMAL_CONTIGUOUS_BREAKDOWN_RUNS")
    (root / "preflight.json").write_text("tampered")
    with pytest.raises(RuntimeError, match="STALE_ARTIFACT"):
        runner.read_current_artifact()


def test_current_amd_artifact_manifest_and_run_count():
    root = Path("research_outputs/amd_early_recovery_new_entry")
    manifest = json.loads((root / "artifact_manifest.json").read_text())
    summary = json.loads((root / "state_transition_report/summary.json").read_text())
    assert manifest["current"] is True
    assert manifest["data_source"] == "PCS_CANONICAL_DATA"
    assert manifest["population_semantics"] == "MAXIMAL_CONTIGUOUS_BREAKDOWN_RUNS"
    assert summary["true_breakdown_run_count"] == 74
    assert not any("legacy" in key.lower() for key in summary)


def test_current_amd_directory_has_only_authoritative_files():
    root = Path("research_outputs/amd_early_recovery_new_entry")
    files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert files == {"artifact_manifest.json", "preflight.json", "pit_state_timeline.parquet",
                     "state_transition_report/breakdown_runs.csv",
                     "state_transition_report/summary.json"}


def test_repo_has_no_legacy_reproduction_entrypoint():
    matches = []
    for root in (Path("scripts"), Path("src"), Path("tests"), Path("docs"), Path("config")):
        for path in root.rglob("*"):
            if path.is_file() and path.suffix not in {".pyc"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                forbidden = ["--" + "legacy" + "-" + "reproduction", "LEGACY" + "_" + "RESULT", "DEPRECATED" + "_" + "RESEARCH_ENTRYPOINT"]
                if any(token in text for token in forbidden):
                    matches.append(str(path))
    assert matches == []


def test_duplicate_current_manifest_is_rejected(tmp_path):
    runner = _runner(tmp_path)
    other = tmp_path / "lifecycle_test_old"
    other.mkdir()
    (other / "artifact_manifest.json").write_text(json.dumps({"current": True}))
    root = tmp_path / "lifecycle_test"
    root.mkdir()
    (root / "preflight.json").write_text("canonical")
    with pytest.raises(RuntimeError, match="DUPLICATE_CURRENT_ARTIFACT"):
        runner.write_artifact_manifest(["preflight.json"], data_version="test", population_semantics="MAXIMAL_CONTIGUOUS_BREAKDOWN_RUNS")
