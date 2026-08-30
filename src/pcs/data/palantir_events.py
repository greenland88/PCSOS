"""Authorized point-in-time Palantir earnings-calendar adapter."""
from __future__ import annotations

from datetime import timedelta
from urllib.request import Request, urlopen
import pandas as pd


SOURCE_ID = "palantir_ir_earnings"
BASE = "https://palantir2020ipo.q4web.com/news-details"

# Publication dates and event dates are independently stated on Palantir's
# archived issuer releases. The adapter re-verifies both values in the remote
# page before returning a row; these constants are expectations, not an
# alternate unverified data source.
RELEASES = (
    ("2020-10-29", "2020-11-12", "2020/Palantir-Announces-Date-of-Third-Quarter-2020-Earnings-Release-and-Conference-Call"),
    ("2021-02-02", "2021-02-16", "2021/Palantir-Announces-Date-of-Fourth-Quarter-2020-Earnings-Release-and-Conference-Call"),
    ("2021-04-27", "2021-05-11", "2021/Palantir-Announces-Date-of-First-Quarter-2021-Earnings-Release-and-Conference-Call"),
    ("2021-07-29", "2021-08-12", "2021/Palantir-Announces-Date-of-Second-Quarter-2021-Earnings-Release-and-Conference-Call"),
    ("2021-10-26", "2021-11-09", "2021/Palantir-Announces-Date-of-Third-Quarter-2021-Earnings-Release-and-Conference-Call"),
    ("2022-02-03", "2022-02-17", "2022/Palantir-Announces-Date-of-Fourth-Quarter-2021-Earnings-Release-and-Video-Conference"),
    ("2022-04-19", "2022-05-09", "2022/Palantir-Announces-Date-of-First-Quarter-2022-Earnings-Release-and-Webcast"),
    ("2022-07-18", "2022-08-08", "2022/Palantir-Announces-Date-of-Second-Quarter-2022-Earnings-Release-and-Webcast"),
    ("2022-10-17", "2022-11-07", "2022/Palantir-Announces-Date-of-Third-Quarter-2022-Earnings-Release-and-Webcast"),
    ("2023-01-24", "2023-02-13", "2023/Palantir-Announces-Date-of-Fourth-Quarter-2022-Earnings-Release-and-Webcast"),
    ("2023-04-17", "2023-05-08", "2023/Palantir-Announces-Date-of-First-Quarter-2023-Earnings-Release-and-Webcast"),
    ("2023-07-17", "2023-08-07", "2023/Palantir-Announces-Date-of-Second-Quarter-2023-Earnings-Release-and-Webcast"),
    ("2023-10-12", "2023-11-02", "2023/Palantir-Announces-Date-of-Third-Quarter-2023-Earnings-Release-and-Webcast"),
)


def capabilities() -> dict[str, object]:
    return {"source_id": SOURCE_ID, "datasets": ["events"],
            "capabilities": ["EARNINGS_CALENDAR_PIT"], "point_in_time": True}


def fetch_pltr_earnings_events(*, fetcher=None) -> pd.DataFrame:
    fetcher = fetcher or _fetch
    rows = []
    for announced, event, slug in RELEASES:
        url = f"{BASE}/{slug}/default.aspx"
        body = fetcher(url)
        announced_ts, event_ts = pd.Timestamp(announced), pd.Timestamp(event)
        publication_token = announced_ts.strftime("%m/%d/%Y")
        event_tokens = {event_ts.strftime("%B %d, %Y").replace(" 0", " ")}
        if publication_token not in body or not any(token and token in body for token in event_tokens):
            raise RuntimeError(f"EVENT_PIT_SOURCE_CONTENT_MISMATCH:{url}")
        # Publication time is absent from the archive. Next-day UTC is the
        # conservative availability boundary and cannot introduce same-day
        # knowledge into a post-close decision.
        event_asof = (announced_ts + timedelta(days=1)).tz_localize("UTC")
        rows.append({"symbol": "PLTR", "date": event_ts, "event_date": event_ts,
                     "event_type": "EARNINGS", "event_asof": event_asof,
                     "source": "Palantir Investor Relations", "source_id": url})
    return pd.DataFrame(rows)


def _fetch(url: str) -> str:
    request = Request(url, headers={"User-Agent": "PCSDataControlPlane/1.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")
