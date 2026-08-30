# NVDL covered-call research decision

NVDL has no validated production covered-call profile. It must not inherit
NVDA parameters. The research entry point is
`config/research/nvdl_covered_call_baseline.yaml`, executed only through the
unified Research Runner.

The policy is WAIT-first. Research states are descriptive candidates, not
production rules:

- `RALLY_ACCELERATION` / `BREAKOUT`: `WAIT`; protect upside during expansion.
- `RALLY_IV`: test conservative deltas and at least 2.5 ATR strike distance.
- `RESISTANCE_STALL`: test a separate, slightly more aggressive band.
- `NORMAL_UPTREND`: test conservative coverage only.
- `PULLBACK` / `OVERSOLD`: `WAIT`.

Each entry must use the full PIT NVDL daily calendar, then select the exact
call from the option chain available on that date. Position sizing is bounded
by owned shares and a tested covered fraction. Active calls are managed with
profit capture, breakout close, and only non-debit upward-strike rolls.

The objective is total portfolio return, not call premium alone:

`stock return + call income - buyback/roll cost - assignment/opportunity cost`

The spec is research-only, does not authorize live orders, does not access
FINAL OOS, and cannot change the NVDL profile automatically. Current data
readiness still requires authoritative NVDL event coverage before execution.
