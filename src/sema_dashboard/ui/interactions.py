from __future__ import annotations

import streamlit as st

FILTER_WIDGET_KEYS = {
    "fecha_inicio": "filter_fecha_inicio_widget",
    "fecha_fin": "filter_fecha_fin_widget",
    "externo": "filter_externo_widget",
    "direccion": "filter_direccion_widget",
    "corredor": "filter_corredor_widget",
    "acceso": "filter_acceso_widget",
    "zona_auto": "filter_zona_auto_widget",
    "estado_concert": "filter_estado_concert_widget",
    "gestion_sema": "filter_gestion_sema_widget",
}

PENDING_FILTER_WIDGET_SYNC_KEY = "_pending_filter_widget_sync"


def update_filter(key: str, value: object, *, sync_widget: bool = False) -> None:
    filters = dict(st.session_state.filters)
    filters[key] = value
    st.session_state.filters = filters
    if sync_widget:
        pending = dict(st.session_state.get(PENDING_FILTER_WIDGET_SYNC_KEY, {}))
        pending[key] = value
        st.session_state[PENDING_FILTER_WIDGET_SYNC_KEY] = pending


def sync_filters_from_widgets() -> None:
    filters = dict(st.session_state.filters)
    changed = False
    for filter_key, widget_key in FILTER_WIDGET_KEYS.items():
        if widget_key in st.session_state and filters.get(filter_key) != st.session_state[widget_key]:
            filters[filter_key] = st.session_state[widget_key]
            changed = True
    if changed:
        st.session_state.filters = filters


def apply_pending_widget_sync() -> None:
    pending = dict(st.session_state.get(PENDING_FILTER_WIDGET_SYNC_KEY, {}))
    if not pending:
        return
    for filter_key, value in pending.items():
        widget_key = FILTER_WIDGET_KEYS.get(filter_key)
        if widget_key:
            st.session_state[widget_key] = value
    st.session_state[PENDING_FILTER_WIDGET_SYNC_KEY] = {}
