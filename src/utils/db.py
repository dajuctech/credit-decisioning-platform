"""Tiny SQLite helper.

SQLite keeps the project zero-setup. In a cloud deployment this would be a
managed PostgreSQL instance; only this file would need to change.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from src.config import get_settings

SERVICE_TABLES_DDL = [
    """
    CREATE TABLE IF NOT EXISTS model_scores (
        request_id TEXT,
        application_id TEXT,
        borrower_id TEXT,
        partner_id TEXT,
        model_version TEXT,
        score REAL,
        decision TEXT,
        recommended_limit REAL,
        recommended_tenor_months INTEGER,
        reason_codes TEXT,
        missing_features TEXT,
        features_json TEXT,
        source TEXT,
        scored_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS api_requests (
        request_id TEXT,
        method TEXT,
        path TEXT,
        status_code INTEGER,
        latency_ms REAL,
        error TEXT,
        requested_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS monitoring_snapshots (
        snapshot_id TEXT,
        snapshot_at TEXT,
        check_name TEXT,
        subject TEXT,
        value REAL,
        threshold REAL,
        status TEXT,
        owner_layer TEXT,
        explanation TEXT
    )
    """,
]


def db_path() -> Path:
    return get_settings().database_path


@contextmanager
def connect(path: Path | None = None):
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_service_tables(path: Path | None = None) -> None:
    with connect(path) as conn:
        for ddl in SERVICE_TABLES_DDL:
            conn.execute(ddl)


def write_table(df: pd.DataFrame, name: str, path: Path | None = None, if_exists: str = "replace") -> None:
    with connect(path) as conn:
        df.to_sql(name, conn, if_exists=if_exists, index=False)


def read_sql(query: str, path: Path | None = None, params: tuple = ()) -> pd.DataFrame:
    with connect(path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def table_exists(name: str, path: Path | None = None) -> bool:
    with connect(path) as conn:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def insert_row(table: str, row: dict, path: Path | None = None) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with connect(path) as conn:
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))
