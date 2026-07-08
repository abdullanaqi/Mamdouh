"""Read-only table report for market_data.duckdb.

Database mutation is intentionally reserved for load_data.py.
"""

import duckdb

KEEP = {"market_news", "minute_bars"}

con = duckdb.connect("market_data.duckdb", read_only=True)

all_tables = [name for (name,) in con.execute("SHOW TABLES").fetchall()]
extra = [t for t in all_tables if t not in KEEP]

print(f"Tables found ({len(all_tables)}): {all_tables}")
print(f"Keeping: {sorted(KEEP & set(all_tables))}")
print(f"Extra tables that would previously have been dropped ({len(extra)}): {extra}")
print("No changes made. Only load_data.py is allowed to modify this database.")

con.close()
