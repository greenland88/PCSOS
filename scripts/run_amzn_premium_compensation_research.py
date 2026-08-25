"""Research-only AMZN premium compensation analysis.

Consumes the canonical enriched pass-trade artifact. No strategy or threshold
code is imported or changed. Buckets are distribution-derived quartiles.
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "research_outputs" / "option_compensation_enriched_pass_trades.csv"
OUT = ROOT / "research_outputs" / "amzn_premium_compensation_research"


def pf(s):
    pos = s[s > 0].sum()
    neg = s[s < 0].sum()
    return float(pos / abs(neg)) if neg else None


def max_dd(s):
    curve = s.fillna(0).cumsum()
    return float((curve - curve.cummax()).min()) if len(curve) else None


def metrics(g, tail_cut):
    p = g.realized_pnl
    return {
        "trades": int(len(g)),
        "pnl": float(p.sum()),
        "expectancy": float(p.mean()),
        "pf": pf(p),
        "stop_rate": float(g.exit_reason.eq("STOP").mean()),
        "mae_5d_atr": float(g.mae_5d_atr.mean()),
        "mae_10d_atr": float(g.mae_10d_atr.mean()),
        "tail_loss_rate": float(p.le(tail_cut).mean()),
        "worst_trade": float(p.min()),
        "max_drawdown": max_dd(p),
    }


def run():
    d = pd.read_csv(SOURCE, parse_dates=["date"])
    d = d.loc[d.symbol.eq("AMZN")].copy().sort_values(["date", "run"]).reset_index(drop=True)
    d["year"] = d.date.dt.year
    d["credit_atr"] = d.initial_credit / d.atr14
    dte = (pd.to_datetime(d.expiration) - d.date).dt.days
    d["dte"] = dte
    # Transparent, distribution-derived tail definition; no unavailable risk field is invented.
    tail_cut = float(d.realized_pnl.quantile(0.10))
    q = d.credit_atr.quantile([0, .25, .5, .75, 1]).to_list()
    labels = ["Q1_low", "Q2", "Q3", "Q4_high"]
    d["credit_atr_bucket"] = pd.cut(d.credit_atr, bins=q, labels=labels, include_lowest=True, duplicates="drop")
    # If duplicate edges collapse a bucket, relabel from the actual categories.
    cats = list(d.credit_atr_bucket.cat.categories)
    labels = [str(x) for x in cats]
    d["credit_atr_bucket"] = d["credit_atr_bucket"].astype(str)
    rows = []
    for b, g in d.groupby("credit_atr_bucket", sort=False):
        r = {"bucket": b, "range_low": float(g.credit_atr.min()), "range_high": float(g.credit_atr.max())}
        r.update(metrics(g, tail_cut)); rows.append(r)
    buckets = pd.DataFrame(rows)
    year_rows = []
    for (year, b), g in d.groupby(["year", "credit_atr_bucket"], sort=True):
        year_rows.append({"year": int(year), "bucket": b, **metrics(g, tail_cut)})
    year_buckets = pd.DataFrame(year_rows)
    # Counterfactuals are descriptive filters, not proposed production thresholds.
    med = float(d.credit_atr.median())
    q75 = float(d.credit_atr.quantile(.75))
    filters = {
        "all": d.index == d.index,
        "credit_atr_ge_median": d.credit_atr.ge(med),
        "credit_atr_ge_q75": d.credit_atr.ge(q75),
    }
    counter = pd.DataFrame([{"filter": k, "cut": med if "median" in k else q75 if "q75" in k else None, **metrics(d[v], tail_cut)} for k, v in filters.items()])
    robustness = []
    for b in d.credit_atr_bucket.unique():
        x = year_buckets[year_buckets.bucket.eq(b)]
        robustness.append({"bucket": b, "years_present": int(x.year.nunique()), "positive_years": int((x.expectancy > 0).sum()), "year_expectancy_min": float(x.expectancy.min()), "year_expectancy_max": float(x.expectancy.max())})
    # Spearman is descriptive only; monotonicity is assessed on bucket expectancy and stop rate.
    bucket_order = {b: i for i, b in enumerate(d.credit_atr_bucket.drop_duplicates())}
    b2 = buckets.copy(); b2["order"] = b2.bucket.map(bucket_order)
    def spearman(a, b):
        return float(pd.Series(a).rank().corr(pd.Series(b).rank()))
    mono_exp = spearman(b2.order, b2.expectancy) if len(b2) >= 3 else None
    mono_stop = spearman(b2.order, b2.stop_rate) if len(b2) >= 3 else None
    summary = {
        "source": str(SOURCE), "symbol": "AMZN", "trades": int(len(d)),
        "tail_definition": "realized_pnl <= AMZN 10th percentile (distribution-derived)",
        "tail_cut": tail_cut, "credit_atr_median": med, "credit_atr_q75": q75,
        "availability_unavailable": ["bid_ask", "short_long_volume", "short_long_oi", "iv", "iv_rank", "expected_move", "planned_loss", "realized_mae_dollars"],
        "bucket_expectancy_spearman": mono_exp, "bucket_stop_rate_spearman": mono_stop,
        "year_robustness": robustness,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    buckets.to_csv(OUT / "credit_atr_buckets.csv", index=False)
    year_buckets.to_csv(OUT / "credit_atr_year_buckets.csv", index=False)
    counter.to_csv(OUT / "counterfactual_filters.csv", index=False)
    d.to_csv(OUT / "amzn_research_population.csv", index=False)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"summary": summary, "buckets": buckets.to_dict("records"), "counterfactuals": counter.to_dict("records")}, indent=2, default=str))


if __name__ == "__main__":
    run()
