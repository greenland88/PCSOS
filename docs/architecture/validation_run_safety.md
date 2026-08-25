# Validation run safety

Validation, replay, and onboarding callers should construct
`pcs.validation.ValidationRun` before reading shared code or data. The object
records `HEAD`, branch, worktree, UTC start time, and SHA-256 hashes of every
critical dependency supplied by the caller. Call `finish()` before accepting a
result; it re-reads `HEAD` and dependency hashes. Any change returns exactly
`STALE — RERUN REQUIRED`, regardless of test outcome or requested status.

Use `claim_output()` for each ticker/year/quarter output. It uses an atomic
exclusive lock file and rejects a second job targeting the same partition.
Jobs should use separate output roots (and, where available, isolated Git
worktrees) and record the resulting paths with `ValidationRun.add_output()`.
The metadata JSON contains the run ID, start/end heads, dependency hashes,
outputs, and final status. Missing Git metadata or missing dependencies fail
closed with `BLOCKED` at the caller boundary.
