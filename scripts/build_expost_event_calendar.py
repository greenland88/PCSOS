"""Fetch and persist the authoritative ex-post earnings calendar.

This is deliberately separate from the strict PIT calendar. ``event_asof`` is
retrieval metadata only; it is not a claim that the date was known at entry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
from pathlib import Path
import re
from urllib.request import Request, urlopen

import pandas as pd

AMD_RESULTS = "https://ir.amd.com/financial-information/financial-results"
OUT = Path("data/raw/events/expost_historical_earnings_v1.csv")


def fetch(url: str) -> str:
    return urlopen(Request(url, headers={"User-Agent": "PCSOS historical research collector"}), timeout=60).read().decode("utf-8", "ignore")


def amd_rows(retrieved_at: str) -> list[dict]:
    html = fetch(AMD_RESULTS)
    rows = []
    for match in re.finditer(r"<h3>(.*?)</h3>(.*?)(?=<h3>|</body>)", html, re.S | re.I):
        title = re.sub(r"<.*?>", " ", unescape(match.group(1))).strip()
        if not re.search(r"2020|2021|2022|2023|2024|2025|2026", title) or "Q" not in title:
            continue
        link = re.search(r'href="(https://ir\.amd\.com/news-events/press-releases/detail/[^"]+)"', match.group(2))
        if not link:
            continue
        source_id = link.group(1)
        press = fetch(source_id)
        published = re.search(r'published_time" content="(\d{4}-\d{2}-\d{2})"', press)
        if not published:
            raise RuntimeError(f"AMD release has no publication date: {source_id}")
        rows.append({"event_type": "EARNINGS", "symbol": "AMD", "event_date": published.group(1),
                     "source": "AMD Investor Relations Financial Results", "source_id": source_id,
                     "event_asof": retrieved_at, "event_mode": "EVENT_MODE_EX_POST_HISTORICAL",
                     "date_basis": "earnings_release_publication_date", "source_retrieved_at": retrieved_at})
    return rows


def main() -> None:
    retrieved_at = datetime.now(timezone.utc).isoformat()
    existing = pd.read_csv("data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv")
    rows = amd_rows(retrieved_at)
    nvda = existing[(existing.event_type == "EARNINGS") & (existing.symbol == "NVDA")].copy()
    for _, r in nvda.iterrows():
        rows.append({"event_type": "EARNINGS", "symbol": "NVDA", "event_date": r.event_date,
                     "source": "NVIDIA Investor Relations Quarterly Results",
                     "source_id": r.source_url, "event_asof": retrieved_at,
                     "event_mode": "EVENT_MODE_EX_POST_HISTORICAL",
                     "date_basis": "earnings_release_publication_date", "source_retrieved_at": retrieved_at})
    out = pd.DataFrame(rows).drop_duplicates(["event_type", "symbol", "event_date"], keep="last")
    out.event_date = pd.to_datetime(out.event_date).dt.strftime("%Y-%m-%d")
    if out.duplicated(["event_type", "symbol", "event_date"]).any():
        raise RuntimeError("duplicate earnings identity")
    out = out.sort_values(["symbol", "event_date"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print({"path": str(OUT), "rows": len(out), "amd_rows": int((out.symbol == "AMD").sum()),
           "nvda_rows": int((out.symbol == "NVDA").sum()), "retrieved_at": retrieved_at})


if __name__ == "__main__":
    main()
