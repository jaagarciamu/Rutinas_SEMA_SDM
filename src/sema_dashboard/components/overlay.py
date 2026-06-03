from __future__ import annotations

import streamlit as st

from sema_dashboard.config import CHARTS, MATRICES
from sema_dashboard.state import close_overlay


def render_overlay() -> None:
    if not st.session_state.overlay_open:
        return

    if st.session_state.overlay_type == "chart":
        title = CHARTS.get(st.session_state.overlay_key, "Grafica")
    else:
        title = MATRICES.get(st.session_state.overlay_key, "Matriz")

    st.markdown(
        f"""
        <div class="overlay-panel glass-panel">
            <div class="overlay-header">
                <div>
                    <div class="eyebrow">Visualizacion</div>
                    <div class="overlay-title">{title}</div>
                </div>
            </div>
            <div class="overlay-placeholder">
                Aqui ira la visualizacion final conectada al notebook.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Cerrar visualizacion", key="close_overlay_button"):
        close_overlay()
