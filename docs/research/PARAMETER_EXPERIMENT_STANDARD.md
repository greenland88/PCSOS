# Parameter Experiment Standard

This is the repository-wide standard for strategy parameter research. It applies to every ticker, strategy, and research runner.

## Required method

1. Ask one decision question and vary one independent parameter family only.
2. Freeze every unrelated parameter, data boundary, population, accounting rule, lifecycle rule, and execution assumption.
3. Use the minimum candidate set needed to answer the question. Do not add values for completeness.
4. Pass data, price-basis, contract, and accounting validity gates before interpreting results.
5. Reuse existing valid artifacts and validated common engines. Do not recreate or silently substitute a legacy path.
6. Evaluate PROFIT and RISK separately, then evaluate marginal profit against marginal risk.
7. Do not automatically expand the candidate set, run another parameter family, run timing or Roll research, or add other tickers.
8. Stop when the current decision question is answered. Any next experiment requires a new explicit scope.

## Minimum report

Report the frozen parameters and candidate values, the validity status and first blocker, profit metrics, risk metrics, and the marginal change between adjacent candidates. Raw P&L alone cannot select a parameter. A result with invalid data or unreconciled accounting is not decision evidence.

## Strategy extensions

### COVERED_CALL

Freeze share count, one-call capacity, serial capacity, entry/exit execution sides, lifecycle and Roll rules, price basis, DTE/delta/strike families not under study, and the postulated signal population. Keep call-only P&L separate from stock P&L. Persist explicit entry credit, close/BTC debit, Roll close debit, Roll open credit, expiration settlement, assignment settlement, and realized call P&L.

### PCS

Freeze the underlying population, spread structure, width, debit/credit convention, entry/exit rules, event gates, risk limits, and lifecycle. Keep spread P&L separate from underlying P&L and reject missing-leg or stale-quote accounting.

### CASH_SECURED_PUT

Freeze cash collateral, share-assignment rules, strike selection, DTE, delta, event gates, liquidity, and management rules. Separate option premium, BTC/close debit, assignment cash flow, and stock basis; do not treat assignment as option-only profit.

## Enforcement

Research specifications should declare `parameter_experiment.parameter_family` and `parameter_experiment.candidates`. The unified runner validates that only one independent family is varied and fails closed otherwise. This standard contains no ticker-specific values.

## Existing examples

Current NVDA experiments are historical examples only, not global defaults: the OTM example used 7.5%, 10%, 12.5%, and 15% with 12.5% as the recorded balanced finalist; the DTE example used 14, 21, and 30 with 21/30 as current finalists. They do not authorize reruns or promotion.
