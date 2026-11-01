"""Ingestion (Data Engineering, step 1).

Reads raw source tables exactly as they arrive. No cleaning happens here:
keeping ingestion "dumb" means we can always see what the source sent us.

Supported formats: CSV, Parquet, or a SQLite database table.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from src.utils.logger import get_logger
from src.utils.paths import RAW_TABLES, Paths

log = get_logger(__name__)


def read_any(source: Path | str, table: str | None = None) -> pd.DataFrame:
    """Read one table from a CSV file, a Parquet file, or a SQLite database."""
    source = Path(source)
    suffix = source.suffix.lower()
    if suffix == ".csv":
        # dtype=str keeps raw values untouched so validation sees what really arrived
        return pd.read_csv(source, dtype=str, keep_default_na=True)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(source).astype("string")
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        if not table:
            raise ValueError("A table name is required when reading from SQLite")
        with sqlite3.connect(source) as conn:
            return pd.read_sql_query(f'SELECT * FROM "{table}"', conn).astype("string")
    raise ValueError(f"Unsupported file format: {source}")


def ingest_raw(paths: Paths | None = None) -> dict[str, pd.DataFrame]:
    paths = paths or Paths()
    tables: dict[str, pd.DataFrame] = {}
    for name in RAW_TABLES:
        src = paths.raw(name)
        if not src.exists():
            raise FileNotFoundError(f"Missing raw file {src}. Run `make data` (or the pipeline) first.")
        tables[name] = read_any(src)
        log.info("Ingested %-26s %7d rows", name, len(tables[name]))
    return tables


def ingest_incoming(paths: Paths | None = None) -> pd.DataFrame:
    paths = paths or Paths()
    return read_any(paths.incoming_applications)
