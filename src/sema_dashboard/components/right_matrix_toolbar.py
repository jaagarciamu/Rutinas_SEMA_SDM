from __future__ import annotations

import streamlit as st

from sema_dashboard.config import MATRICES
from sema_dashboard.constants import MATRIX_ICONS
from sema_dashboard.state import open_overlay


def render_right_matrix_toolbar() -> None:
    st.markdown('<div class="right-matrix-panel glass-panel"><div class="panel-title">Matrices</div>', unsafe_allow_html=True)
    for matrix_key, label in MATRICES.items():
        if st.button(MATRIX_ICONS[matrix_key], key=f"matrix_{matrix_key}", use_container_width=True):
            open_overlay("matrix", matrix_key)
    st.markdown("</div>", unsafe_allow_html=True)
