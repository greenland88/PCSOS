# NVDA covered-call daily decision

The ticker-agnostic entry point is `pcs.research.evaluate_covered_call`.
It consumes a point-in-time stock snapshot and canonical option-chain snapshot,
or obtains both through `PCSDataAccess` when `data_access` is supplied.

```python
from pcs.data.access import PCSDataAccess
from pcs.research import evaluate_covered_call

result = evaluate_covered_call(
    "NVDA", "2026-07-31", data_access=PCSDataAccess.canonical(),
    active_calls=0,
)
```

The result is JSON-serializable and returns exactly one entry decision:
`SELL`, `WAIT`, or `NO_SELL`. A `SELL` result includes the selected canonical
expiration, DTE, strike, delta, moneyness, ATR distance, expected credit,
liquidity status, and transparent `roll_safety`. The same result exposes the
applied `min_safe_strike` and `minimum_atr_distance` boundaries.

Current research-derived defaults are deliberately conservative: minimum
moneyness `+20%`, minimum distance `3 ATR`, DTE `30–60` with a `43 DTE`
preference, delta no greater than `0.30`, and maximum three active calls.
Callers may override the distance and DTE preferences explicitly; overrides
remain caller-owned research choices and are not production promotion.

Existing positions use `evaluate_active_call`, which returns `HOLD`, `ROLL`,
or `CLOSE`. Roll selection remains non-debit only and close eligibility uses
whole-episode P&L.

The current NVDA evidence is stored under
`research_outputs/covered_call_nvda_unified_decision_evidence/`. It is
canonical-data, unified-lifecycle-only, and `final_oos_read=false`. DTE and
delta plateau studies remain conditional until dedicated NVDA unified evidence
is available; the decision layer does not infer those conclusions from other
tickers or old artifacts.
