"""Read and explore a DuckDB database: list tables, schemas, row counts, and samples."""

import argparse
import sys

import duckdb
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "db_path",
        nargs="?",
        default="market_data.duckdb",
        help="Path to the DuckDB file (default: market_data.duckdb).",
    )
    parser.add_argument(
        "-n", "--rows",
        type=int,
        default=5,
        help="Number of sample rows to show per table (default: 5).",
    )
    parser.add_argument(
        "--head",
        action="store_true",
        help="Show the first rows instead of the last rows.",
    )
    parser.add_argument(
        "-t", "--table",
        action="append",
        help="Only inspect this table (repeatable). Default: all tables.",
    )
    return parser.parse_args()


                                                                   
                                                                       
                                                                            
_TIME_COL_CANDIDATES = ["ts_ny", "ts_utc", "trade_date", "date", "t", "window_start"]


def show_table(con: duckdb.DuckDBPyConnection, name: str, rows: int, head: bool) -> None:
    print("=" * 60)
    print(f"TABLE: {name}")
    print("=" * 60)

    cols = con.execute(f'DESCRIBE "{name}"').fetchdf()
    print(cols[["column_name", "column_type"]].to_string(index=False))

    n = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    print(f"\nRows: {n:,}")

    if n == 0:
        print("\n(empty table)\n")
        return

    col_names = set(cols["column_name"])
    time_col = next((c for c in _TIME_COL_CANDIDATES if c in col_names), None)
    order_clause = f' ORDER BY "{time_col}"' if time_col else ""
    if time_col is None:
        print("\n(no time column found — row order is unspecified)")

    k = min(rows, n)
    if head:
        print(f"\nFirst {k} rows:")
        sample = con.execute(f'SELECT * FROM "{name}"{order_clause} LIMIT {k}').fetchdf()
    else:
        print(f"\nLast {k} rows:")
        sample = con.execute(
            f'SELECT * FROM "{name}"{order_clause} LIMIT {k} OFFSET {n - k}'
        ).fetchdf()
    print(sample.to_string(index=False))
    print()


def main() -> int:
    args = parse_args()

                                          
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", None)

    try:
                                                                       
        con = duckdb.connect(args.db_path, read_only=True)
    except duckdb.Error as exc:
        print(f"Could not open '{args.db_path}': {exc}", file=sys.stderr)
        return 1

    try:
        all_tables = [name for (name,) in con.execute("SHOW TABLES").fetchall()]
        if args.table:
            missing = [t for t in args.table if t not in all_tables]
            if missing:
                print(f"Table(s) not found: {', '.join(missing)}", file=sys.stderr)
                return 1
            tables = args.table
        else:
            tables = all_tables

        print(f"Tables ({len(all_tables)}):")
        for name in all_tables:
            print(f"  - {name}")
        print()

        for name in tables:
            show_table(con, name, args.rows, args.head)
    finally:
        con.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
