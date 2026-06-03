from __future__ import annotations

import streamlit as st

from sema_dashboard.config import MAPS
from sema_dashboard.constants import MAP_ICONS
from sema_dashboard.state import switch_map



def render_right_map_selector() -> None:
    st.markdown(
        '<div class="right-map-panel glass-panel"><div class="panel-title">Mapas</div>',
        unsafe_allow_html=True
    )
    for map_key, label in MAPS.items():
        is_active = st.session_state.active_map == map_key
        if st.button(
            MAP_ICONS[map_key],
            key=f"map_{map_key}",
            use_container_width=True,
            type="primary" if is_active else "secondary"
        ):
            switch_map(map_key)
    st.markdown("</div>", unsafe_allow_html=True)