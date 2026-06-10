from __future__ import annotations

import streamlit as st

from sema_dashboard.state import open_overlay


def render_right_sheet_toolbar() -> None:
    externo = st.session_state.filters.get("externo", "") or st.session_state.selected_externo
    has_externo = bool(str(externo).strip()) if externo is not None else False
    help_text = None if has_externo else "Selecciona un externo para habilitar la ficha."

    st.markdown(
        '<div class="right-sheet-panel glass-panel"><div class="panel-title">Fichas</div>',
        unsafe_allow_html=True,
    )
    if st.button(
        "F. Tecnica",
        key="sheet_tecnica",
        use_container_width=True,
        disabled=not has_externo,
        help=help_text,
    ):
        open_overlay("sheet", "tecnica")
    st.markdown("</div>", unsafe_allow_html=True)
