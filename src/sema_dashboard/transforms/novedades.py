from __future__ import annotations

import numpy as np
import pandas as pd

from sema_dashboard.services.filter_service import apply_common_filters
from sema_dashboard.transforms.estados import normalize_estados_frame

NOVEDAD_COLORS = {
    "EN SERVICIO": "#00FF66",
    "AISLADA": "#29B6F6",
    "APAGADA": "#C44E7A",
    "INTERMITENTE": "#9BBB59",
    "MANTENIMIENTO": "#FF8C00",
    "EN PMT": "#0D3B66",
}


def _hex_to_rgb(color: str) -> list[int]:
    color = color.lstrip("#")
    return [int(color[i : i + 2], 16) for i in (0, 2, 4)]


def _safe_value(value: object) -> str:
    if pd.isna(value):
        return "-"
    return str(value)


def _normalize_estado_interseccion(value: object) -> str:
    if pd.isna(value):
        return "EN SERVICIO"
    normalized = str(value).strip().upper()
    aliases = {
        "EN SERVICIO": "EN SERVICIO",
        "OPERANDO": "EN SERVICIO",
        "FUERA DE SERVICIO": "AISLADA",
        "AISLADA": "AISLADA",
        "AISLADO": "AISLADA",
        "DESTELLO": "INTERMITENTE",
        "INTERMITENTE": "INTERMITENTE",
        "MANTENIMIENTO": "MANTENIMIENTO",
        "APAGADO": "APAGADA",
        "APAGADA": "APAGADA",
        "EN PMT": "EN PMT",
    }
    return aliases.get(normalized, "EN SERVICIO")


def _contains_pmt(value: object) -> bool:
    if pd.isna(value):
        return False
    return "PMT" in str(value).upper()


def build_novedades_dataset(df: pd.DataFrame, filters: dict | None = None) -> pd.DataFrame:
    dataset = normalize_estados_frame(df)
    if dataset.empty:
        return dataset

    dataset = dataset.dropna(subset=["latitud", "longitud"]).copy()
    incident_mask = dataset["id_de_solicitud"].notna() & (dataset["id_de_solicitud"].astype(str).str.strip() != "")
    dataset["incidente_activo"] = np.where(incident_mask, "SI", "NO")
    raw_estado_interseccion = dataset["estado_de_la_interseccion"].copy()
    pmt_mask = raw_estado_interseccion.apply(_contains_pmt) | dataset["causa"].apply(_contains_pmt)
    dataset["estado_de_la_interseccion"] = np.where(
        pmt_mask,
        "EN PMT",
        raw_estado_interseccion.fillna("EN SERVICIO").apply(_normalize_estado_interseccion),
    )
    if filters:
        dataset = apply_common_filters(dataset, filters)
    dataset = dataset[dataset["estado_de_la_interseccion"].isin(NOVEDAD_COLORS)].copy()
    if dataset.empty:
        return dataset

    dataset["color_hex"] = dataset["estado_de_la_interseccion"].map(NOVEDAD_COLORS).fillna("#808080")
    dataset["color_rgb"] = dataset["color_hex"].map(_hex_to_rgb)
    dataset["radius_value"] = np.where(
        dataset["estado_de_la_interseccion"] == "EN SERVICIO",
        15,
        300,
    )
    dataset["tooltip_html"] = dataset.apply(
        lambda row: (
            "<b>Externo:</b> "
            f"{_safe_value(row['externo'])}<br>"
            "<b>Direccion:</b> "
            f"{_safe_value(row['direccion'])}<br><br>"
            "<b>Estado:</b> "
            f"{_safe_value(row['estado_de_la_interseccion'])}<br><br>"
            "<b>Fecha:</b> "
            f"{_safe_value(row['fecha'])}<br>"
            "<b>Causa:</b> "
            f"{_safe_value(row['causa'])}<br>"
            "<b>Solicitud:</b> "
            f"{_safe_value(row['id_de_solicitud'])}<br>"
            "<b>Tiempo:</b> "
            f"{_safe_value(row['tiempo_transcurrido'])}"
        ),
        axis=1,
    )
    return dataset
