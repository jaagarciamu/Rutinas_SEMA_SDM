from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st

from sema_dashboard.services.oracle_service import get_oracle_connection

DETECCIONES_QUERY = """
SELECT *
FROM DET_RESUM_SEMA_15M
WHERE "Tiempo" >= :fecha_inicio
  AND "Tiempo" < :fecha_fin
"""


def _resolve_window(fecha_inicio: date | None, fecha_fin: date | None) -> tuple[datetime, datetime]:
    end_date = fecha_fin or date.today()
    start_date = fecha_inicio or (end_date - timedelta(days=7))
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)
    return start_dt, end_dt


@st.cache_data(ttl=300, show_spinner="Cargando detecciones SEMA...")
def fetch_detecciones(fecha_inicio: date | None = None, fecha_fin: date | None = None) -> pd.DataFrame:
    start_dt, end_dt = _resolve_window(fecha_inicio, fecha_fin)
    connection = get_oracle_connection()
    try:
        return pd.read_sql(
            DETECCIONES_QUERY,
            connection,
            params={"fecha_inicio": start_dt, "fecha_fin": end_dt},
        )
    finally:
        connection.close()
