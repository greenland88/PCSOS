"""Build the reproducible PLTR market-data import acceptance artifact."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.control_plane import get_market_data_status


def main() -> int:
    access = PCSDataAccess.canonical()
    daily = access.read("daily", "PLTR")
    options = access.read("options", "PLTR")
    target = daily[pd.to_datetime(daily["date"]).dt.date == pd.Timestamp("2026-08-14").date()]
    key = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
    requirements = {
        "start": "2018-01-01",
        "end": "2026-08-25",
        "datasets": {"daily": {"required": True}, "options": {"required": True}},
        "consumer": "IMPORT_ACCEPTANCE",
    }
    status = get_market_data_status("PLTR", requirements, access=access)
    checks = {
        "boundary_start": "2018-01-01",
        "pre_listing_option_periods_excluded": not any(
            str(p).startswith(("2018", "2019", "2020Q1", "2020Q2", "2020Q3"))
            for p in status.coverage_plan.get("required_option_periods", [])
        ),
        "daily_row_2026_08_14_count": int(len(target)),
        "daily_row_2026_08_14_unique": len(target) == 1,
        "options_row_count": int(len(options)),
        "options_baseline_row_count": 1_830_934,
        "options_expected_row_count": int(len(options)) >= 1_830_934,
        "options_duplicate_key_count": int(options.duplicated(key).sum()),
        "options_has_call_and_put": set(options["call_put"].astype(str).str.lower()) >= {"c", "p"},
        "control_plane_status": status.status,
        "strategy_research_started": False,
        "final_oos_read": False,
    }
    payload = {
        "module": "pcs.data.import_acceptance",
        "version": "1.0",
        "symbol": "PLTR",
        "status": "ACCEPTED" if all(
            [checks["daily_row_2026_08_14_unique"], checks["options_expected_row_count"],
             checks["options_duplicate_key_count"] == 0, checks["options_has_call_and_put"],
             status.status == "ALREADY_COMPLETE"]
        ) else "BLOCKED",
        "checks": checks,
        "control_plane_result": status.to_dict(),
    }
    output = Path("data/manifests/pltr_import_acceptance.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0 if payload["status"] == "ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
