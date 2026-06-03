from __future__ import annotations

import streamlit as st

from sema_dashboard.components.base import render_shell
from sema_dashboard.components.externo_sheet import render_externo_sheet
from sema_dashboard.components.left_filters import render_left_filters
from sema_dashboard.components.left_links import render_left_links
from sema_dashboard.components.logo_panel import render_logo_panel
from sema_dashboard.components.map_stage import render_map_stage
from sema_dashboard.components.overlay import render_overlay
from sema_dashboard.components.right_chart_toolbar import render_right_chart_toolbar
from sema_dashboard.components.right_map_selector import render_right_map_selector
from sema_dashboard.components.right_matrix_toolbar import render_right_matrix_toolbar
from sema_dashboard.components.top_bar import render_top_bar


def render_app() -> None:
    render_shell()
    logo_col, filter_col, center_col, right_col = st.columns(
    [0.55, 0.75, 9.0, 0.55],
    gap="small")

    with logo_col:
        render_logo_panel()
        render_left_links()

    with filter_col:
        render_left_filters()

    with center_col:
        render_map_stage()

    with right_col:
        render_right_map_selector()
        render_right_chart_toolbar()
        render_right_matrix_toolbar()

    render_overlay()
    render_externo_sheet()

