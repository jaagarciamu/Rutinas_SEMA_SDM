from __future__ import annotations

import plotly.io as pio
import streamlit as st
import streamlit.components.v1 as components

from sema_dashboard.charts.detecciones_chart import build_detecciones_chart
from sema_dashboard.config import CHARTS, MATRICES, TEMP_FIXED_EXTERNALS
from sema_dashboard.repositories.detecciones_repository import fetch_detecciones
from sema_dashboard.state import close_overlay


def render_overlay() -> None:
    if not st.session_state.overlay_open:
        return

    if st.session_state.overlay_type == "chart":
        title = CHARTS.get(st.session_state.overlay_key, "Grafica")
    else:
        title = MATRICES.get(st.session_state.overlay_key, "Matriz")

    if st.session_state.overlay_type == "chart" and st.session_state.overlay_key == "detecciones":
        _render_detecciones_overlay(title)
    else:
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


def _render_detecciones_overlay(title: str) -> None:
    externo = TEMP_FIXED_EXTERNALS["detecciones"]
    try:
        detecciones_df = fetch_detecciones(
            st.session_state.filters.get("fecha_inicio"),
            st.session_state.filters.get("fecha_fin"),
        )
        fig, config = build_detecciones_chart(detecciones_df, externo)
        fig_html = pio.to_html(
            fig,
            include_plotlyjs=True,
            full_html=False,
            config=config,
            default_width="100%",
            default_height="660px",
        )
        components.html(
            f"""
            <div class="overlay-panel glass-panel" style="padding: 1.2rem;">
                <div class="overlay-header" style="margin-bottom: 0.8rem;">
                    <div>
                        <div class="eyebrow">Visualizacion</div>
                        <div class="overlay-title">{title}</div>
                    </div>
                </div>
                {fig_html}
            </div>
            """,
            height=760,
            scrolling=False,
        )
    except Exception as exc:
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
                    No fue posible cargar la grafica de detecciones para el externo {externo}: {exc}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
