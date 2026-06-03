from __future__ import annotations

from pipelines.db_connection import get_connection


def get_oracle_connection():
    return get_connection()
