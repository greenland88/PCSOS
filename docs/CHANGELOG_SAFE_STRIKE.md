# Safe Strike Change Record

Date: 2026-08-19

- Previous default: 2.0 ATR
- New default: 2.3 ATR
- Conservative alternative: 2.5 ATR, explicit selection only
- Research basis: `research_outputs/safe_strike_atr_research_final.md`
- Scope: Safe Strike multiplier only

No DTE, liquidity, credit, scoring, entry, exit, Planned Loss, portfolio, or
other PCS rule was changed by this record. `config/pcs_rules.yaml` now carries
2.3 as the research-selected default and 2.5 as the conservative alternative.
External live deployment state is not established by this repository; activation
and any later change remain separately controlled.
