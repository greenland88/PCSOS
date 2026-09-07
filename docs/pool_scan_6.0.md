# Pool Scan 6.0

Accepted Python source: `ad7fb45e40dad10f18e613c8118823d79196a24b`, tagged
`pool-scan-6.0-acceptance-20260906`. The later documentation commit does not
change that Python source. [Acceptance evidence](history/2026-09-06_pool_scan_6.0_acceptance.md).

Run committed source from the clean `H:/workspace/PCSOS-6.0` worktree, with
`H:/workspace/PCSOS` as the canonical data/config working directory. The branch
extends current c62db50 and the existing working changes, not a historical reset.

```powershell
Set-Location H:/workspace/PCSOS
$env:PYTHONPATH = 'H:/workspace/PCSOS-6.0/src'
& C:/Python313/python.exe -B -m pcs.cli pool-scan --universe-id global_pcs_candidates --mode EOD --as-of 2026-09-04T16:01:00-04:00 --data-mode READ_ONLY --max-workers 8 --stage-timeout-seconds 900 --scan-timeout-seconds 1800 --output-directory H:/workspace/PCSOS/pool_scan_runs/6.0_release_acceptance --new-run
```

To resume, replace `--new-run` with `--resume-run-id <checkpoint run_id>` and
retain the same inputs/output directory. Missing or incompatible identities fail
with `CHECKPOINT_IDENTITY_MISMATCH`. `--no-resume` forces recomputation. New runs
assign a new run id, revalidate every pool member and reuse valid assessments.
Update as-of for a new session; this fixed acceptance session is not live data.

The accepted run can be resumed directly with the following alternative command.
A subsequent new run replaces the recovery anchor with its own run id; use the
latest checkpoint's id after starting a new run.

```powershell
Set-Location H:/workspace/PCSOS
$env:PYTHONPATH = 'H:/workspace/PCSOS-6.0/src'
& C:/Python313/python.exe -B -m pcs.cli pool-scan --universe-id global_pcs_candidates --mode EOD --as-of 2026-09-04T16:01:00-04:00 --data-mode READ_ONLY --max-workers 8 --stage-timeout-seconds 900 --scan-timeout-seconds 1800 --output-directory H:/workspace/PCSOS/pool_scan_runs/6.0_release_acceptance --resume-run-id 384187f5a6e0465e968d01a5983bb017
```

The existing `.checkpoints/<identity>.json` is atomically saved after timing and
completed ticker results. Pending options can resume from saved timing. Daily
and benchmark identity changes invalidate their dependent results; options-only
changes preserve timing. Configuration and Python source changes conservatively
invalidate checkpoints. Injected live adapters disable persistent result reuse.
All reads retain generation, checksum and fingerprint validation.
The writer caches serialized immutable rows within the run, re-encoding replaced
rows only; the persisted JSON schema and full atomic replacement are unchanged.

Stderr `POOL_SCAN_PROGRESS` records operations and a five-second heartbeat.
Operation milliseconds accumulate worker time; runner total is wall time before
final artifact serialization. The invocation evidence records end-to-end time.

Count definitions: `raw_count` is the full pool; `daily_scan_ready_count` excludes
an extra benchmark; eligibility and timing fields are independent funnel states.
`options_check_count` includes blocked or valid cached assessments, not just successful verification.
`spread_count` is discovered spreads; `pcs_trade_ready_count` requires final gates.
`blocked_assessment_count` counts daily/options/evidence blockers, excluding
execution failures, timeouts and unprocessed rows. Those have separate counts.
`checkpoint_hits` counts rows reusing saved work. Timing success can coexist with
an options blocker. See each row's states/reason codes for the exact assessment.
The legacy `options_reader_calls` counter follows this assessment count; use
`options_frame_reads` and operation events to measure actual reads in this invocation.

Python API thread deadlines bound collection, not OS I/O cancellation. The
READ_ONLY CLI supervisor terminates the child before returning a global timeout
and retains saved rows in PARTIAL artifacts. Never use it for promotion work.
Readiness is revalidated on every invocation; checkpoint reuse does not skip
physical checksum checks. This repair does not authorize live trading or promote
the recovery baseline to a stable release.
