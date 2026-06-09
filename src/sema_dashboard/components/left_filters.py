from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from sema_dashboard.services.filter_catalog_service import coerce_filters_to_available_options, get_filter_options
from sema_dashboard.ui.interactions import update_filter


def _format_option(value: str) -> str:
    return value if value else "Todos"


def render_left_filters() -> None:
    st.markdown(
    '<div class="filters-title">Filtros</div>',
    unsafe_allow_html=True
    )

    fecha_inicio = st.date_input(
        "Fecha inicio",
        value=st.session_state.filters.get("fecha_inicio") or (date.today() - timedelta(days=7)),
        key="filter_fecha_inicio_widget",
    )

    fecha_fin = st.date_input(
        "Fecha fin",
        value=st.session_state.filters.get("fecha_fin") or date.today(),
        key="filter_fecha_fin_widget",
    )

    update_filter("fecha_inicio", fecha_inicio)
    update_filter("fecha_fin", fecha_fin)

    options = get_filter_options(st.session_state.filters)
    normalized_filters = coerce_filters_to_available_options(st.session_state.filters, options)
    if normalized_filters != st.session_state.filters:
        st.session_state.filters = normalized_filters

    externo = st.selectbox(
        "Externo",
        options=options["externo"],
        index=options["externo"].index(st.session_state.filters.get("externo", "")),
        format_func=_format_option,
        key="filter_externo_widget",
    )

    acceso = st.selectbox(
        "Acceso",
        options=options["acceso"],
        index=options["acceso"].index(st.session_state.filters.get("acceso", "")),
        format_func=_format_option,
        key="filter_acceso_widget",
    )

    zona_auto = st.selectbox(
        "Zona automatica",
        options=options["zona_auto"],
        index=options["zona_auto"].index(st.session_state.filters.get("zona_auto", "")),
        format_func=_format_option,
        key="filter_zona_auto_widget",
    )

    update_filter("externo", externo)
    update_filter("acceso", acceso)
    update_filter("zona_auto", zona_auto)
