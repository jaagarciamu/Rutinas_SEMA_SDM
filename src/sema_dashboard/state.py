from __future__ import annotations

import streamlit as st

from sema_dashboard.config import DEFAULT_FILTERS


def initialize_state() -> None:
    defaults = {
        "active_map": "inventario",
        "filters": DEFAULT_FILTERS.copy(),
        "selected_externo": None,
        "selected_feature_count": 0,
        "overlay_open": False,
        "overlay_type": None,
        "overlay_key": None,
        "externo_sheet_open": False,
        "map_view_state": {"latitude": 4.65, "longitude": -74.1, "zoom": 11.2},
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def switch_map(map_key: str) -> None:
    st.session_state.active_map = map_key
    st.session_state.overlay_open = False
    st.session_state.overlay_type = None
    st.session_state.overlay_key = None
    st.session_state.selected_externo = None
    st.session_state.selected_feature_count = 0
    st.session_state.externo_sheet_open = False


def open_overlay(overlay_type: str, overlay_key: str) -> None:
    st.session_state.overlay_open = True
    st.session_state.overlay_type = overlay_type
    st.session_state.overlay_key = overlay_key


def close_overlay() -> None:
    st.session_state.overlay_open = False
    st.session_state.overlay_type = None
    st.session_state.overlay_key = None
