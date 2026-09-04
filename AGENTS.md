# PCS Agent Contract

This file is the short entrypoint for work in PCS OS. It defines scope and
points to detailed contracts; it is not a second implementation manual.

## Startup and task routing

For every task, read this file, [`docs/PCS_CAPABILITY_ROADMAP.md`](docs/PCS_CAPABILITY_ROADMAP.md),
and any `AGENTS.md` under directories actually involved. Then read only the
task contract required by the work:

- data import, routing, onboarding, or recovery: [`docs/architecture/ticker_onboarding.md`](docs/architecture/ticker_onboarding.md), [`docs/data_import_runbook.md`](docs/data_import_runbook.md), and the control-plane source registry/remediation policy;
- research or replay: [`docs/research/UNIFIED_RESEARCH_RUNNER.md`](docs/research/UNIFIED_RESEARCH_RUNNER.md) and [`docs/research/RESEARCH_FRAMEWORK.md`](docs/research/RESEARCH_FRAMEWORK.md);
- replay validation and parallel outputs: [`docs/architecture/validation_run_safety.md`](docs/architecture/validation_run_safety.md);
- parameter experiments: also [`docs/research/PARAMETER_EXPERIMENT_STANDARD.md`](docs/research/PARAMETER_EXPERIMENT_STANDARD.md) and `config/research/parameter_experiment_policy.yaml`;
- Pool Scan: [`docs/architecture/pool_scan_contract.md`](docs/architecture/pool_scan_contract.md);
- a new module or materially changed interface: [`docs/architecture/agent_ready_interfaces.md`](docs/architecture/agent_ready_interfaces.md).

Use [`docs/README.md`](docs/README.md) to locate other contracts. Read
task-specific strategy, replay, or validation documents only when the changed
area requires them.

## Scope and completion

Before changing anything, state the current problem, allowed change surface,
required evidence, and stopping condition. Reuse the canonical implementation,
valid data, and compatible artifacts; repair only missing or invalid portions.
Do not turn a local fix into an architecture rewrite, multi-ticker scan, or
parameter study. Research stops after its declared decision question and never
promotes itself. Documentation-only work must not change strategy, program,
configuration, data, or research results.

Complete work only when the requested change is implemented, affected links and
contracts are checked, and validation evidence is recorded. Tests are selected
by the affected contract and concrete risk, with existing focused tests preferred.
New tests must cover a real failure or necessary invariant. Validate the final
code state after merges; “final HEAD” does not mean a full repository suite or
historical replay unless specifically required.

## Non-bypassable boundaries

- Data readiness and import use `PCSDataAccess`, `pcs.data.control_plane`,
  registered adapters, and isolated staging/transactional promotion. Never read
  raw provider files, invent manifests/provenance/checksums/generations, or use a
  catalog/unrelated manifest to bypass verified resolution. Read-only work never
  writes canonical storage. Pool Scan preparation requires its explicit
  `auto_prepare_data` switch. Batch 1 and Batch 2 source authority remain
  distinct.
- Preserve point-in-time data, exact contract identity/strike, price-basis and
  source authority. Missing or invalid inputs fail closed with the most specific
  machine-readable `reason_codes`; unknown is not PASS or FAIL.
- Research uses a validated `ResearchSpec` and exactly one population route:
  `EXISTING_TRADE`, `NEW_ENTRY`, or `CONTRACT_VARIANT`. `NEW_ENTRY` starts from
  the complete PIT daily calendar; it may not use a frozen ledger. Research is
  `RESEARCH -> VALIDATION -> CONTRACT OWNER DECISION -> PRODUCTION CHANGE`.
  Preserve frozen populations unless an explicit documented change is approved.
  Threshold and production parameter changes require explicit owner
  authorization.
- FINAL OOS is forbidden by default. Access requires prior user authorization for
  the specific research scope, recorded in the validated `ResearchSpec`; an
  agent may not fill in authorization itself. Existing authorization cannot be
  broadened.
- A failed run cannot replace `CURRENT` or publish a success. Isolated
  diagnostics, blocked artifacts, and compatible checkpoints may remain; resume
  only after checking spec, code, config, and data identity. Invalid artifacts
  are first marked invalid and removed from consumption with traceability kept.
  Deletion follows project cleanup rules and never happens merely to tidy docs.
- Each `research_id` has one authoritative `CURRENT` artifact set. Readers
  require current manifest, hashes, matching spec/code/data identity, and the
  canonical source; otherwise consumption stops with `STALE_ARTIFACT`.
- Source checks distinguish configuration, bounded read-only provider probe,
  import, recovery, and promotion. Report unexamined, timed out, unauthorized,
  and confirmed-empty outcomes separately. Do not infer data absence from a
  missing provider or event source.
- Deterministic engines own eligibility, timing, options, event, portfolio, and
  final readiness as separate states. AI cannot override market-regime hard
  stops, portfolio/liquidity/position limits, or risk controls; V1 adds no
  automatic live trading. Preserve valid timing output when options are blocked,
  and never obtain a nonzero spread by fabricated input or weakened validation.
  Decision actions are limited to `OPEN`, `WAIT`, `HOLD`, `CLOSE`, and `ROLL`.
  Agents pass compact derived evidence to these engines and do not read raw
  option files or recompute core indicators and risk metrics; parallel runs use
  isolated outputs/worktrees.

## Existing evidence and history

Reuse only artifacts whose manifests, hashes, versions, route, and canonical
source validate as current. Historical evidence is context, not present-day
readiness or authorization. The dated implementation record moved from this file
is [`docs/history/2026-08-26_unified_data_acceptance.md`](docs/history/2026-08-26_unified_data_acceptance.md);
it proves only the environment, account, data, and HEAD recorded there.

## Worktree and reporting

At start and before delivery, inspect `git status` and the actual diff. Preserve
existing user changes, including untracked or ignored research work; do not use a
file allowlist as the ownership test. Do not stage or delete unrelated files.
Run `git diff --check` for documentation changes. Report only completion status,
actual files changed and purpose, exact checks and results, historical evidence
explicitly reused, whether the canonical data path truly ran, and any blocker
with stage, reason code, confirmed facts, reusable checkpoint, and required input.
Distinguish code tests, fixtures, provider probes, canonical readiness, and real
business runs. Do not report a commit or branch change unless one was actually
created.
