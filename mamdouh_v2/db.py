"""Connection helper: source DB attached read-only, output DB for all writes.

Every stage module must obtain its connection via get_connection() so the
read-only guarantee on market_data.duckdb is enforced in one place.
"""
from __future__ import annotations

import duckdb

import config

_log = config.setup_logging(__name__)


def get_connection() -> duckdb.DuckDBPyConnection:
    """Open a connection with src=source (read-only) and out=research DB.

    The default catalog is 'out' so plain `CREATE TABLE foo AS ...` lands in
    halal_research.duckdb; reads from the source must be qualified as
    `src.minute_bars` / `src.market_news`.
    """
    con = duckdb.connect()
    con.execute(f"ATTACH '{config.SOURCE_DB_PATH}' AS src (READ_ONLY)")
    con.execute(f"ATTACH '{config.OUTPUT_DB_PATH}' AS out")
    con.execute("USE out")
    return con


def table_exists(con: duckdb.DuckDBPyConnection, table_name: str, catalog: str = "out") -> bool:
    row = con.execute(
        """
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_catalog = ? AND table_name = ?
        """,
        [catalog, table_name],
    ).fetchone()
    return bool(row and row[0] > 0)


def row_count(con: duckdb.DuckDBPyConnection, table_name: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]


def skip_if_exists(con: duckdb.DuckDBPyConnection, table_name: str, force: bool, stage_name: str) -> bool:
    """Return True if this stage should be skipped (table already built)."""
    if not force and table_exists(con, table_name):
        n = row_count(con, table_name)
        _log.info(f"[{stage_name}] '{table_name}' already exists ({n:,} rows) - skipping (use force=True to rebuild)")
        return True
    return False
