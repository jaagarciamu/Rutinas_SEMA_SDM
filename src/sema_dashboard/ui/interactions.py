from __future__ import annotations

import streamlit as st


def update_filter(key: str, value: object) -> None:
    filters = dict(st.session_state.filters)
    filters[key] = value
    st.session_state.filters = filters
