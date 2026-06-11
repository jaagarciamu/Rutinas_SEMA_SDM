from __future__ import annotations

import numpy as np
import pandas as pd

from sema_dashboard.services.filter_service import apply_common_filters
from sema_dashboard.transforms.estados import normalize_estados_frame

DETECCION_COLORS = {
    "Sin datos": "#808080",
    "Fluido": "#00FF66",
    "Saturado": "#FFD700",
    "Congestionado": "#FF3333",
}


def _hex_to_rgb(color: str) -> list[int]:
    color = color.lstrip("#")
    return [int(color[i : i + 2], 16) for i in (0, 2, 4)]


def _safe_value(value: object, numeric_format: str | None = None) -> str:
    if pd.isna(value):
        return "-"
    if numeric_format is not None:
        try:
            return format(float(value), numeric_format)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _classify_ocupacion(value: object) -> str:
    if pd.isna(value):
        return "Sin datos"
    if value <= 5:
        return "Sin datos"
    if value < 50:
        return "Fluido"
    if value < 85:
        return "Saturado"
    return "Congestionado"


def _resolve_column(df: pd.DataFrame, *candidates: str) -> str | None:
    existing = {str(column).strip().lower(): column for column in df.columns}
    for candidate in candidates:
        match = existing.get(candidate.lower())
        if match is not None:
            return match
    return None


def build_detecciones_dataset(
    detecciones_df: pd.DataFrame,
    estados_df: pd.DataFrame,
    filters: dict | None = None,
) -> pd.DataFrame:
    if detecciones_df.empty:
        return detecciones_df.copy()

    detecciones = detecciones_df.copy()
    detecciones.columns = [str(column).strip() for column in detecciones.columns]
    ext_column = _resolve_column(detecciones, "ext", "est", "EXT", "EST")
    acceso_column = _resolve_column(detecciones, "Acceso", "acceso")
    deteccion_column = _resolve_column(detecciones, "Deteccion", "deteccion")
    ocupacion_column = _resolve_column(detecciones, "Ocupacion", "ocupacion")
    fecha_column = _resolve_column(detecciones, "Fecha", "fecha")
    tiempo_column = _resolve_column(detecciones, "Tiempo", "tiempo")

    if ext_column is None:
        raise KeyError("ext")
    if acceso_column is None:
        raise KeyError("Acceso")

    rename_map = {ext_column: "ext"}
    rename_map[acceso_column] = "Acceso"
    if deteccion_column is not None:
        rename_map[deteccion_column] = "Deteccion"
    if ocupacion_column is not None:
        rename_map[ocupacion_column] = "Ocupacion"
    if fecha_column is not None:
        rename_map[fecha_column] = "Fecha"
    if tiempo_column is not None:
        rename_map[tiempo_column] = "Tiempo"
    detecciones = detecciones.rename(columns=rename_map)

    if "Tiempo" in detecciones.columns:
        detecciones["Tiempo"] = pd.to_datetime(detecciones["Tiempo"], errors="coerce")
    if "Fecha" not in detecciones.columns and "Tiempo" in detecciones.columns:
        detecciones["Fecha"] = detecciones["Tiempo"].dt.date
    detecciones["ext"] = detecciones["ext"].astype(str)
    detecciones["Acceso"] = detecciones["Acceso"].astype(str)

    if filters:
        detecciones = apply_common_filters(detecciones, filters)
    if detecciones.empty:
        return detecciones

    if "Deteccion" in detecciones.columns:
        detecciones["Deteccion"] = pd.to_numeric(detecciones["Deteccion"], errors="coerce")
    if "Ocupacion" in detecciones.columns:
        detecciones["Ocupacion"] = pd.to_numeric(detecciones["Ocupacion"], errors="coerce")

    ocup_ext = detecciones.groupby("ext", as_index=False).agg(Ocupacion=("Ocupacion", "mean"))
    det_ext = detecciones.groupby("ext", as_index=False).agg(Detecciones=("Deteccion", "sum"))
    mapa_det = det_ext.merge(ocup_ext, on="ext", how="left")
    fecha_min = pd.to_datetime(detecciones["Tiempo"], errors="coerce").min() if "Tiempo" in detecciones.columns else pd.NaT
    fecha_max = pd.to_datetime(detecciones["Tiempo"], errors="coerce").max() if "Tiempo" in detecciones.columns else pd.NaT
    mapa_det["FechaInicio"] = fecha_min
    mapa_det["FechaFin"] = fecha_max

    estados = normalize_estados_frame(estados_df)
    if states_filters := filters:
        estados = apply_common_filters(estados, states_filters)
    estados = estados[
        ["externo", "direccion", "localidad", "zona_auto", "corredor", "equipo", "operacion", "longitud", "latitud"]
    ].drop_duplicates(subset="externo")
    estados["externo"] = estados["externo"].astype(str)

    mapa_det["ext"] = mapa_det["ext"].astype(str)
    mapa_det = mapa_det.merge(estados, left_on="ext", right_on="externo", how="left")
    if filters:
        mapa_det = apply_common_filters(mapa_det, filters)
    if mapa_det.empty:
        return mapa_det
    mapa_det = mapa_det.dropna(subset=["latitud", "longitud"]).copy()
    mapa_det["categoria"] = mapa_det["Ocupacion"].apply(_classify_ocupacion)
    mapa_det["color_hex"] = mapa_det["categoria"].map(DETECCION_COLORS).fillna("#808080")
    mapa_det["color_rgb"] = mapa_det["color_hex"].map(_hex_to_rgb)
    detecciones_scale = mapa_det["Detecciones"].clip(lower=0).fillna(0)
    if len(detecciones_scale) > 1 and detecciones_scale.max() > detecciones_scale.min():
        mapa_det["radius_value"] = np.interp(
            detecciones_scale,
            (float(detecciones_scale.min()), float(detecciones_scale.max())),
            (18.0, 420.0),
        )
    else:
        mapa_det["radius_value"] = 60.0
    mapa_det["tooltip_html"] = mapa_det.apply(
        lambda row: (
            "<b>Externo:</b> "
            f"{_safe_value(row['ext'])}<br>"
            "<b>Direccion:</b> "
            f"{_safe_value(row['direccion'])}<br>"
            "<b>Localidad:</b> "
            f"{_safe_value(row['localidad'])}<br><br>"
            "<b>Detecciones:</b> "
            f"{_safe_value(row['Detecciones'], ',.0f')}<br>"
            "<b>Ocupacion:</b> "
            f"{_safe_value(row['Ocupacion'], '.1f')}%<br>"
            "<b>Estado:</b> "
            f"{_safe_value(row['categoria'])}<br><br>"
            "<b>Periodo:</b> "
            f"{_safe_value(row['FechaInicio'])} a {_safe_value(row['FechaFin'])}<br><br>"
            "<b>Equipo:</b> "
            f"{_safe_value(row['equipo'])}<br>"
            "<b>Operacion:</b> "
            f"{_safe_value(row['operacion'])}"
        ),
        axis=1,
    )
    return mapa_det
