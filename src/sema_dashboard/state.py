from __future__ import annotations

import streamlit as st

from sema_dashboard.config import DEFAULT_FILTERS, DEFAULT_MAP_VIEW_STATE, SEMA_EN_LINEA_VIEW_STATE
from sema_dashboard.services.filter_state_service import DATE_RANGE_STATE_KEY, build_default_date_ranges, normalize_date_ranges


def initialize_state() -> None:
    defaults = {
        "active_map": "inventario",
        "filters": DEFAULT_FILTERS.copy(),
        DATE_RANGE_STATE_KEY: build_default_date_ranges(),
        "selected_externo": None,
        "selected_feature_count": 0,
        "overlay_open": False,
        "overlay_type": None,
        "overlay_key": None,
        "map_view_state": DEFAULT_MAP_VIEW_STATE.copy(),
        "map_view_revision": 0,
        "sema_en_linea_view_state": SEMA_EN_LINEA_VIEW_STATE.copy(),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    if isinstance(st.session_state.get("filters"), dict) and "sensor" in st.session_state.filters:
        filters = dict(st.session_state.filters)
        filters.pop("sensor", None)
        st.session_state.filters = filters

    legacy_fecha_inicio = None
    legacy_fecha_fin = None
    if isinstance(st.session_state.get("filters"), dict):
        filters = dict(st.session_state.filters)
        legacy_fecha_inicio = filters.pop("fecha_inicio", None)
        legacy_fecha_fin = filters.pop("fecha_fin", None)
        normalized_filters = DEFAULT_FILTERS.copy()
        for key, value in DEFAULT_FILTERS.items():
            if key in filters:
                normalized_filters[key] = filters[key]
        if normalized_filters != st.session_state.filters:
            st.session_state.filters = normalized_filters

    normalized_ranges = normalize_date_ranges(st.session_state.get(DATE_RANGE_STATE_KEY))
    if legacy_fecha_inicio is not None:
        normalized_ranges["general"]["fecha_inicio"] = legacy_fecha_inicio
    if legacy_fecha_fin is not None:
        normalized_ranges["general"]["fecha_fin"] = legacy_fecha_fin
    if normalized_ranges != st.session_state.get(DATE_RANGE_STATE_KEY):
        st.session_state[DATE_RANGE_STATE_KEY] = normalized_ranges


def switch_map(map_key: str) -> None:
    st.session_state.active_map = map_key
    st.session_state.overlay_open = False
    st.session_state.overlay_type = None
    st.session_state.overlay_key = None
    st.session_state.selected_externo = None
    st.session_state.selected_feature_count = 0
    st.session_state.map_view_state = DEFAULT_MAP_VIEW_STATE.copy()
    st.session_state.map_view_revision = st.session_state.get("map_view_revision", 0) + 1
    st.session_state.sema_en_linea_view_state = SEMA_EN_LINEA_VIEW_STATE.copy()


def open_overlay(overlay_type: str, overlay_key: str) -> None:
    st.session_state.overlay_open = True
    st.session_state.overlay_type = overlay_type
    st.session_state.overlay_key = overlay_key


def close_overlay() -> None:
    st.session_state.overlay_open = False
    st.session_state.overlay_type = None
    st.session_state.overlay_key = None
