from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from sema_dashboard.repositories.inventario_repository import fetch_inventario
from sema_dashboard.transforms.inventario import build_inventario_dataset

LEFT_FIELDS = [
    ("Direccion corta", "DIRECCION CORTA"),
    ("Localidad", "localidad"),
    ("Zona planeamiento", "ZONA PLANEAMIENTO"),
    ("Zona automatica", "ZONA AUTO"),
    ("Referencia equipo", "REFERENCIA EQUIPO"),
    ("Fecha instalacion", "FECHA DE INSTALACION"),
    ("Operacion actual", "OPERACION ACTUAL"),
]

RIGHT_FIELDS = [
    ("Tipo interseccion", "TIPO DE INTERSECCION"),
    ("Grupos vehiculares", "Grupos Vehiculares"),
    ("Grupos peatonales", "Grupos Peatonales"),
    ("Wide", "Wide"),
    ("Narrow", "Narrow"),
    ("Shutdown", "Shut Down"),
    ("Prioridad atencion", "PRIORIDAD DE ATENCION"),
]

LINK_FIELDS = [
    ("Config VD", "LINK CONFIG VD"),
    ("DATEM", "LINK DATEM"),
    ("Esquemas", "LINK ESQUEMAS"),
    ("Repositorio", "LINK REPOSITORIO"),
    ("Automatico", "LINK AUTOMATICO"),
]


def get_ficha_tecnica_row(externo: str | None) -> tuple[str | None, pd.Series | None]:
    selected = str(externo).strip() if externo is not None else ""
    if not selected:
        return None, None

    inventario_raw = fetch_inventario()
    inventario = build_inventario_dataset(inventario_raw, filters=None)
    ficha = inventario[inventario["externo"].astype(str) == selected].copy()
    if ficha.empty:
        return selected, None
    return selected, ficha.iloc[0]


def render_ficha_tecnica_content(externo: str | None) -> None:
    selected, row = get_ficha_tecnica_row(externo)
    if not selected:
        st.info("Selecciona un externo en el mapa o en el filtro para activar la ficha.")
        return
    if row is None:
        st.warning(f"No se encontro informacion para el externo {selected}.")
        return

    st.subheader(f"Externo {selected}")
    info_col, links_col = st.columns([2.4, 1.0], gap="large")

    with info_col:
        left_col, right_col = st.columns(2, gap="medium")
        with left_col:
            _render_field_group(row, LEFT_FIELDS)
        with right_col:
            _render_field_group(row, RIGHT_FIELDS)

    with links_col:
        st.caption("ENLACES")
        for label, column in LINK_FIELDS:
            _render_link(label, row.get(column))


def _render_field_group(row: pd.Series, fields: list[tuple[str, str]]) -> None:
    for label, column in fields:
        st.caption(label.upper())
        st.write(_safe_value(row.get(column)))


def _safe_value(value: object) -> str:
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
    st.link_button(label, str(value).strip(), use_container_width=True)
