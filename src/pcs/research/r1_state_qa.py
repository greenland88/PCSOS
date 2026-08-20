from pathlib import Path
import duckdb, pandas as pd

ROOT=Path(__file__).resolve().parents[3]
def run():
    c=duckdb.connect(); c.execute("set enable_progress_bar=false")
    d=c.execute("select ticker,state,count(*) n from (select distinct ticker,date,state from read_parquet('research_outputs/r1_external_batches/*.parquet')) group by ticker,state").fetchdf(); c.close()
    d.to_csv(ROOT/"research_outputs/r1_state_counts_by_ticker_v1.csv",index=False)
    s=d.groupby("state")["n"].sum().reset_index(name="valid_state_days"); s["share"]=s.valid_state_days/s.valid_state_days.sum(); s.to_csv(ROOT/"research_outputs/r1_state_counts_v1.csv",index=False)
    r1=d[d.state.eq("R1_NORMAL")].groupby("ticker").n.sum(); freq=pd.DataFrame({"ticker":r1.index,"R1_count":r1.values}); u=pd.read_csv(ROOT/"research_outputs/r1_external_validation_universe_v1.csv"); u["years"]=(pd.to_datetime(u.last_date)-pd.to_datetime(u.first_date)).dt.days/365.25; freq=freq.merge(u[["ticker","years"]],on="ticker",how="left"); freq["R1_per_year"]=freq.R1_count/freq.years.clip(lower=1)
    freq.to_csv(ROOT/"research_outputs/r1_frequency_qa_v1.csv",index=False)
    return d,s
if __name__=="__main__":
    d,s=run(); print(s.to_string(index=False)); print({"tickers":d.ticker.nunique(),"state_rows":len(d)})
