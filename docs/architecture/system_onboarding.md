# System-managed ticker onboarding

The canonical entry point is:

```text
pcs onboard SYMBOL [--period YEAR-QUARTER ...]
```

Without `--period`, the command discovers all supported year/quarter shards in
the configured source root. The option is retained only for isolated fixtures.
The command uses the generic onboarding pipeline and advances these stages
automatically: source discovery, daily readiness, options ingestion and
validation, manifest update, route activation, canonical readiness, contract
smoke, lifecycle smoke, and research readiness. Normal progression does not
require an agent to start the next stage.

State is stored at `data/onboarding/<SYMBOL>/state.json` by default. Every
stage transition is written atomically and records the current stage, status,
attempt count, shard/row counters, source metadata, failure taxonomy, reason
codes, and timestamps. Re-running the command resumes from the persisted
stage; a completed `RESEARCH_READY` state is reused without rebuilding valid
work.

External transient failures use bounded retries. Data-quality, invariant, and
permanent external failures fail closed and remain visible in the state file.
Canonical manifest and route activation remain downstream of validation and
must be performed atomically by their existing PCS canonical services.

The state-machine implementation is exposed as the Python API
`pcs.data.onboarding_engine.OnboardingEngine`, allowing deterministic stage
adapters and isolated system tests without changing strategy rules or frozen
artifacts.
