from __future__ import annotations

import streamlit as st

from sema_dashboard.config import EXTERNAL_LINKS


LINK_LABELS = {
    "SUANET": "SUANET",
    "WEB SEMA": "WEBSEMA",
    "DATASTUDIO": "DETECCIONES",
    "ARCGIS": "EXPERIENCE",
}


def render_left_links() -> None:

    st.markdown(
        '<div class="panel-title">Enlaces</div>',
        unsafe_allow_html=True,
    )

    for key, data in EXTERNAL_LINKS.items():

        st.image(
            f"app/assets/logos/links/{data['logo']}",
            width=40,
        )

        st.link_button(
            LINK_LABELS.get(key, key),
            data["url"],
            use_container_width=False,
        )