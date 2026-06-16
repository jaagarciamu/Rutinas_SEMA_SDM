from __future__ import annotations

from datetime import datetime

import pandas as pd

from sema_dashboard.services.filter_service import apply_common_filters

PALETTE = [
    "#00D4FF",
    "#72FFA0",
    "#FFD447",
    "#FF8C42",
    "#FF5A5A",
    "#B388FF",
]


def _hex_to_rgb(color: str) -> list[int]:
    color = color.lstrip("#")
    return [int(color[i : i + 2], 16) for i in (0, 2, 4)]


def _safe_value(value: object) -> str:
    if pd.isna(value):
        return "-"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    return str(value)


def build_inventario_dataset(df: pd.DataFrame, filters: dict | None = None) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    dataset = df.copy()
    dataset.columns = [str(column).strip() for column in dataset.columns]

    alias_map = {
        "EXTERNO": "externo",
        "DIRECCION CORTA": "direccion",
        "LOCALIDAD": "localidad",
        "LONGITUD": "longitud",
        "LATITUD": "latitud",
        "FUNCIONAMIENTO": "funcionamiento",
        "OPERACION ACTUAL": "OPERACION ACTUAL",
        "CORREDOR": "corredor",
    }
    for source_column, target_column in alias_map.items():
        if source_column in dataset.columns and target_column not in dataset.columns:
            dataset[target_column] = dataset[source_column]

    for column in ["latitud", "longitud", "externo", "ZONA AUTO", "corredor"]:
        if column not in dataset.columns:
            dataset[column] = None

    dataset = dataset.dropna(subset=["latitud", "longitud"]).copy()
    dataset["latitud"] = pd.to_numeric(dataset["latitud"], errors="coerce")
    dataset["longitud"] = pd.to_numeric(dataset["longitud"], errors="coerce")
    dataset = dataset.dropna(subset=["latitud", "longitud"]).copy()
    dataset["externo"] = dataset["externo"].astype(str)
    dataset["ZONA AUTO"] = dataset["ZONA AUTO"].fillna("Sin zona").astype(str)
    dataset["corredor"] = dataset["corredor"].fillna("").astype(str).str.strip()

    if "FECHA DE INSTALACION" in dataset.columns:
        dataset["FECHA DE INSTALACION"] = pd.to_datetime(
            dataset["FECHA DE INSTALACION"],
            errors="coerce",
        )

    if filters:
        dataset = apply_common_filters(dataset, filters)

    zonas = sorted(dataset["ZONA AUTO"].astype(str).unique())
    color_map = {zona: PALETTE[index % len(PALETTE)] for index, zona in enumerate(zonas)}
    dataset["color_hex"] = dataset["ZONA AUTO"].map(color_map)
    dataset["color_rgb"] = dataset["color_hex"].map(_hex_to_rgb)

    hover_columns = [
        "externo",
        "DIRECCION CORTA",
        "localidad",
        "corredor",
        "ZONA PLANEAMIENTO",
        "REFERENCIA EQUIPO",
        "# INTERSECCIONES POR EQUIPO",
        "FECHA DE INSTALACION",
        "ZONA AUTO",
        "funcionamiento",
        "TIPO DE INTERSECCION",
        "Grupos Vehiculares",
        "Grupos Peatonales",
        "Wide",
        "Grupos Wide",
        "Narrow",
        "grupos Narrow",
        "Shut Down",
        "LINK CONFIG VD",
        "LINK DATEM",
        "LINK REPOSITORIO",
        "LINK ESQUEMAS",
        "LINK AUTOMATICO",
        "PRIORIDAD DE ATENCION",
    ]

    for column in hover_columns:
        if column not in dataset.columns:
            dataset[column] = None

    dataset["tooltip_html"] = dataset.apply(
        lambda row: (
            "<b>EXTERNO:</b> "
            f"{_safe_value(row['externo'])}<br><br>"
            "<b>DIRECCION:</b> "
            f"{_safe_value(row['DIRECCION CORTA'])}<br>"
            "<b>LOCALIDAD:</b> "
            f"{_safe_value(row['localidad'])}<br>"
            "<b>CORREDOR:</b> "
            f"{_safe_value(row['corredor'])}<br>"
            "<b>ZONA PLANEAMIENTO:</b> "
            f"{_safe_value(row['ZONA PLANEAMIENTO'])}<br>"
            "<b>ZONA AUTO:</b> "
            f"{_safe_value(row['ZONA AUTO'])}<br><br>"
            "<b>EQUIPO:</b> "
            f"{_safe_value(row['REFERENCIA EQUIPO'])}<br>"
            "<b>INTERSECCIONES EQUIPO:</b> "
            f"{_safe_value(row['# INTERSECCIONES POR EQUIPO'])}<br>"
            "<b>INSTALACION:</b> "
            f"{_safe_value(row['FECHA DE INSTALACION'])}<br><br>"
            "<b>FUNCIONAMIENTO:</b> "
            f"{_safe_value(row['funcionamiento'])}<br>"
            "<b>TIPO INTERSECCION:</b> "
            f"{_safe_value(row['TIPO DE INTERSECCION'])}<br><br>"
            "<b>GRUPOS VEHICULARES:</b> "
            f"{_safe_value(row['Grupos Vehiculares'])}<br>"
            "<b>GRUPOS PEATONALES:</b> "
            f"{_safe_value(row['Grupos Peatonales'])}<br><br>"
            "<b>WIDE:</b> "
            f"{_safe_value(row['Wide'])}<br>"
            "<b>GRUPOS WIDE:</b> "
            f"{_safe_value(row['Grupos Wide'])}<br><br>"
            "<b>NARROW:</b> "
            f"{_safe_value(row['Narrow'])}<br>"
            "<b>GRUPOS NARROW:</b> "
            f"{_safe_value(row['grupos Narrow'])}<br><br>"
            "<b>SHUTDOWN:</b> "
            f"{_safe_value(row['Shut Down'])}<br>"
            "<b>PRIORIDAD:</b> "
            f"{_safe_value(row['PRIORIDAD DE ATENCION'])}"
        ),
        axis=1,
    )

    return dataset
