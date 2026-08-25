"""DuckDB views over derived Parquet partitions."""
from pathlib import Path
import duckdb
from .storage_schema import DAILY_FIELDS, OPTION_FIELDS

_REPO_ROOT = Path(__file__).resolve().parents[3]

def _default_path(value, default):
    return _REPO_ROOT / default if str(value).replace("\\", "/") == default else Path(value)

def connect(path="data/duckdb/pcs.duckdb"):
    if path != ":memory:":
        path = _default_path(path, "data/duckdb/pcs.duckdb")
        path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def refresh_views(con, parquet_root="data/parquet", symbols=None):
    parquet_root = _default_path(parquet_root, "data/parquet")
    if symbols:
        symbols=[s.upper() for s in symbols]
        option_paths=[str(p).replace("\\", "/") for s in symbols for p in (Path(parquet_root)/"options"/f"symbol={s}").glob("**/*.parquet")]
        daily_paths=[str(p).replace("\\", "/") for s in symbols for p in (Path(parquet_root)/"daily"/f"symbol={s}").glob("**/*.parquet")]
        options="["+",".join("'"+p+"'" for p in option_paths)+"]"; daily="["+",".join("'"+p+"'" for p in daily_paths)+"]"; option_sql=options; daily_sql=daily
    else:
        options = str(Path(parquet_root) / "options" / "**" / "*.parquet").replace("\\", "/")
        daily = str(Path(parquet_root) / "daily" / "**" / "*.parquet").replace("\\", "/")
        option_sql="'"+options+"'"; daily_sql="'"+daily+"'"; option_paths=list((Path(parquet_root)/"options").glob("**/*.parquet")); daily_paths=list((Path(parquet_root)/"daily").glob("**/*.parquet"))
    if option_paths:
        con.execute(f"CREATE OR REPLACE VIEW options AS SELECT * FROM read_parquet({option_sql}, union_by_name=true, hive_partitioning=true)")
    else:
        con.execute("CREATE OR REPLACE VIEW options AS SELECT " + ", ".join(f"NULL AS {field}" for field in OPTION_FIELDS) + " WHERE FALSE")
    if daily_paths:
        con.execute(f"CREATE OR REPLACE VIEW daily_prices AS SELECT * FROM read_parquet({daily_sql}, union_by_name=true, hive_partitioning=true)")
    else:
        con.execute("CREATE OR REPLACE VIEW daily_prices AS SELECT " + ", ".join(f"NULL AS {field}" for field in DAILY_FIELDS) + " WHERE FALSE")
    derived = Path(parquet_root) / "derived"
    for name in ("daily_indicators", "trend_history", "option_features", "market_features"):
        glob = str(derived / name / "*.parquet").replace("\\", "/")
        if list((derived / name).glob("*.parquet")):
            con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{glob}', union_by_name=true)")
    # Research artifacts are stored below the canonical parquet root.  Using
    # ``parent / research`` silently points at the legacy ``data/research``
    # directory and leaves the view disconnected from its writer.
    research = Path(parquet_root) / "research"
    trades = str(research / "pcs_backtest_trades" / "**" / "*.parquet").replace("\\", "/")
    if list((research / "pcs_backtest_trades").glob("**/*.parquet")):
        con.execute(f"CREATE OR REPLACE VIEW pcs_backtest_trades AS SELECT * FROM read_parquet('{trades}', union_by_name=true, hive_partitioning=true)")


def query_option_chain(con, symbol, trade_date):
    return con.execute("SELECT * FROM options WHERE symbol = ? AND trade_date = ?", [symbol.upper(), trade_date]).fetchdf()


def query_daily(con, symbol, start_date, end_date):
    return con.execute("SELECT * FROM daily_prices WHERE symbol = ? AND date BETWEEN ? AND ? ORDER BY date", [symbol.upper(), start_date, end_date]).fetchdf()
