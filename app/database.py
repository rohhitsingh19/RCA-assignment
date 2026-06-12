import duckdb
import os

# Path to the CSV file
CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "quick_commerce_orders_gold_20260422.csv")

# Single shared connection
_conn = None


def get_connection() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is None:
        _conn = duckdb.connect(database=":memory:")
        _load_data(_conn)
    return _conn


def _load_data(conn: duckdb.DuckDBPyConnection):
    csv_path = os.path.abspath(CSV_PATH)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS orders AS
        SELECT * FROM read_csv_auto('{csv_path}', header=true, nullstr='')
    """)
    print(f"[DB] Loaded orders table from {csv_path}")
    count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    print(f"[DB] Rows loaded: {count}")


def query(sql: str) -> list[dict]:
    """Run a SQL query and return list of dicts."""
    print(f"\n[DB] Agent running SQL: \n{sql}\n")
    conn = get_connection()
    result = conn.execute(sql).fetchdf()
    # Replace NaN with None for JSON serialisation. Must cast to object first!
    result = result.astype(object).where(result.notna(), None)
    return result.to_dict(orient="records")
