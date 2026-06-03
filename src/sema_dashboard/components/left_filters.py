from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from sema_dashboard.ui.interactions import update_filter


def render_left_filters() -> None:

    st.markdown(
    '<div class="filters-title">Filtros</div>',
    unsafe_allow_html=True
    )

    fecha_inicio = st.date_input(
        "Fecha inicio",
        value=date.today() - timedelta(days=7),
        key="filter_fecha_inicio_widget",
    )

    fecha_fin = st.date_input(
        "Fecha fin",
        value=date.today(),
        key="filter_fecha_fin_widget",
    )

    externo = st.text_input(
        "Externo",
        value=st.session_state.filters["externo"]
    )

    acceso = st.text_input(
        "Acceso",
        value=st.session_state.filters["acceso"]
    )

    sensor = st.text_input(
        "Sensor",
        value=st.session_state.filters["sensor"]
    )

    zona_auto = st.text_input(
        "Zona automatica",
        value=st.session_state.filters["zona_auto"]
    )

    update_filter("fecha_inicio", fecha_inicio)
    update_filter("fecha_fin", fecha_fin)
    update_filter("externo", externo)
    update_filter("acceso", acceso)
    update_filter("sensor", sensor)
    update_filter("zona_auto", zona_auto)