from __future__ import annotations

import streamlit as st

from sema_dashboard.config import CHARTS
from sema_dashboard.constants import CHART_ICONS
from sema_dashboard.state import open_overlay


def render_right_chart_toolbar() -> None:
    st.markdown(
        '<div class="right-chart-panel glass-panel"><div class="panel-title">Graficas</div>',
        unsafe_allow_html=True
    )
    for chart_key, label in CHARTS.items():
        if st.button(
            CHART_ICONS[chart_key],
            key=f"chart_{chart_key}",
            use_container_width=True
        ):
            open_overlay("chart", chart_key)
    st.markdown("</div>", unsafe_allow_html=True)
