"""Stage 1-2: load the halal ticker allowlist and materialize it in DuckDB."""
from __future__ import annotations

import duckdb

import config
from db import skip_if_exists
from halal_screen import load_halal_tickers

_log = config.setup_logging(__name__)


def run(con: duckdb.DuckDBPyConnection, force: bool = False) -> None:
    if skip_if_exists(con, "halal_universe", force, "Stage 1-2"):
        return

    _log.info(f"Loading halal tickers from {config.HALAL_TICKERS_PATH}")
    tickers = sorted(load_halal_tickers(config.HALAL_TICKERS_PATH))
    _log.info(f"Loaded {len(tickers):,} halal tickers")

    con.execute("CREATE OR REPLACE TABLE halal_universe (ticker VARCHAR PRIMARY KEY)")
    con.executemany("INSERT INTO halal_universe VALUES (?)", [(t,) for t in tickers])

    n_in_bars = con.execute(
        """
        SELECT COUNT(DISTINCT h.ticker)
        FROM halal_universe h
        JOIN src.minute_bars m ON m.ticker = h.ticker
        """
    ).fetchone()[0]
    n_missing = len(tickers) - n_in_bars
    _log.info(f"halal_universe: {len(tickers):,} tickers ({n_in_bars:,} have minute_bars data, {n_missing:,} missing)")
    if n_missing:
        _log.warning(f"{n_missing} halal tickers have no minute_bars rows at all - they will simply never appear in halal_daily_candidates")
