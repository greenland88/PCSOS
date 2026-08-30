import pandas as pd
def compute_breadth(frames:dict[str,pd.DataFrame], *, authoritative=False)->pd.DataFrame:
    rows=[]
    for s,f in frames.items():
        x=f.copy(); x["date"]=pd.to_datetime(x.date); c=pd.to_numeric(x.close,errors="coerce")
        for h in (20,50,200): x[f"above{h}"]=c>c.rolling(h,min_periods=h).mean()
        x["advance"]=c.diff()>0; x["high20"]=c>=c.rolling(20,min_periods=20).max(); x["low20"]=c<=c.rolling(20,min_periods=20).min(); rows.append(x.set_index("date"))
    if not rows:return pd.DataFrame()
    z=pd.concat(rows,keys=frames).reset_index().rename(columns={"level_0":"symbol"}); g=z.groupby("date")
    out=pd.DataFrame(index=sorted(z.date.unique())); out["eligible_count"]=g.close.count(); out["excluded_symbols"] = g.close.apply(lambda s: "".join([]))
    for h in (20,50,200): out[f"breadth_{h}"]=g[f"above{h}"].mean()
    out["advance_ratio"]=g.advance.mean(); out["new_high_minus_new_low"]=g.high20.sum()-g.low20.sum(); out["breadth_5d_change"]=out.breadth_50-out.breadth_50.shift(5); out["universe_authority"]="AUTHORITATIVE" if authoritative else "CONFIGURED_RESEARCH_UNIVERSE"; out["survivorship_risk"]="ABSENT" if authoritative else "PRESENT"; return out.reset_index(names="date")
