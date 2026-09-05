"""Source-backed event and portfolio adapters; no provider I/O or defaults."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
from pcs.engine.decision_engine import load_rules
from pcs.entry.gates import EventGate, GateStatus, PortfolioRiskGate
from pcs.risk.portfolio_risk import PortfolioRiskAggregator


class PoolContextAdapters:
    """v1: symbols[ticker][events|portfolio] = {source_id, as_of, data}.

    Events additionally requires coverage_end through the selected expiration.
    Market data must supply every MarketState field. Selection delegates to
    DecisionEngine using canonical candidate construction and pinned frames.
    """
    def __init__(self, payload, rules):
        if payload.get("schema_version") not in {1, 2} or not isinstance(payload.get("symbols"), dict):
            raise ValueError("POOL_CONTEXT_INVALID")
        self.mode = payload.get("decision_mode", "HISTORICAL_PIT")
        if self.mode not in {"HISTORICAL_PIT", "CURRENT_EOD"}:
            raise ValueError("POOL_DECISION_MODE_INVALID")
        if self.mode == "CURRENT_EOD" and payload.get("schema_version") != 2:
            raise ValueError("CURRENT_CONTEXT_SCHEMA_REQUIRED")
        self.payload = payload
        self.rules = rules
        self.identity = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()

    def bind_run(self, as_of, mode):
        self.run_as_of = pd.Timestamp(as_of)
        if self.mode == "CURRENT_EOD":
            decision = self._timestamp(self.payload.get("decision_as_of"))
            if mode != "EOD" or decision != self._timestamp(as_of):
                raise ValueError("CURRENT_DECISION_IDENTITY_MISMATCH")

    @staticmethod
    def _timestamp(value):
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError("CONTEXT_TIMESTAMP_TIMEZONE_REQUIRED")
        return stamp.tz_convert("UTC")

    def temporal_identity(self, symbol, day):
        if self.mode != "CURRENT_EOD":
            return {"decision_mode": self.mode, "market_data_as_of": str(day)}
        decision = self._timestamp(self.payload.get("decision_as_of"))
        market_day = self.payload.get("market_data_as_of")
        from .modes import resolve_effective_market_session
        expected = resolve_effective_market_session(decision, "EOD", "XNYS")
        if str(market_day) != str(expected.date()) or str(day)[:10] != str(market_day):
            raise ValueError("MARKET_CONTEXT_STALE")
        return {"decision_mode": self.mode, "decision_as_of": decision.isoformat(),
                "market_data_as_of": market_day}

    def record(self, symbol, name, day):
        record = self.payload["symbols"].get(symbol, {}).get(name)
        if not isinstance(record, dict) or not record.get("source_id") or "data" not in record:
            raise ValueError(name.upper() + "_CONTEXT_MISSING")
        if self.mode == "CURRENT_EOD":
            identity = self.temporal_identity(symbol, day)
            if name == "market":
                if str(record.get("as_of"))[:10] != identity["market_data_as_of"]:
                    raise ValueError("MARKET_CONTEXT_STALE")
            else:
                field = "portfolio_observed_at" if name == "portfolio" else "event_known_at"
                stamp = self._timestamp(record.get(field))
                decision = self._timestamp(identity["decision_as_of"])
                # Current snapshots must have been observed on this UTC decision
                # day and no later than the decision. Historical mode stays strict.
                if stamp > decision or stamp.date() != decision.date():
                    raise ValueError(name.upper() + "_CONTEXT_STALE")
            return record
        stamp = pd.Timestamp(record.get("as_of"))
        if hasattr(self, "run_as_of") and stamp.tzinfo is not None:
            cutoff = self.run_as_of
            if cutoff.tzinfo is None:
                cutoff = cutoff.tz_localize("UTC")
            if stamp > cutoff:
                raise ValueError(name.upper() + "_CONTEXT_FUTURE")
        if pd.isna(stamp) or stamp.date() != pd.Timestamp(day).date():
            raise ValueError(name.upper() + "_CONTEXT_STALE")
        return record

    def prepare_selector(self, *, symbol, day, daily, handle, chain, runtime, access):
        """Build one formal context from pinned inputs, then reuse per spread."""
        from pcs.market_context import build_market_context
        from pcs.models.market import MarketState
        from pcs.engine.decision_engine import DecisionEngine
        from pcs.pcs_status import _candidate, _decision_reason_codes
        market_record = self.record(symbol, "market", day)
        market = market_record["data"]
        if not set(MarketState.model_fields) <= set(market):
            raise ValueError("MARKET_CONTEXT_INCOMPLETE")
        if any(isinstance(v, float) and not math.isfinite(v) for v in market.values()):
            raise ValueError("MARKET_CONTEXT_INVALID")
        state = MarketState.model_validate(market, strict=True)
        portfolio = self.record(symbol, "portfolio", day)["data"]
        required = {"planned_loss", "theoretical_max_loss", "bucket_risk", "ticker_risk", "account_capital"}
        if not required <= set(portfolio):
            raise ValueError("PORTFOLIO_CONTEXT_INCOMPLETE")
        numbers = [portfolio[k] for k in ("planned_loss", "theoretical_max_loss", "account_capital")]
        numbers += list(portfolio["bucket_risk"].values()) + list(portfolio["ticker_risk"].values())
        if any(isinstance(v, bool) or not math.isfinite(float(v)) or float(v) < 0 for v in numbers) or float(portfolio["account_capital"]) <= 0:
            raise ValueError("PORTFOLIO_CONTEXT_INVALID")
        risk = PortfolioRiskAggregator().from_portfolio(portfolio)
        handles, frames = {symbol: handle}, {symbol: daily}
        for ticker in ("QQQ", "SPY", "SOXX"):
            if ticker not in handles:
                handles[ticker] = runtime.resolve_daily_handle(ticker, day, 200)
                frames[ticker] = runtime.read_daily(handles[ticker], end_date=day, required_warmup_rows=200)
        context = build_market_context(symbol, day, data_access=access, rules=self.rules,
            daily_frame=daily, benchmark_frame=frames["QQQ"], spy_frame=frames["SPY"],
            soxx_frame=frames["SOXX"], verified_handles=handles, mode="FORMAL")
        engine = DecisionEngine(self.rules)
        canonical = chain.rename(columns={"expiration": "expiration_date", "option_type": "call_put"})

        def select(spread, **unused):
            contract = spread.to_dict()
            decision_day = (str(self._timestamp(self.payload["decision_as_of"]).date())
                            if self.mode == "CURRENT_EOD" else day)
            contract["entry_date"] = decision_day
            event_status = self.event_status(symbol, SimpleNamespace(selected_contract=contract))
            result = {"status": "DATA_BLOCKED", "contract": contract, "reason_codes": [],
                      "data_identity": {**self.temporal_identity(symbol, day),
                                        "portfolio_observed_at": self.payload["symbols"].get(symbol, {}).get("portfolio", {}).get("portfolio_observed_at"),
                                        "event_known_at": self.payload["symbols"].get(symbol, {}).get("events", {}).get("event_known_at"),
                                        "context_sha256": self.identity, "quote_as_of": spread.quote_as_of,
                                        "options_generation_id": spread.options_generation_id,
                                        "daily_handles": {key: str(getattr(value, "generation_id", getattr(value, "dataset_fingerprint", ""))) for key, value in handles.items()}}}
            if event_status != "EVENT_PASS":
                result["reason_codes"] = [event_status]
                return result
            calendar = pd.DataFrame(self.record(symbol, "events", day)["data"])
            rows = canonical[(pd.to_datetime(canonical.expiration_date).dt.date == pd.Timestamp(spread.expiration).date()) & canonical.call_put.str.lower().isin(["p", "put"])]
            short = rows[rows.strike == spread.short_strike].iloc[0]
            long = rows[rows.strike == spread.long_strike].iloc[0]
            row = {"date": decision_day, "ticker": symbol, "expiration": spread.expiration,
                   "short_strike": spread.short_strike, "long_strike": spread.long_strike,
                   "short_delta": spread.short_delta_diagnostic}
            for prefix, leg in (("short", short), ("long", long)):
                for field, source in (("bid", "bid"), ("ask", "ask"), ("volume", "volume"), ("oi", "open_interest")):
                    row[prefix + "_" + field] = leg[source]
            if any(value is None or not math.isfinite(float(value)) for key, value in row.items()
                   if key not in {"date", "ticker", "expiration"}):
                result["reason_codes"] = ["CONTRACT_INPUT_NONFINITE_OR_MISSING"]
                return result
            candidate = _candidate(row, context, canonical)
            bucket = next((name for name, tickers in self.rules["portfolio"]["buckets"].items() if symbol in tickers), "other")
            candidate.correlation_bucket = bucket
            decision = engine.evaluate_candidate(candidate, state, risk, event_calendar=calendar)
            result.update(status="PASS" if decision.action == "OPEN" and decision.recommended_contracts > 0 else "REJECT",
                decision=decision.model_dump(mode="json"), correlation_bucket=bucket,
                reason_codes=_decision_reason_codes(decision))
            return result
        return select

    def event_status(self, symbol, row):
        try:
            contract = row.selected_contract
            if not contract:
                return "NOT_EVALUATED"
            day = self.payload["market_data_as_of"] if self.mode == "CURRENT_EOD" else contract["entry_date"]
            record = self.record(symbol, "events", day)
            end = pd.Timestamp(record.get("coverage_end"))
            if pd.isna(end) or end.date() < pd.Timestamp(contract["expiration"]).date():
                return "EVENT_COVERAGE_INCOMPLETE"
            if not isinstance(record["data"], list):
                return "EVENT_CONTEXT_INVALID"
            calendar = pd.DataFrame(record["data"])
            if not calendar.empty:
                required = {"symbol", "event_type", "event_date", "event_date_known_at_entry"}
                if not required <= set(calendar) or not calendar.symbol.eq(symbol).all():
                    return "EVENT_CONTEXT_INVALID"
                if not calendar.event_date_known_at_entry.astype(str).str.upper().isin({"YES", "TRUE", "1"}).all():
                    return "EVENT_PIT_METADATA_UNVERIFIED"
                if pd.to_datetime(calendar.event_date, errors="raise").isna().any():
                    return "EVENT_CONTEXT_INVALID"
            candidate = SimpleNamespace(ticker=symbol, entry_date=(str(self._timestamp(self.payload["decision_as_of"]).date()) if self.mode == "CURRENT_EOD" else day),
                expiration=contract["expiration"], event_risk=0)
            result = EventGate().evaluate(candidate, calendar)
            return "EVENT_PASS" if result.status == GateStatus.PASS else (next(iter(result.reason_codes), "EVENT_BLOCKED"))
        except (ValueError, TypeError, KeyError, AttributeError):
            return "EVENT_DATA_STALE"

    def portfolio_status(self, symbol, row):
        try:
            if not row.selected_contract:
                return "NOT_EVALUATED"
            record = self.record(symbol, "portfolio", self.payload["market_data_as_of"] if self.mode == "CURRENT_EOD" else row.selected_contract["entry_date"])
            data = record["data"]
            required = {"planned_loss", "theoretical_max_loss", "bucket_risk", "ticker_risk", "account_capital"}
            if not required <= set(data):
                return "PORTFOLIO_CONTEXT_INCOMPLETE"
            numbers = [data[k] for k in ("planned_loss", "theoretical_max_loss", "account_capital")]
            numbers += list(data["bucket_risk"].values()) + list(data["ticker_risk"].values())
            if any(isinstance(v, bool) or not math.isfinite(float(v)) or float(v) < 0 for v in numbers) or float(data["account_capital"]) <= 0:
                return "PORTFOLIO_CONTEXT_INVALID"
            # Require the actual selected decision's incremental exposure.
            decision = (row.selection_result or {}).get("decision", {})
            if decision.get("action") != "OPEN" or not decision.get("recommended_contracts", 0) > 0:
                return "PORTFOLIO_SELECTED_SIZE_MISSING"
            addition = float(decision["planned_loss"])
            if not math.isfinite(addition) or addition <= 0:
                return "PORTFOLIO_SELECTED_SIZE_INVALID"
            snapshot = PortfolioRiskAggregator().from_portfolio(data)
            if snapshot.planned_loss + addition > self.rules["portfolio"]["max_planned_risk"]:
                return "PORTFOLIO_PLANNED_LOSS_LIMIT"
            bucket = (row.selection_result or {}).get("correlation_bucket")
            if not bucket:
                return "PORTFOLIO_BUCKET_MISSING"
            if snapshot.bucket_planned_loss.get(bucket, 0) + addition > self.rules["portfolio"]["max_bucket_risk"]:
                return "PORTFOLIO_BUCKET_LIMIT"
            if snapshot.ticker_planned_loss.get(symbol, 0) + addition > self.rules["capital"]["single_ticker"]["conviction_ceiling"]:
                return "PORTFOLIO_TICKER_LIMIT"
            gate = PortfolioRiskGate(self.rules).evaluate(snapshot)
            return "PORTFOLIO_PASS" if gate.status == GateStatus.PASS else "PORTFOLIO_PLANNED_LOSS_LIMIT"
        except (ValueError, TypeError, KeyError, AttributeError):
            return "PORTFOLIO_DATA_STALE"


def load_pool_context_adapters(path=None, *, rules_path="config/pcs_rules.yaml"):
    if path is None:
        return {}
    adapter = PoolContextAdapters(json.loads(Path(path).read_text(encoding="utf-8")), load_rules(rules_path))
    return {"contract_selector": adapter, "event_status_reader": adapter.event_status, "portfolio_status_reader": adapter.portfolio_status}
