import json

import pandas as pd
import pytest

from pcs.data.vix_history import PIT_STATUS, VixInputStatus, VixValidationError, canonicalize_vix_csv, ingest_historical_vix


def _csv(path, rows):
    pd.DataFrame(rows, columns=["DATE", "OPEN", "HIGH", "LOW", "CLOSE"]).to_csv(path, index=False)


def test_canonical_vix_preserves_close_and_delays_availability_to_after_day_end(tmp_path):
    raw = tmp_path / "VIX_History.csv"
    _csv(raw, [["01/02/2026", 20, 21, 19, 20.5], ["01/05/2026", 22, 23, 21, 22.5]])
    frame, report = canonicalize_vix_csv(raw)
    assert frame.vix_close.tolist() == [20.5, 22.5]
    assert frame.pit_status.tolist() == [PIT_STATUS, PIT_STATUS]
    assert frame.available_as_of.iloc[0].startswith("2026-01-03T04:59:59")
    assert report["invalid"]["open_outside_low_high"] == 0


def test_vix_rejects_invalid_close_even_if_ohlc_file_exists(tmp_path):
    raw = tmp_path / "VIX_History.csv"
    _csv(raw, [["01/02/2026", 20, 21, 19, 22]])
    with pytest.raises(VixValidationError, match="VIX_CLOSE_DATA_QUALITY_INVALID"):
        canonicalize_vix_csv(raw)


def test_ingestion_reports_coverage_and_deterministic_payload(tmp_path):
    raw, out = tmp_path / "VIX_History.csv", tmp_path / "canonical"
    _csv(raw, [["01/02/2026", 20, 21, 19, 20], ["01/05/2026", 21, 22, 20, 21]])
    required = {"complete": ["2026-01-02"], "gap": ["2026-01-06"]}
    first = ingest_historical_vix(raw, out, required_date_sets=required, run_id="run-1", request_id="request-1", source_reference_verified=True)
    second = ingest_historical_vix(raw, out, required_date_sets=required, run_id="run-2", request_id="request-2", source_reference_verified=True)
    assert first.status == second.status == VixInputStatus.VIX_INPUT_PARTIAL
    assert first.payload_sha256 == second.payload_sha256
    validation = json.loads((out / "canonical_vix_daily.validation.json").read_text())
    assert validation["required_period_coverage"]["complete"]["coverage_pct"] == 100
    assert validation["required_period_coverage"]["gap"]["missing_dates"] == ["2026-01-06"]
    assert validation["market_state_status"] == "MARKET_STATE_STILL_BLOCKED_BY_BREADTH"
