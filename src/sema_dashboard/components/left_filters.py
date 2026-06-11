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

    options = get_filter_options(st.session_state.filters, st.session_state.active_map)
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

    direccion = st.selectbox(
        "Direccion",
        options=options["direccion"],
        index=options["direccion"].index(st.session_state.filters.get("direccion", "")),
        format_func=_format_option,
        key="filter_direccion_widget",
    )

    corredor = st.selectbox(
        "Corredor",
        options=options["corredor"],
        index=options["corredor"].index(st.session_state.filters.get("corredor", "")),
        format_func=_format_option,
        key="filter_corredor_widget",
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

    estado_concert = st.selectbox(
        "Estado Concert",
        options=options["estado_concert"],
        index=options["estado_concert"].index(st.session_state.filters.get("estado_concert", "")),
        format_func=_format_option,
        key="filter_estado_concert_widget",
    )

    gestion_sema = st.selectbox(
        "Gestion SEMA",
        options=options["gestion_sema"],
        index=options["gestion_sema"].index(st.session_state.filters.get("gestion_sema", "")),
        format_func=_format_option,
        key="filter_gestion_sema_widget",
    )

    update_filter("externo", externo)
    update_filter("direccion", direccion)
    update_filter("corredor", corredor)
    update_filter("acceso", acceso)
    update_filter("zona_auto", zona_auto)
    update_filter("estado_concert", estado_concert)
    update_filter("gestion_sema", gestion_sema)
