from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st

from sema_dashboard.services.oracle_service import get_oracle_connection

DETECCIONES_QUERY = """
SELECT *
FROM DET_APP_SEMA
WHERE "Tiempo" >= :fecha_inicio
  AND "Tiempo" < :fecha_fin
"""

DETECCIONES_QUERY_BY_EXTERNAL = """
SELECT *
FROM DET_APP_SEMA
WHERE "Tiempo" >= :fecha_inicio
  AND "Tiempo" < :fecha_fin
  AND ext = :externo
"""

DETECCIONES_CATALOG_QUERY = """
SELECT DISTINCT ext, "Acceso"
FROM DET_APP_SEMA
WHERE "Tiempo" >= :fecha_inicio
  AND "Tiempo" < :fecha_fin
"""

DETECCIONES_CATALOG_QUERY_BY_EXTERNAL = """
SELECT DISTINCT ext, "Acceso"
FROM DET_APP_SEMA
WHERE "Tiempo" >= :fecha_inicio
  AND "Tiempo" < :fecha_fin
  AND ext = :externo
"""


def _resolve_window(fecha_inicio: date | None, fecha_fin: date | None) -> tuple[datetime, datetime]:
    end_date = fecha_fin or date.today()
    start_date = fecha_inicio or (end_date - timedelta(days=7))
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    return start_dt, end_dt


@st.cache_data(ttl=300, show_spinner="Cargando detecciones SEMA...")
def fetch_detecciones(
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    externo: str | None = None,
) -> pd.DataFrame:
    start_dt, end_dt = _resolve_window(fecha_inicio, fecha_fin)
    connection = get_oracle_connection()
    try:
        query = DETECCIONES_QUERY_BY_EXTERNAL if externo else DETECCIONES_QUERY
        params = {"fecha_inicio": start_dt, "fecha_fin": end_dt}
        if externo:
            params["externo"] = str(externo)
        return pd.read_sql(
            query,
            connection,
            params=params,
        )
    finally:
        connection.close()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_detecciones_catalog(
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    externo: str | None = None,
) -> pd.DataFrame:
    start_dt, end_dt = _resolve_window(fecha_inicio, fecha_fin)
    connection = get_oracle_connection()
    try:
        query = DETECCIONES_CATALOG_QUERY_BY_EXTERNAL if externo else DETECCIONES_CATALOG_QUERY
        params = {"fecha_inicio": start_dt, "fecha_fin": end_dt}
        if externo:
            params["externo"] = str(externo)
        return pd.read_sql(query, connection, params=params)
    finally:
        connection.close()
