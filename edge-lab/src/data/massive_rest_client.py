"""Massive REST/WebSocket client -- INTENTIONALLY UNIMPLEMENTED STUB.

Why this exists: flat files are published end-of-day, so they can power
research and historical simulation but NOT a live same-day pick. Live
selection needs today's premarket + intraday minute bars from the Massive
REST or WebSocket API.

Why it is a stub: this repo refuses to invent endpoint paths or response
schemas. Before implementing, verify the aggregate-bars endpoint and auth
scheme in your Massive dashboard docs ("Accessing the API" tab), then fill
in _get() and today_minute_bars().
"""
from __future__ import annotations

import os


class MassiveRestClient:
    def __init__(self):
        self.base = os.environ.get("MASSIVE_API_BASE", "https://api.massive.com")  # VERIFY in docs
        self.api_key = os.environ.get("MASSIVE_API_KEY", "")

    def today_minute_bars(self, ticker: str):
        raise NotImplementedError(
            "Live data path not implemented. Verify the minute-aggregates "
            "endpoint in Massive's API docs, then implement here. Do not "
            "guess endpoints -- see README_TRUTH.md."
        )
