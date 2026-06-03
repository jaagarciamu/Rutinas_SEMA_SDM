from __future__ import annotations

import streamlit as st


def render_shell() -> None:
    st.markdown('<div class="control-shell"></div>', unsafe_allow_html=True)
