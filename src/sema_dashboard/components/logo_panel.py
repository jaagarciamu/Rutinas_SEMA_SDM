from __future__ import annotations

import streamlit as st


def render_logo_panel() -> None:

    st.markdown("<div class='logo-wrapper'>", unsafe_allow_html=True)

    st.image(
        "app/assets/logos/Prudencia_SEMA-fin.png",
        use_container_width=True
    )

    st.markdown("</div>", unsafe_allow_html=True)
