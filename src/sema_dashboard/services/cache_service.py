from __future__ import annotations

import streamlit as st


@st.cache_data(ttl=300, show_spinner=False)
def cache_frame(loader_name: str, payload: object) -> object:
    return payload
