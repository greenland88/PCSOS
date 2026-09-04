# PCS documentation map

Use this page to choose the correct source of truth. Research output reports and
incident records are evidence, but they do not override canonical contracts or
promote a strategy.

## Required orientation

| Purpose | Canonical document |
|---|---|
| Agent startup and change control | [`../AGENTS.md`](../AGENTS.md) |
| Capability status and promotion boundaries | [`PCS_CAPABILITY_ROADMAP.md`](PCS_CAPABILITY_ROADMAP.md) |
| Current repository state and blockers | [`../PROJECT_STATUS.md`](../PROJECT_STATUS.md) |
| System correctness audit snapshot | [`../SYSTEM_CORRECTNESS_AUDIT.md`](../SYSTEM_CORRECTNESS_AUDIT.md) |

## Architecture and data

| Topic | Document |
|---|---|
| Canonical data architecture | [`data_architecture_audit.md`](data_architecture_audit.md) |
| New ticker onboarding | [`architecture/ticker_onboarding.md`](architecture/ticker_onboarding.md) |
| Ticker research-readiness gate | [`architecture/pcs_ticker_readiness.md`](architecture/pcs_ticker_readiness.md) |
| Agent-ready typed interfaces | [`architecture/agent_ready_interfaces.md`](architecture/agent_ready_interfaces.md) |
| Validation run safety | [`architecture/validation_run_safety.md`](architecture/validation_run_safety.md) |
| Pool Scan execution, routing, and acceptance | [`architecture/pool_scan_contract.md`](architecture/pool_scan_contract.md) |
| Phase 0 integration boundary | [`phase0_data_integration.md`](phase0_data_integration.md) |
| Stage 4A Entry Contract v2 | [`stage4a_entry_contract_canonicalization.md`](stage4a_entry_contract_canonicalization.md) |

## Research

| Topic | Document |
|---|---|
| Research population and PIT contract | [`research/RESEARCH_FRAMEWORK.md`](research/RESEARCH_FRAMEWORK.md) |
| Guarded runner commands and artifacts | [`research/UNIFIED_RESEARCH_RUNNER.md`](research/UNIFIED_RESEARCH_RUNNER.md) |
| MSFT long-history replay performance | [`research/MSFT_REPLAY_PERFORMANCE_RUNBOOK.md`](research/MSFT_REPLAY_PERFORMANCE_RUNBOOK.md) |
| NVDA discovery scope | [`research/NVDA_ENTRY_DISCOVERY_AGENT_V2_SCOPE.md`](research/NVDA_ENTRY_DISCOVERY_AGENT_V2_SCOPE.md) |
| Annualized reporting semantics | [`research/annualized_performance_reporting.md`](research/annualized_performance_reporting.md) |

## Historical and incident records

Ticker cutover notes, research summaries, `ERRORS.md`, and dated audit reports
describe the state at the time they were written. They remain useful evidence,
but must be checked against `PROJECT_STATUS.md`, the canonical roadmap, active
routes, manifests, and current artifact identities before being used.

The dated unified-data acceptance record is
[`history/2026-08-26_unified_data_acceptance.md`](history/2026-08-26_unified_data_acceptance.md).

`docs/PCS/_CAPABILITY_ROADMAP.md` is a compatibility pointer only. It must not
become a second capability registry.

## Authority rules

1. Code plus active config defines implemented behavior.
2. Canonical contracts define allowed behavior and change control.
3. Current manifests and source identities define data/artifact validity.
4. Research reports are evidence only.
5. Production changes require `RESEARCH -> VALIDATION -> CONTRACT OWNER DECISION -> PRODUCTION CHANGE`.
