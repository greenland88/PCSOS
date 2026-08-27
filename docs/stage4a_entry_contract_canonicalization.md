# Stage 4A entry contract canonicalization

The canonicalization artifact is produced by `scripts/canonicalize_stage4a_entry_inputs.py`.
It preserves the existing 2.3 ATR candidate identity and derives DTE only from
the existing PCS convention: `expiration - decision date` in calendar days.
The artifact builder does not create candidates or infer missing formulas.

The existing support producer is `pcs.trend.support.analyze_support`; the
existing confirmation producer is `pcs.research.entry_confirmation`, but its
0–4 research score is not the 0–100 `TradeCandidate.price_confirmation` field,
so it is not silently substituted.  The current repository has no exact PCS
producer for the remaining requested fields at the Stage 4A candidate row
level.  Those fields remain null and are reported as `BLOCKED`.
