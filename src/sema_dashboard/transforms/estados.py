from __future__ import annotations

import pandas as pd

from sema_dashboard.services.filter_service import apply_common_filters

ESTADO_COLORS = {
    "Operando": "#18FF1C",
    "Operacion sin conexion": "#12B8FF",
    "En falla": "#6D2C1E",
    "Equipo en Error": "#17D7C0",
}


def _hex_to_rgb(color: str) -> list[int]:
    color = color.lstrip("#")
    return [int(color[i : i + 2], 16) for i in (0, 2, 4)]


def _safe_value(value: object) -> str:
    if pd.isna(value):
        return "-"
    return str(value)


def _normalize_estado(value: object) -> str | None:
    if pd.isna(value):
        return None

    raw = str(value).strip()
    normalized = raw.casefold()

    if normalized in {"operando", "en servicio"}:
        return "Operando"
    if normalized in {
        "operacion sin conexion",
        "operando sin conexion",
        "sin conexion etb",
        "offline",
        "note",
    }:
        return "Operacion sin conexion"
    if normalized in {"en falla", "alarm"}:
        return "En falla"
    if normalized in {"equipo en error", "error"}:
        return "Equipo en Error"
    return None


def normalize_estados_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    dataset = df.copy()
    dataset.columns = [str(column).strip() for column in dataset.columns]

    alias_map = {
        "EXTERNO": "externo",
        "DIRECCION CORTA": "direccion",
        "DIRECCION": "direccion",
        "ZONA AUTO": "zona_auto",
        "LOCALIDAD": "localidad",
        "REFERENCIA EQUIPO": "equipo",
        "EQUIPO": "equipo",
        "OPERACION ACTUAL": "operacion",
        "OPERACION": "operacion",
        "Wide": "num_wide",
        "NUM_WIDE": "num_wide",
        "Narrow": "num_narrow",
        "NUM_NARROW": "num_narrow",
        "Shut Down": "shutdown",
        "SHUTDOWN": "shutdown",
        "LONGITUD": "longitud",
        "LATITUD": "latitud",
        "ESTADO": "estado",
        "FECHA": "fecha",
        "CAUSA": "causa",
        "ID_DE_SOLICITUD": "id_de_solicitud",
        "ESTADO_DE_LA_INTERSECCION": "estado_de_la_interseccion",
        "TIEMPO_TRANSCURRIDO": "tiempo_transcurrido",
    }
    for source_column, target_column in alias_map.items():
        if source_column in dataset.columns and target_column not in dataset.columns:
            dataset[target_column] = dataset[source_column]

    for column in [
        "externo",
        "direccion",
        "zona_auto",
        "localidad",
        "equipo",
        "shutdown",
        "estado",
        "operacion",
        "num_wide",
        "num_narrow",
        "longitud",
        "latitud",
        "fecha",
        "causa",
        "id_de_solicitud",
        "estado_de_la_interseccion",
        "tiempo_transcurrido",
    ]:
        if column not in dataset.columns:
            dataset[column] = None

    dataset["externo"] = dataset["externo"].astype(str)
    dataset["longitud"] = pd.to_numeric(dataset["longitud"], errors="coerce")
    dataset["latitud"] = pd.to_numeric(dataset["latitud"], errors="coerce")
    dataset["fecha"] = pd.to_datetime(dataset["fecha"], errors="coerce")
    return dataset


def build_estados_dataset(df: pd.DataFrame, filters: dict | None = None) -> pd.DataFrame:
    dataset = normalize_estados_frame(df)
    if dataset.empty:
        return dataset

    dataset = dataset.dropna(subset=["latitud", "longitud"]).copy()
    if filters:
        dataset = apply_common_filters(dataset, filters)

    dataset["estado"] = dataset["estado"].apply(_normalize_estado)
    dataset = dataset[dataset["estado"].notna()].copy()
    if dataset.empty:
        return dataset

    dataset["color_hex"] = dataset["estado"].map(ESTADO_COLORS).fillna("#808080")
    dataset["color_rgb"] = dataset["color_hex"].map(_hex_to_rgb)
    dataset["radius_value"] = 90
    dataset["tooltip_html"] = dataset.apply(
        lambda row: (
            "<b>Externo:</b> "
            f"{_safe_value(row['externo'])}<br>"
            "<b>Direccion:</b> "
            f"{_safe_value(row['direccion'])}<br>"
            "<b>Zona:</b> "
            f"{_safe_value(row['zona_auto'])}<br>"
            "<b>Localidad:</b> "
            f"{_safe_value(row['localidad'])}<br>"
            "<b>Equipo:</b> "
            f"{_safe_value(row['equipo'])}<br>"
            "<b>Shutdown:</b> "
            f"{_safe_value(row['shutdown'])}<br>"
            "<b>Estado:</b> "
            f"{_safe_value(row['estado'])}<br>"
            "<b>Operacion:</b> "
            f"{_safe_value(row['operacion'])}<br>"
            "<b>Wide:</b> "
            f"{_safe_value(row['num_wide'])}<br>"
            "<b>Narrow:</b> "
            f"{_safe_value(row['num_narrow'])}"
        ),
        axis=1,
    )
    return dataset
