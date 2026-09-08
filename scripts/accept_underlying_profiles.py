"""Verify saved profile views and an independent NVDA canonical API call.

This is a bounded acceptance consumer, not a scan or data import entry point.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from pcs.analysis_contracts import CallContext
from pcs.pool.artifacts import _write_atomic
from pcs.pool.underlying_profiles import ProfileDataReader, underlying_profiles_to_markdown, underlying_profile_to_ai_view
from pcs.trend.selection_models import UnderlyingProfile
from pcs.trend.underlying_profile import measure_underlying_profile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory")
    args = parser.parse_args()
    root = Path(args.output_directory)
    manifest = json.loads((root/"artifact_manifest.json").read_text(encoding="utf-8"))
    hashes = all(sha256((root/name).read_bytes()).hexdigest() == digest for name, digest in manifest["sha256"].items())
    assert hashes
    results = [UnderlyingProfile.model_validate(r) for r in json.loads((root/"underlying_profiles.json").read_text(encoding="utf-8"))]
    expected = {"NVDA", "PLTR", "MSFT", "HOOD", "UBER", "MDLZ", "AAL", "AAOI"}
    saved_audit = json.loads((root/"read_audit.json").read_text(encoding="utf-8"))
    failures = saved_audit["failures"]
    actual = [r.symbol for r in results]
    failed = [r["symbol"] for r in failures]
    assert len(actual + failed) == 8 and set(actual + failed) == expected
    assert set(actual).isdisjoint(failed)
    assert (root/"underlying_profiles.zh-CN.md").read_text(encoding="utf-8") == underlying_profiles_to_markdown(results)
    assert json.loads((root/"underlying_profiles.ai.json").read_text(encoding="utf-8")) == [underlying_profile_to_ai_view(r) for r in results]
    batch_nvda = next(r for r in results if r.symbol == "NVDA")
    reader = ProfileDataReader()
    ctx = CallContext(symbol="NVDA", requested_as_of="2026-09-04", effective_daily_session="2026-09-04",
        mode="HISTORICAL", run_id="independent-acceptance", request_id="independent-acceptance", scope="UNDERLYING_PROFILE")
    independent = measure_underlying_profile(reader.load(ctx))
    assert independent.result_id == batch_nvda.result_id
    assert independent.measurements == batch_nvda.measurements
    assert independent.episodes == batch_nvda.episodes
    verification = reader.verify_unchanged()
    source_hashes = saved_audit["source_unchanged"]["file_sha256"]
    assert all(sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in source_hashes.items())
    report = {"status": "PARTIAL" if failures else "PASS", "source_commit": manifest["source_commit"], "source_clean": not manifest["tracked_source_dirty"],
        "profiles_generated": len(results), "source_failures": failures,
        "symbols_unique_and_complete": True, "model_roundtrip": True, "output_hashes": hashes,
        "deterministic_views_equal": True, "independent_nvda_result_id": independent.result_id,
        "independent_nvda_measurements_and_episodes_equal": True, "source_hashes_unchanged": True,
        "source_file_count": len(source_hashes), "independent_read_verification": verification}
    assert report["source_clean"]
    for name, payload in (("independent_nvda.json", independent.model_dump(mode="json")), ("acceptance.json", report)):
        if (root/name).exists():
            raise ValueError("ACCEPTANCE_FILE_ALREADY_EXISTS")
        _write_atomic(root/name, json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "independent_read_verification"}, indent=2))


if __name__ == "__main__":
    main()
