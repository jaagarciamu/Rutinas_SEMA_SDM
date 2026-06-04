from __future__ import annotations

import pandas as pd
import streamlit as st

from sema_dashboard.services.oracle_service import get_oracle_connection

INVENTARIO_QUERY = 'SELECT * FROM ESP_INT_SEMA'


@st.cache_data(ttl=300, show_spinner="Cargando inventario semaforico...")
def fetch_inventario() -> pd.DataFrame:
    connection = get_oracle_connection()
    try:
        return pd.read_sql(INVENTARIO_QUERY, connection)
    finally:
        connection.close()
