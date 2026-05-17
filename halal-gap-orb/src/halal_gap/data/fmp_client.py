"""Async FMP REST client with cache-first lookup, retry, and rate limiting.

The client mirrors a small subset of FMP endpoints we actually need.  Every
call is parquet-cached; force_refresh=True bypasses the cache.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import date
from typing import Any

import aiohttp
import pandas as pd
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from halal_gap.data.cache import cache_read, cache_write
from halal_gap.utils.config import settings
from halal_gap.utils.logging import log


class FMPError(RuntimeError):
    """Wraps any HTTP / API-level failure from FMP."""


@dataclass(slots=True)
class FMPClient:
    """Thin async wrapper around the FMP REST API.

    Example:
        >>> # async with FMPClient() as fmp:
        >>> #     df = await fmp.historical_price_eod("AAPL", "2024-01-01", "2024-02-01")
    """

    api_key: str | None = None
    session: aiohttp.ClientSession | None = None
    _sem: asyncio.Semaphore | None = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("FMP_API_KEY")
        cfg = settings()["fmp"]
        self._sem = asyncio.Semaphore(cfg["rate_limit_concurrency"])

    async def __aenter__(self) -> FMPClient:
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    # ---- low-level fetch -------------------------------------------------

    async def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        if not self.api_key:
            raise FMPError("FMP_API_KEY not set in env")
        if self.session is None:
            self.session = aiohttp.ClientSession()
        assert self._sem is not None
        params = {**params, "apikey": self.api_key}
        cfg = settings()["fmp"]
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(cfg["retry_attempts"]),
            wait=wait_exponential(multiplier=cfg["retry_backoff_seconds"]),
            retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
            reraise=True,
        ):
            with attempt:
                async with self._sem:
                    async with self.session.get(url, params=params, timeout=60) as resp:
                        if resp.status >= 400:
                            body = await resp.text()
                            raise FMPError(f"{resp.status} from {url}: {body[:200]}")
                        return await resp.json()
        raise FMPError("unreachable")

    async def _fetch(
        self,
        endpoint: str,
        params: dict[str, Any],
        *,
        stable: bool = False,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        cache_key = {"endpoint": endpoint, "stable": stable, **params}
        if not force_refresh:
            cached = cache_read(endpoint, cache_key)
            if cached is not None:
                return cached
        base = settings()["fmp"]["stable_base_url" if stable else "base_url"]
        url = f"{base}/{endpoint}"
        log.debug(f"FMP GET {endpoint} params={params}")
        data = await self._get_json(url, params)
        df = _to_df(data)
        cache_write(endpoint, cache_key, df)
        return df

    # ---- high-level endpoints -------------------------------------------

    async def company_profile(self, symbol: str) -> pd.DataFrame:
        """Return one-row DataFrame with sector / industry / mktCap."""
        return await self._fetch(f"profile/{symbol}", {})

    async def sp500_historical_constituents(self) -> pd.DataFrame:
        """All historical S&P 500 add/remove events. Use to build point-in-time list."""
        return await self._fetch("historical/sp500_constituent", {})

    async def sp500_current_constituents(self) -> pd.DataFrame:
        """Current S&P 500 constituents (use only for live trading, not backtest)."""
        return await self._fetch("sp500_constituent", {})

    async def historical_price_eod(
        self, symbol: str, start: date | str, end: date | str
    ) -> pd.DataFrame:
        """Daily OHLCV (full) for `symbol` between `start` and `end`."""
        data = await self._fetch(
            f"historical-price-full/{symbol}",
            {"from": str(start), "to": str(end)},
        )
        if "historical" in data.columns and len(data) > 0:
            rows = data["historical"].iloc[0]
            if isinstance(rows, list):
                df = pd.DataFrame(rows)
                df["symbol"] = symbol
                return df
        return data

    async def intraday_5min(
        self, symbol: str, start: date | str, end: date | str
    ) -> pd.DataFrame:
        """5-minute OHLCV bars between `start` and `end` (NY-local, no tz)."""
        return await self._fetch(
            f"historical-chart/5min/{symbol}",
            {"from": str(start), "to": str(end)},
        )

    async def shares_float(self, symbol: str) -> pd.DataFrame:
        """Float shares for `symbol`."""
        return await self._fetch("shares_float", {"symbol": symbol})

    async def stock_news(
        self, symbol: str, start: date | str, end: date | str, limit: int = 50
    ) -> pd.DataFrame:
        """Stock news for `symbol` within a date range."""
        return await self._fetch(
            "stock_news",
            {"tickers": symbol, "from": str(start), "to": str(end), "limit": limit},
        )

    async def press_releases(self, symbol: str, limit: int = 25) -> pd.DataFrame:
        """Press releases for `symbol`, newest first."""
        return await self._fetch(f"press-releases/{symbol}", {"limit": limit})


def _to_df(data: Any) -> pd.DataFrame:
    """Normalize FMP responses (list / dict / dict-with-historical) into a DataFrame."""
    if isinstance(data, list):
        return pd.DataFrame(data)
    if isinstance(data, dict):
        return pd.DataFrame([data])
    return pd.DataFrame()
