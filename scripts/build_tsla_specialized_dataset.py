"""Build the PIT-safe TSLA specialized PCS research dataset.

Research infrastructure only.  This module never changes strategy configuration
or production eligibility.  It consumes the frozen TSLA replay population and
routes all market reads through PCSDataAccess.
"""
from __future__ import annotations

import hashlib, json, sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pcs.data.access import PCSDataAccess

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT = REPO_ROOT / "research_outputs/tsla_specialized_pcs_20260820"
FROZEN = REPO_ROOT / "data/parquet/research/variant_b_full/TSLA_full_post2020_2d.parquet"
TARGETS = (2.3, 2.6, 3.0, 3.3, 3.6)


def _id(row: pd.Series) -> str:
    raw = "|".join(str(row.get(x, "")) for x in ("ticker", "date", "expiration", "short_strike", "long_strike"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _volatility_audit() -> dict:
    # Deliberately fail closed: no approved ATR adverse-excursion boundary was
    # found in the repository, so this variant must not be invented here.
    return {"variant": "VOLATILITY_AWARE_STOP", "existing_boundary_found": False,
            "definition": "DEFINITION_BLOCKED", "producer": None,
            "pit_safe": False, "applicable": False,
            "reason_code": "NO_APPROVED_ATR_BOUNDARY"}


def build(output_dir: Path = ROOT) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    frozen = pd.read_parquet(FROZEN).copy()
    if len(frozen) != 1119:
        raise ValueError(f"FROZEN_POPULATION_COUNT_MISMATCH:{len(frozen)}")
    frozen["base_candidate_id"] = frozen.apply(_id, axis=1)
    if frozen.base_candidate_id.duplicated().any():
        raise ValueError("FROZEN_IDENTITY_FAILURE")

    # Entry variants are derived only from persisted exact baseline rows.  A
    # row is unavailable unless the canonical chain query supplies the exact
    # requested strike and an allowed exact-width long leg.
    access = PCSDataAccess()
    daily = access.read_prices("TSLA", start_date=frozen.date.min(), end_date=frozen.expiration.max())
    close_by_date = daily.set_index("date")["close"]
    chain_cache: dict[object, pd.DataFrame] = {}
    rows = []
    coverage = []
    for target in TARGETS:
        for r in frozen.itertuples(index=False):
            event = "EARNINGS_CROSSING" if bool(r.event_crosses_earnings) else "NON_EARNINGS_CROSSING"
            rec = {"base_candidate_id": r.base_candidate_id, "research_variant": f"ATR_{target:.1f}",
                   "decision_date": r.date, "expiration": r.expiration, "target_atr_distance": target,
                   "event_class": event, "status": "VARIANT_UNAVAILABLE", "reason_code": "DATA_UNAVAILABLE"}
            try:
                spot = close_by_date.get(pd.Timestamp(r.date))
                if pd.isna(spot):
                    rows.append(rec); continue
                day = pd.Timestamp(r.date).date()
                if day not in chain_cache:
                    chain_cache[day] = access.read("options", "TSLA", r.date, r.date)
                chain = chain_cache[day]
                chain = chain[chain.expiration_date.eq(pd.Timestamp(r.expiration).date())]
                puts = chain[chain.call_put.eq("p")].copy()
                puts["actual_atr_distance"] = (float(spot) - puts.strike) / float(r.atr)
                short = puts[puts.actual_atr_distance >= target].sort_values("strike", ascending=False).head(1)
                if short.empty: rows.append(rec); continue
                s = short.iloc[0]
                leg = None
                for width in (5.0, 10.0, 2.0):
                    x = puts[puts.strike.eq(float(s.strike - width))]
                    if not x.empty: leg = x.iloc[0]; break
                if leg is None: rows.append(rec); continue
                rec.update({"status":"VALID", "reason_code":"OK", "underlying_price":float(spot), "atr":float(r.atr),
                            "short_strike":float(s.strike), "long_strike":float(leg.strike), "actual_atr_distance":float(s.actual_atr_distance),
                            "width":float(s.strike-leg.strike), "short_bid":s.get("bid"), "short_ask":s.get("ask"),
                            "long_bid":leg.get("bid"), "long_ask":leg.get("ask"), "short_volume":s.get("volume"), "short_oi":s.get("open_interest"),
                            "long_volume":leg.get("volume"), "long_oi":leg.get("open_interest"), "initial_credit":s.get("bid", pd.NA)-leg.get("ask", pd.NA)})
            except Exception as exc:
                rec["reason_code"] = "DATA_UNAVAILABLE" if isinstance(exc, (FileNotFoundError, ValueError)) else "IMPLEMENTATION_FAILURE"
            rows.append(rec)
        coverage.append({"atr_profile":target,"base_opportunities":len(frozen),"exact_spread_available":sum(x["status"]=="VALID" and x["research_variant"]==f"ATR_{target:.1f}" for x in rows)})
    variants = pd.DataFrame(rows)
    variants.to_parquet(output_dir/"tsla_specialized_candidate_variants.parquet", index=False)
    valid = variants[variants.status.eq("VALID")].copy()
    valid["credit_width_ratio"] = valid.initial_credit / valid.width
    valid["credit_per_ATR"] = valid.initial_credit / valid.atr
    valid["credit_per_expected_move"] = pd.NA
    valid["credit_per_strike_buffer_ATR"] = pd.NA
    valid.to_parquet(output_dir/"tsla_specialized_entry_features.parquet", index=False)
    valid[["base_candidate_id","research_variant","initial_credit","atr","width","credit_width_ratio",
           "credit_per_ATR","credit_per_expected_move","credit_per_strike_buffer_ATR"]].to_parquet(
        output_dir/"tsla_specialized_compensation_features.parquet", index=False)

    # Underlying lifecycle is canonical purchased QFQ history.  Support is
    # intentionally fail-closed because the frozen artifact does not contain
    # the PIT numeric support level.
    paths, support = [], []
    for r in valid.itertuples(index=False):
        bars = daily[(daily.date >= pd.Timestamp(r.decision_date)) & (daily.date <= pd.Timestamp(r.expiration))]
        for b in bars.itertuples(index=False):
            paths.append({"base_candidate_id":r.base_candidate_id,"research_variant":r.research_variant,"date":b.date,
                          "open":b.open,"high":b.high,"low":b.low,"close":b.close,"volume":b.volume,
                          "source":"PCSDataAccess","provenance":"canonical_daily_source","pit_status":"PIT"})
            support.append({"base_candidate_id":r.base_candidate_id,"research_variant":r.research_variant,"date":b.date,
                            "entry_support":pd.NA,"daily_close":b.close,"support_intact":pd.NA,
                            "confirmed_close_below_entry_support":pd.NA,"first_support_break_date":pd.NaT,
                            "reason_code":"DATA_UNAVAILABLE:ENTRY_SUPPORT_LEVEL"})
    pd.DataFrame(paths).to_parquet(output_dir/"tsla_specialized_daily_underlying.parquet", index=False)
    pd.DataFrame(support).to_parquet(output_dir/"tsla_specialized_support_path.parquet", index=False)

    # Exact quote lifecycle.  Each date is read once and filtered by the full
    # identity; duplicate/conflicting keys are surfaced by PCSDataAccess.
    quote_rows, mark_rows, quote_cache = [], [], {}
    for r in valid.itertuples(index=False):
        bars = daily[(daily.date >= pd.Timestamp(r.decision_date)) & (daily.date <= pd.Timestamp(r.expiration))]
        for dt in bars.date:
            day = pd.Timestamp(dt).date()
            if day not in quote_cache: quote_cache[day] = access.read("options", "TSLA", day, day)
            q = quote_cache[day]
            q = q[(q.expiration_date.eq(pd.Timestamp(r.expiration).date())) & q.call_put.eq("p") & q.strike.isin([r.short_strike, r.long_strike])]
            s = q[q.strike.eq(r.short_strike)].head(1); l = q[q.strike.eq(r.long_strike)].head(1)
            complete = len(s)==1 and len(l)==1
            rec = {"base_candidate_id":r.base_candidate_id,"research_variant":r.research_variant,"date":dt,
                   "short_bid":s.iloc[0].get("bid") if len(s) else pd.NA,"short_ask":s.iloc[0].get("ask") if len(s) else pd.NA,
                   "long_bid":l.iloc[0].get("bid") if len(l) else pd.NA,"long_ask":l.iloc[0].get("ask") if len(l) else pd.NA,
                   "availability":"AVAILABLE" if complete else "MARK_UNAVAILABLE","source":"PCSDataAccess",
                   "provenance":"canonical_exact_identity"}
            quote_rows.append(rec)
            mark = ((rec["short_bid"] + rec["short_ask"])/2 - (rec["long_bid"] + rec["long_ask"])/2) if complete else pd.NA
            mark_rows.append({"base_candidate_id":r.base_candidate_id,"research_variant":r.research_variant,"date":dt,
                              "spread_mark":mark,"mark_method":"MIDPOINT_SHORT_MINUS_LONG","mark_valid":complete})
    pd.DataFrame(quote_rows).to_parquet(output_dir/"tsla_specialized_daily_quotes.parquet", index=False)
    pd.DataFrame(mark_rows).to_parquet(output_dir/"tsla_specialized_spread_marks.parquet", index=False)

    base = valid[valid.research_variant.eq("ATR_2.3")].merge(frozen, on="base_candidate_id", suffixes=("_recon","_auth"))
    parity = {"applicable":int(len(base)),"mismatch_counts":{},"status":"BLOCKED_DATA_UNAVAILABLE",
              "baseline_authoritative_rows":len(frozen),"reconstructed_rows":len(base),
              "reason_codes":["ENTRY_SUPPORT_LEVEL_MISSING","LIFECYCLE_QUOTES_NOT_COMPLETE"]}
    for a,b in (("short_strike_recon","short_strike_auth"),("long_strike_recon","long_strike_auth")):
        parity["mismatch_counts"][a] = int((base[a].astype(float).round(8) != base[b].astype(float).round(8)).sum()) if a in base and b in base else None
    (output_dir/"tsla_baseline_23_parity.json").write_text(json.dumps(parity, indent=2, default=str), encoding="utf-8")
    audit = _volatility_audit()
    (output_dir/"tsla_volatility_stop_definition_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    validation = {"frozen_population":1119,"candidate_identity_preserved":True,"baseline_parity":parity["status"],
                  "research_only":True,"coverage":coverage,"structure_stop_ready":False,"structure_stop_blocker":"ENTRY_SUPPORT_LEVEL_MISSING","volatility_stop_ready":False}
    (output_dir/"tsla_specialized_data_coverage.json").write_text(json.dumps(validation, indent=2, default=str), encoding="utf-8")
    (output_dir/"tsla_specialized_validation.json").write_text(json.dumps(validation, indent=2, default=str), encoding="utf-8")
    return validation


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
