# Pool Scan contract

The canonical implementation is `pcs.pool.runner.run_pcs_pool`, exposed by the
`pool-scan` CLI. Extend that path; do not create an alternate scanner.

## Execution and data boundaries

- Every scan is bounded and observable. After argument validation the CLI emits
  `POOL_SCAN_STARTED` immediately. Every stage has a finite timeout and returns
  one ordered outcome per requested symbol. A hang, timeout, process failure, or
  empty ticker result is not success.
- Read-only scans do not import, promote, recover, or write canonical data.
  Preparation is opt-in through `data_mode="PREPARE_THEN_SCAN"` plus
  `auto_prepare_data=True` and still uses the control plane.
- Daily and options data resolve only through `PCSDataAccess` and
  generation-pinned verified handles. Explicitly supplied access objects do not
  disable handle resolvers. Options resolution starts with logical
  `_resolve_route("options", symbol)`; consumers never guess `options`,
  `options_v2`, or `options_v3`.
- Reuse a manifest snapshot only when its path equals the routed manifest path.
  A daily/default snapshot cannot resolve an options generation on another route.
  Missing route, generation, provenance, checksum, schema, or coverage fails
  closed with the specific reason code. Never fall back to raw or unrelated data.
- Required options coverage is `min_date <= decision_date <= max_date`.

When comparing two runs, require matching effective session, universe snapshot,
mode, manifest snapshot, code revision, engine version, profile versions, and
refresh policy. Any missing or changed identity makes the comparison
non-comparable; do not attribute deltas to recovery. Recovery evidence may be
attached per symbol, including nested `admission_result` values, but READY
counts must still come from the independent scan result.

## Runtime and result invariants

Handle resolution is single-flight and cached by the complete decision identity,
including symbol and trading session. Initialize worker-local result collections
before guarded execution so error paths preserve the original exception.
`spread_count` is nonzero only when real spread candidates were produced under
the current approved options status contract; fabricated inputs and relaxed
validation are forbidden.
If options fail after timing succeeds, retain eligibility/timing, return
`options_status=DATA_BLOCKED`, zero spreads, and the original options reason code.
Eligibility, timing, options, event, portfolio, and final trade readiness remain
separate; missing required adapters cannot be reported as `PCS_TRADE_READY`.

## Focused acceptance checklist

Material Pool Scan, routing, or runtime changes require focused regression
evidence for these nine risks (select applicable items for a local change;
shared routing, scheduling, or handle changes cover cross-module items):

1. bounded execution and immediate startup observability;
2. one ordered result per requested symbol;
3. daily/options handle single-flight behavior;
4. logical-to-physical options routing;
5. rejection of a manifest snapshot from another route;
6. exact options date-window validation;
7. preservation of the original reason code on options failure;
8. a timing-ready verified-options fixture producing at least one real spread;
9. a negative fixture returning `DATA_BLOCKED`, zero spreads, and no masked exception.

A valid-JSON CLI smoke test with `spread_count=0` proves CLI completion only; it
does not prove the verified options path or trade readiness.
