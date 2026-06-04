from __future__ import annotations

import pandas as pd
import streamlit as st

from sema_dashboard.services.oracle_service import get_oracle_connection

ESTADOS_QUERY = 'SELECT * FROM EST_ACT_SEMA'


@st.cache_data(ttl=300, show_spinner="Cargando estados SEMA...")
def fetch_estados() -> pd.DataFrame:
    connection = get_oracle_connection()
    try:
        return pd.read_sql(ESTADOS_QUERY, connection)
    finally:
        connection.close()
