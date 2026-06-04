from __future__ import annotations

import streamlit as st

from sema_dashboard.charts.detecciones_chart import build_detecciones_chart
from sema_dashboard.charts.ocupacion_chart import build_ocupacion_chart
from sema_dashboard.charts.planes_chart import build_planes_chart
from sema_dashboard.charts.scatter_chart import build_scatter_chart
from sema_dashboard.config import CHARTS, MATRICES
from sema_dashboard.matrices.dia_hora_matrix import build_dia_hora_matrix
from sema_dashboard.repositories.detecciones_repository import fetch_detecciones
from sema_dashboard.repositories.planes_repository import fetch_planes
from sema_dashboard.state import close_overlay

DIALOG_MAX_WIDTH = 588
DIALOG_PLOT_HEIGHT = 405


def render_overlay() -> None:
    if not st.session_state.overlay_open:
        return

    if st.session_state.overlay_type == "chart":
        key = st.session_state.overlay_key
        if key == "detecciones":
            _render_detecciones_dialog()
            return
        if key == "ocupacion":
            _render_ocupacion_dialog()
            return
        if key == "scatter":
            _render_scatter_dialog()
            return
        if key == "planes":
            _render_planes_dialog()
            return
        title = CHARTS.get(key, "Grafica")
    else:
        key = st.session_state.overlay_key
        if key == "dia_hora":
            _render_dia_hora_dialog()
            return
        title = MATRICES.get(key, "Matriz")

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


def _apply_dialog_theme(max_width: int = DIALOG_MAX_WIDTH) -> None:
    st.markdown(
        f"""
        <style>
        [data-testid="stDialog"] [data-testid="stDialogContent"] {{
            background: #111111;
        }}
        [data-testid="stDialog"] [data-testid="stDialogContent"] > div {{
            max-width: {max_width}px;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _dismiss_dialog() -> None:
    close_overlay()


def _render_close_button(key: str) -> None:
    if st.button("Cerrar visualizacion", key=key, width="stretch"):
        close_overlay()
        st.rerun()


def _selected_externo() -> str | None:
    value = st.session_state.filters.get("externo", "")
    return str(value) if value else None


def _build_chart_error_message(chart_name: str, externo: str | None, exc: Exception) -> str:
    raw_message = str(exc)
    scope = f"el externo {externo}" if externo else "la red"
    if "No hay datos para el externo" in raw_message or "No hay planes visibles para el externo" in raw_message:
        return f"No hay datos de {chart_name.lower()} para {scope} en el rango seleccionado."
    if raw_message.strip("'") in {"ext", "Acceso", "Deteccion", "Tiempo", "Ocupacion", "Externo", "Plan_ingresa", "Plan_finaliza", "Referencia"}:
        return (
            f"La fuente de {chart_name.lower()} no contiene una columna requerida "
            f"para la grafica: {raw_message.strip(chr(39))}."
        )
    return f"No fue posible cargar la grafica de {chart_name.lower()} para {scope}: {raw_message}"


def _build_matrix_error_message(matrix_name: str, externo: str | None, exc: Exception) -> str:
    raw_message = str(exc)
    scope = f"el externo {externo}" if externo else "la red"
    if "No hay datos para el externo" in raw_message:
        return f"No hay datos de {matrix_name.lower()} para {scope} en el rango seleccionado."
    if raw_message.strip("'") in {"ext", "Deteccion", "Tiempo"}:
        return (
            f"La fuente de {matrix_name.lower()} no contiene una columna requerida "
            f"para la matriz: {raw_message.strip(chr(39))}."
        )
    return f"No fue posible cargar la matriz de {matrix_name.lower()} para {scope}: {raw_message}"


@st.dialog("Visualizacion · Detecciones", width="large", dismissible=True, on_dismiss=_dismiss_dialog)
def _render_detecciones_dialog() -> None:
    externo = _selected_externo()
    try:
        detecciones_df = fetch_detecciones(
            st.session_state.filters.get("fecha_inicio"),
            st.session_state.filters.get("fecha_fin"),
            externo,
        )
        fig, config = build_detecciones_chart(detecciones_df, externo)
        _apply_dialog_theme()
        st.plotly_chart(fig, width="stretch", height=DIALOG_PLOT_HEIGHT, theme=None, config=config)
    except Exception as exc:
        st.error(_build_chart_error_message("Detecciones", externo, exc))
    _render_close_button("close_overlay_detecciones_button")


@st.dialog("Visualizacion · Ocupacion", width="large", dismissible=True, on_dismiss=_dismiss_dialog)
def _render_ocupacion_dialog() -> None:
    externo = _selected_externo()
    try:
        detecciones_df = fetch_detecciones(
            st.session_state.filters.get("fecha_inicio"),
            st.session_state.filters.get("fecha_fin"),
            externo,
        )
        fig, config = build_ocupacion_chart(detecciones_df, externo)
        _apply_dialog_theme()
        st.plotly_chart(fig, width="stretch", height=DIALOG_PLOT_HEIGHT, theme=None, config=config)
    except Exception as exc:
        st.error(_build_chart_error_message("Ocupacion", externo, exc))
    _render_close_button("close_overlay_ocupacion_button")


@st.dialog("Visualizacion · Scatter", width="large", dismissible=True, on_dismiss=_dismiss_dialog)
def _render_scatter_dialog() -> None:
    externo = _selected_externo()
    try:
        detecciones_df = fetch_detecciones(
            st.session_state.filters.get("fecha_inicio"),
            st.session_state.filters.get("fecha_fin"),
            externo,
        )
        fig, config = build_scatter_chart(detecciones_df, externo)
        _apply_dialog_theme(max_width=540)
        st.plotly_chart(fig, width="stretch", theme=None, config=config)
    except Exception as exc:
        st.error(_build_chart_error_message("Scatter", externo, exc))
    _render_close_button("close_overlay_scatter_button")


@st.dialog("Visualizacion · Planes", width="large", dismissible=True, on_dismiss=_dismiss_dialog)
def _render_planes_dialog() -> None:
    externo = _selected_externo()
    if not externo:
        _apply_dialog_theme(max_width=540)
        st.info("La gráfica de planes semafóricos requiere seleccionar un único externo en los filtros.")
        _render_close_button("close_overlay_planes_button")
        return
    try:
        planes_df = fetch_planes(
            st.session_state.filters.get("fecha_inicio"),
            st.session_state.filters.get("fecha_fin"),
            externo,
        )
        fig, config = build_planes_chart(
            planes_df,
            externo,
            st.session_state.filters.get("fecha_inicio"),
            st.session_state.filters.get("fecha_fin"),
        )
        _apply_dialog_theme(max_width=540)
        st.plotly_chart(fig, width="stretch", theme=None, config=config)
    except Exception as exc:
        st.error(_build_chart_error_message("Planes", externo, exc))
    _render_close_button("close_overlay_planes_button")


@st.dialog("Visualizacion · Matriz Dia x Hora", width="large", dismissible=True, on_dismiss=_dismiss_dialog)
def _render_dia_hora_dialog() -> None:
    externo = _selected_externo()
    try:
        detecciones_df = fetch_detecciones(
            st.session_state.filters.get("fecha_inicio"),
            st.session_state.filters.get("fecha_fin"),
            externo,
        )
        fig, config = build_dia_hora_matrix(
            detecciones_df,
            externo,
            st.session_state.filters.get("fecha_inicio"),
            st.session_state.filters.get("fecha_fin"),
        )
        _apply_dialog_theme(max_width=540)
        st.plotly_chart(fig, width="stretch", theme=None, config=config)
    except Exception as exc:
        st.error(_build_matrix_error_message("Matriz Dia x Hora", externo, exc))
    _render_close_button("close_overlay_dia_hora_button")
