from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from sema_dashboard.services.filter_catalog_service import coerce_filters_to_available_options, get_filter_options
from sema_dashboard.services.filter_state_service import get_active_date_range, get_active_filters, set_active_date_range
from sema_dashboard.ui.interactions import apply_pending_widget_sync, sync_filters_from_widgets, update_filter


def _format_option(value: str) -> str:
    return value if value else "Todos"


def render_left_filters() -> None:
    apply_pending_widget_sync()
    previous_filters = dict(st.session_state.filters)
    st.markdown(
    '<div class="filters-title">Filtros</div>',
    unsafe_allow_html=True
    )

    active_date_range = get_active_date_range()
    if st.session_state.get("filter_fecha_inicio_widget") != active_date_range.get("fecha_inicio"):
        st.session_state["filter_fecha_inicio_widget"] = active_date_range.get("fecha_inicio")
    if st.session_state.get("filter_fecha_fin_widget") != active_date_range.get("fecha_fin"):
        st.session_state["filter_fecha_fin_widget"] = active_date_range.get("fecha_fin")

    fecha_inicio = st.date_input(
        "Fecha inicio",
        value=active_date_range.get("fecha_inicio") or (date.today() - timedelta(days=7)),
        key="filter_fecha_inicio_widget",
    )

    fecha_fin = st.date_input(
        "Fecha fin",
        value=active_date_range.get("fecha_fin") or date.today(),
        key="filter_fecha_fin_widget",
    )

    if (
        fecha_inicio != active_date_range.get("fecha_inicio")
        or fecha_fin != active_date_range.get("fecha_fin")
    ):
        set_active_date_range(fecha_inicio, fecha_fin)
    sync_filters_from_widgets()

    active_filters = get_active_filters()
    options = get_filter_options(active_filters, st.session_state.active_map)
    normalized_filters = coerce_filters_to_available_options(st.session_state.filters, options)
    if normalized_filters != st.session_state.filters:
        pending = {
            key: value
            for key, value in normalized_filters.items()
            if st.session_state.filters.get(key) != value
        }
        st.session_state.filters = normalized_filters
        if pending:
            pending_widget_sync = dict(st.session_state.get("_pending_filter_widget_sync", {}))
            pending_widget_sync.update(pending)
            st.session_state["_pending_filter_widget_sync"] = pending_widget_sync
        st.rerun()

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

    if previous_filters.get("externo") and not externo and direccion:
        update_filter("direccion", "", sync_widget=True)
        st.rerun()

    if previous_filters.get("direccion") and not direccion and externo:
        update_filter("externo", "", sync_widget=True)
        st.rerun()

    if any(
        previous_filters.get(key) != st.session_state.filters.get(key)
        for key in ["externo", "direccion", "corredor", "acceso", "zona_auto", "estado_concert", "gestion_sema"]
    ):
        st.rerun()
