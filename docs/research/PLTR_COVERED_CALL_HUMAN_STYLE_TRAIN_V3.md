# PLTR Human-Style Opportunistic Covered Call — TRAIN v3

Status: `HUMAN_STYLE_TRAIN_CANDIDATE_FREEZE_READY` on the current two-config
TRAIN specification. Candidate freeze is authorized by TRAIN evidence only;
holdout remains sealed.

Boundaries: TRAIN `2020-10-20` through `2023-12-31`; historical holdout remains
sealed; validation was not run; FINAL OOS was not read; production was not
changed.  The run used the unified Research Runner in `NEW_ENTRY` mode and the
complete PLTR TRAIN daily calendar.

## Prior matrix disposition

The 158-run matrix is reclassified as
`MECHANICAL_COVERED_CALL_ENGINE_BASELINE`.  Its runner/accounting evidence
remains useful, but its candidate freeze is `SUPERSEDED_BEFORE_HOLDOUT`.
The original freeze artifact is retained unchanged as audit evidence.

| # | Human-style behavior | Tested? | Exact prior config IDs |
|---:|---|---|---|
| 1 | Block selling during a strong rally/breakout | NO | none |
| 2 | Sell only after a short-term surge | NO | none |
| 3 | Sell only when IV is high relative to its own history | NO | none (`IV_RISING` was only a one-day direction test) |
| 4 | Sell near PIT resistance/prior high | YES | `entry_timing_sweep_v1:004` |
| 5 | Do not sell before earnings | NO | none |
| 6 | Low delta / far OTM | YES | `dte_delta_profit_matrix_v2:000-007`, `:020-027`, `:040-047`, `:060-067` |
| 7 | Short-to-medium DTE | YES | `dte_delta_profit_matrix_v2:000-059` |
| 8 | 50% / 65% fast profit take | YES | `dte_delta_profit_matrix_v2:000-001`, then every four-run block's first two IDs through `:076-077`; `strike_roll_matrix_v2:000-001`, then every four-run block's first two IDs through `:056-057` |
| 9 | Cooldown after close | NO | none |
| 10 | Avoid continuous call coverage | NO | none |
| 11 | Roll-count cap | NO | none |
| 12 | Roll-debit cap | NO | none |
| 13 | Roll must materially raise strike | NO | none |
| 14 | Buy back and WAIT if no good roll exists | NO | none |
| 15 | WAIT when premium is insufficient | NO | none |
| 16 | Pause selling after strong trend resumes | NO | none |

## Comparison

The larger table below is retained as the pre-repair 12-config diagnostic
audit. The current owner-edited specification contains only
`CONSERVATIVE_30_45_50` and `A2_CONSERVATIVE_30_45_65`; it was not silently
expanded during the data repair.

All dollar amounts are per modeled 100-share benchmark.  `buy-and-hold wealth`
at the TRAIN end is `$1,717.00`.  Human-style WAIT and coverage ratios use all
804 TRAIN trading days.  Human-style roll debit was `$0` in every configuration
because no legal roll trigger occurred; Limited-roll therefore did not manufacture
performance by extending duration.

| Configuration | OPEN | WAIT | Covered | Premium | Buyback/roll cost | Net overlay | Combined wealth | Capped upside | Assignment exposure | Avg hold | Max DD | Mgmt |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Mechanical baseline | 25 | n/a | n/a | 1,211.70 | 1,608.20 | -396.50 | 1,320.50 | 515.00 | 0 | 97.32 | -744.20 | 115 execution legs |
| Prior frozen primary | 22 | n/a | n/a | 1,093.20 | 812.80 | 280.40 | 1,997.40 | 201.00 | 0 | 32.18 | -160.10 | n/a |
| A1 conservative 14–30 / 50% | 9 | 92.7% | 7.3% | 197.00 | 145.60 | 51.40 | 1,768.40 | 0 | 0 | 9.33 | -65.70 | 9 |
| A2 conservative 30–45 / 65% | 6 | 92.0% | 8.0% | 115.00 | 39.40 | 75.60 | 1,792.60 | 0 | 0 | 15.33 | -6.70 | 6 |
| B1 rally/IV 7–21 / 50% | 7 | 97.0% | 3.0% | 148.00 | 173.80 | -25.80 | 1,691.20 | 0 | 0 | 5.14 | -105.10 | 7 |
| B2 rally/IV 14–30 / 65% | 6 | 94.4% | 5.6% | 201.00 | 146.40 | 54.60 | 1,771.60 | 0 | 0 | 11.00 | -105.10 | 6 |
| C1 resistance 14–30 / 50% | 0 | 100% | 0% | 0 | 0 | 0 | 1,717.00 | 0 | 0 | n/a | 0 | 0 |
| C2 resistance 14–30 / 65% | 0 | 100% | 0% | 0 | 0 | 0 | 1,717.00 | 0 | 0 | n/a | 0 | 0 |
| D1 no-roll 14–30 | 9 | 92.7% | 7.3% | 213.00 | 161.60 | 51.40 | 1,768.40 | 0 | 0 | 9.33 | -82.10 | 9 |
| D2 no-roll 30–45 | 6 | 92.0% | 8.0% | 115.00 | 39.40 | 75.60 | 1,792.60 | 0 | 0 | 15.33 | -6.70 | 6 |
| E1 limited-roll 1x / 50% | 9 | 92.7% | 7.3% | 213.00 | 161.60 | 51.40 | 1,768.40 | 0 | 0 | 9.33 | -82.10 | 9 |
| E2 limited-roll 1x / 75% budget | 9 | 90.4% | 9.6% | 213.00 | 149.60 | 63.40 | 1,780.40 | 0 | 0 | 12.33 | -82.10 | 9 |
| E3 limited-roll 2x / 50% budget | 6 | 93.9% | 6.1% | 115.00 | 50.40 | 64.60 | 1,781.60 | 0 | 0 | 12.00 | -10.70 | 6 |
| E4 limited-roll 2x / 75% budget | 6 | 92.0% | 8.0% | 115.00 | 39.40 | 75.60 | 1,792.60 | 0 | 0 | 15.33 | -6.70 | 6 |

Human-style yearly net option overlay:

- A1: 2021 `+48.60`, 2022 `-24.60`, 2023 `+27.40`.
- A2: 2022 `+30.80`, 2023 `+44.80`.
- B1: 2021 `+30.60`, 2022 `-27.20`, 2023 `-29.20`.
- B2: 2021 `+90.60`, 2022 `-31.80`, 2023 `-4.20`.
- D1/E1: 2021 `+46.60`, 2022 `-22.60`, 2023 `+27.40`.
- D2/E4: 2022 `+30.80`, 2023 `+44.80`.
- E2: 2021 `+46.60`, 2022 `-17.60`, 2023 `+34.40`.
- E3: 2022 `+25.80`, 2023 `+38.80`.
- C1/C2: no trades.

## Gate result after Data Control Plane repair

The control plane imported 13 issuer-announced PLTR earnings events for the
TRAIN window. Each row carries a conservative PIT availability timestamp based
on the official announcement date and satisfies `event_asof < event_date`.
The rerun reports `PIT_TIMESTAMP_PRESENT`.

Current TRAIN candidates:

- `CONSERVATIVE_30_45_50`: 6 opens, 93.91% WAIT, 6.09% covered, premium
  `$115.00`, buyback/roll cost `$50.40`, net overlay `+$64.60`, average hold
  12.0 days, max drawdown `-$10.70`, positive in 2022 and 2023.
- `A2_CONSERVATIVE_30_45_65`: 6 opens, 92.04% WAIT, 7.96% covered, premium
  `$115.00`, buyback/roll cost `$39.40`, net overlay `+$75.60`, average hold
  15.33 days, max drawdown `-$6.70`, positive in 2022 and 2023.

Both have zero roll debit, capped upside, and assignment exposure in TRAIN.
The authoritative TRAIN classification is
`HUMAN_STYLE_TRAIN_CANDIDATE_FREEZE_READY`. This does not open holdout and is
not a production promotion.

## QQQ transfer status

The QQQ v3 family is declared in
`config/research/qqq_covered_call_human_style_train_v3.yaml`. Its 804-day
TRAIN calendar preflight ran, but signal discovery and lifecycle replay did
not run because canonical QQQ option partitions for 2020Q4–2023Q4 are not
readable by the current host. The unified market-data control plane now returns
`CANONICAL_FILE_ACCESS_DENIED` and attempts only exact-file ACL repair. The
current Windows account cannot take ownership, so the terminal blocker is
`CANONICAL_PERMISSION_REPAIR_REQUIRES_OWNER`. `selected_source` is empty and
no ClickHouse or other download was attempted. No alternate loader, raw file,
frozen ledger, validation split, holdout, or FINAL OOS was used.

QQQ status is therefore `OPTIONS_DATA_MISSING`, not a strategy result.
