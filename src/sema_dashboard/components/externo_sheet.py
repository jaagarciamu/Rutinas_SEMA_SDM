from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from sema_dashboard.repositories.inventario_repository import fetch_inventario
from sema_dashboard.transforms.inventario import build_inventario_dataset


def render_externo_sheet() -> None:
    if st.session_state.active_map != "inventario":
        return

    externo = st.session_state.filters.get("externo", "") or st.session_state.selected_externo
    if not externo:
        _render_placeholder()
        return

    inventario_raw = fetch_inventario()
    inventario = build_inventario_dataset(inventario_raw, filters=None)
    ficha = inventario[inventario["externo"].astype(str) == str(externo)].copy()
    if ficha.empty:
        _render_placeholder(f"No se encontró información para el externo {externo}.")
        return

    row = ficha.iloc[0]
    st.session_state.selected_externo = str(externo)
    st.session_state.externo_sheet_open = True

    st.caption("FICHA TÉCNICA")
    st.subheader(f"Externo {externo}")

    info_col, links_col = st.columns([2.4, 1.0], gap="large")

    with info_col:
        left_col, right_col = st.columns(2, gap="medium")
        left_fields = [
            ("Dirección corta", _safe_value(row, "DIRECCION CORTA")),
            ("Localidad", _safe_value(row, "localidad")),
            ("Zona planeamiento", _safe_value(row, "ZONA PLANEAMIENTO")),
            ("Zona automática", _safe_value(row, "ZONA AUTO")),
            ("Referencia equipo", _safe_value(row, "REFERENCIA EQUIPO")),
            ("Fecha instalación", _safe_value(row, "FECHA DE INSTALACION")),
            ("Operación actual", _safe_value(row, "OPERACION ACTUAL")),
        ]
        right_fields = [
            ("Tipo intersección", _safe_value(row, "TIPO DE INTERSECCION")),
            ("Grupos vehiculares", _safe_value(row, "Grupos Vehiculares")),
            ("Grupos peatonales", _safe_value(row, "Grupos Peatonales")),
            ("Wide", _safe_value(row, "Wide")),
            ("Narrow", _safe_value(row, "Narrow")),
            ("Shutdown", _safe_value(row, "Shut Down")),
            ("Prioridad atención", _safe_value(row, "PRIORIDAD DE ATENCION")),
        ]

        with left_col:
            _render_field_group(left_fields)
        with right_col:
            _render_field_group(right_fields)

    with links_col:
        st.markdown("**Enlaces**")
        _render_link("Config VD", row.get("LINK CONFIG VD"))
        _render_link("DATEM", row.get("LINK DATEM"))
        _render_link("Esquemas", row.get("LINK ESQUEMAS"))
        _render_link("Repositorio", row.get("LINK REPOSITORIO"))
        _render_link("Automático", row.get("LINK AUTOMATICO"))


def _render_field_group(fields: list[tuple[str, str]]) -> None:
    for label, value in fields:
        st.caption(label.upper())
        st.markdown(value)


def _safe_value(row: pd.Series, column: str) -> str:
    value = row.get(column)
    if pd.isna(value):
        return "-"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    text = str(value).strip()
    return text if text else "-"


def _render_link(label: str, value: object) -> None:
    if pd.isna(value) or not str(value).strip():
        st.caption(f"{label}: sin enlace")
        return
    st.markdown(
        """
        <style>
        div[data-testid="stLinkButton"] a {
            font-size: 1rem !important;
            font-weight: 500 !important;
            line-height: 1.3 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.link_button(label, str(value).strip(), use_container_width=True)


def _render_placeholder(message: str | None = None) -> None:
    st.caption("FICHA TÉCNICA")
    st.info(message or "Selecciona un externo en el mapa o en el filtro para activar la ficha.")
