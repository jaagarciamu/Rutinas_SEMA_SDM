from __future__ import annotations

import streamlit as st

from sema_dashboard.config import MAPS


def render_top_bar() -> None:
    title = MAPS[st.session_state.active_map]
    st.markdown(
        f"""
        <div class="top-title-panel glass-panel">
            <div class="eyebrow">Centro de control</div>
            <div class="map-title">{title}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
