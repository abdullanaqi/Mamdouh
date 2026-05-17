"""Fetch news + press releases from FMP and filter to before a decision time.

The single hard rule here is no-look-ahead: we MUST drop anything whose
`publishedDate` is at or after `as_of_ts`. The whole catalyst pipeline is
unsound otherwise.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from halal_gap.catalyst.models import NewsItem
from halal_gap.data.fmp_client import FMPClient
from halal_gap.utils.config import settings
from halal_gap.utils.logging import log
from halal_gap.utils.time_helpers import to_ny


def _parse_news_df(df: pd.DataFrame, source: str) -> list[NewsItem]:
    """Normalize a raw FMP news / press-release dataframe into NewsItem rows."""
    if df.empty:
        return []
    rows = df.copy()
    # FMP uses 'publishedDate' for stock_news and 'date' for press-releases.
    pub_col = "publishedDate" if "publishedDate" in rows.columns else "date"
    if pub_col not in rows.columns:
        return []
    title_col = "title" if "title" in rows.columns else "text"
    body_col = (
        "text" if "text" in rows.columns else ("title" if "title" in rows.columns else pub_col)
    )
    out: list[NewsItem] = []
    for _, r in rows.iterrows():
        try:
            ts = to_ny(str(r[pub_col]))
        except (ValueError, TypeError):
            continue
        title = str(r.get(title_col, "") or "")
        body = str(r.get(body_col, "") or "")
        url = str(r.get("url", "")) or None
        if not title and not body:
            continue
        out.append(
            NewsItem(
                published_at=ts,
                title=title.strip()[:300],
                body=body.strip()[:2000],
                source=source,
                url=url,
            )
        )
    return out


async def fetch_news_before(
    fmp: FMPClient, symbol: str, as_of_ts: datetime, *, lookback_hours: int | None = None
) -> list[NewsItem]:
    """Return news + press-release items published strictly before `as_of_ts`.

    Pulls from two FMP endpoints (stock_news, press-releases) over the last
    `lookback_hours` (config-defaulted), dedupes by (title, source) and sorts
    newest-first.
    """
    as_of_ts = to_ny(as_of_ts)
    hrs = lookback_hours or int(settings()["llm"]["news_lookback_hours"])
    start = (as_of_ts - timedelta(hours=hrs)).date()
    end = as_of_ts.date()

    items: list[NewsItem] = []
    try:
        n = await fmp.stock_news(symbol, start, end, limit=50)
        items.extend(_parse_news_df(n, "news"))
    except Exception as exc:  # noqa: BLE001
        log.warning(f"stock_news failed for {symbol}: {exc}")
    try:
        p = await fmp.press_releases(symbol, limit=25)
        items.extend(_parse_news_df(p, "press_release"))
    except Exception as exc:  # noqa: BLE001
        log.warning(f"press_releases failed for {symbol}: {exc}")

    # Hard no-look-ahead filter.
    items = [i for i in items if i.published_at < as_of_ts]
    # Stay within the lookback window.
    lookback_cutoff = as_of_ts - timedelta(hours=hrs)
    items = [i for i in items if i.published_at >= lookback_cutoff]

    # Dedup by (title, source).
    seen: set[tuple[str, str]] = set()
    deduped: list[NewsItem] = []
    for it in items:
        key = (it.title.lower(), it.source)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    deduped.sort(key=lambda x: x.published_at, reverse=True)
    return deduped
