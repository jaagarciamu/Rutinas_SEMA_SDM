from __future__ import annotations

import streamlit as st


def render_externo_sheet() -> None:
    if st.session_state.active_map != "inventario":
        return
    st.markdown(
        """
        <div class="externo-sheet glass-panel">
            <div class="panel-title">Ficha del externo</div>
            <div class="placeholder-copy">
                Se activara cuando conectemos el mapa de inventario y exista una seleccion unica.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
