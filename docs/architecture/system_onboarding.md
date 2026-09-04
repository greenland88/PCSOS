# System-managed ticker data preparation

The canonical preparation path is the control plane described in
[ticker onboarding](ticker_onboarding.md). The normal sequence is:

```powershell
# Inspect first; adjust the history window to satisfy daily warmup requirements.
pcs market-data-status MSFT --start 2025-01-01 --end 2026-09-03 --dataset daily --dataset options

# Explicitly execute authorized imports for the declared window.
pcs import-market-data MSFT --start 2025-01-01 --end 2026-09-03 --dataset daily --dataset options

# Scan without importing or promoting data.
pcs pool-scan --symbol MSFT --mode EOD --as-of 2026-09-03T21:00:00Z
```

For precise warmup, decision-session and contract requirements, use
`MarketDataRequirements` with `get_market_data_status()` and
`ensure_market_data()`. Recheck readiness using the same requirements after
preparation. Consumers do not download raw files or promote partitions themselves.

Successful preparation must retain coverage, source identity, validation
results, promoted partition/generation identities, and reason codes. Missing
credentials, unavailable providers, source-empty windows and pre-listing periods
must remain distinct outcomes. Repeated calls reuse valid state and repair only
the remaining gaps. Provider authorization is checked against the source
registry at execution time.

## Pool Scan execution boundaries

The optional scan-integrated daily preparation path is explicit:

```powershell
pcs pool-scan --symbol MSFT --mode EOD --as-of 2026-09-03T21:00:00Z --data-mode PREPARE_THEN_SCAN --auto-prepare-data
```

This prepares daily dependencies, including the benchmark, then revalidates
them. It is not a complete options, event, or portfolio import workflow.
Use the dedicated import path for separately declared missing datasets.

Read-only CLI scans run `pcs.pool.runner.run_pcs_pool` in a disposable child
process. `--stage-timeout-seconds` bounds stage result collection (default 60
seconds). `--scan-timeout-seconds` bounds child startup and scan execution
(default 300 seconds; cleanup adds at most two seconds). The parent preserves
completed results or returns one ordered `DATA_BLOCKED` result per symbol with
`POOL_SCAN_TIMEOUT` / `POOL_SCAN_PROCESS_FAILED`. Timeouts and process failures
exit with code 2. `POOL_SCAN_STARTED` is flushed to stderr before scan setup.

Python threads cannot cancel arbitrary running I/O. A Python API stage timeout
stops waiting and cancels unstarted work, but does not stop running callables.
Import/promotion work must use provider-level deadlines and transaction-safe
completion or rollback. The hard process deadline deliberately applies only
to `READ_ONLY`; prepared scans are not forcibly terminated during writes.
Transaction-aware cancellation of preparation remains a separate engineering
requirement, not a guarantee of this scan wrapper.

`pcs.data.onboarding_engine.OnboardingEngine` and its historical persisted
states remain legacy orchestration evidence. They do not supersede the control
plane's coverage, manifests, provenance, or current verified handles. A previous
`RESEARCH_READY` state alone is insufficient to admit today's decision session.

## Validation scope

CI runs the Pool Scan, production and hard-risk tests plus the explicitly listed
isolated data/research contracts in `.github/workflows/pcs-ci.yml`. Historical
tests that require ignored `data/` or `research_outputs/` artifacts remain local
acceptance tests; a clean-clone CI pass is not full canonical-data acceptance.

The current Stage B implementation returns `DISCOVERED` and a count of real
candidate spreads, retaining `WAIT` until contract selection and final gates.
This differs from the older `AGENTS.md` requirement that nonzero `spread_count`
requires `PASS`. The original test in
`tests/data/test_pool_options_handle_routing.py` also has incomplete timing/rule
fixtures and expects `PASS`. This change preserves current strategy behavior
and does not resolve that status-contract conflict. New verified-boundary tests
exercise actual generation reads, exact date windows, checksum failures and
nonzero candidate discovery without manufacturing trade approval.
