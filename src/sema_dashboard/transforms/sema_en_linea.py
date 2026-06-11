from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from sema_dashboard.services.filter_service import apply_common_filters
from sema_dashboard.transforms.estados import normalize_estados_frame
from sema_dashboard.transforms.inventario import build_inventario_dataset

SEMA_EN_LINEA_COLORS = {
    "Fluido": "#00FF66",
    "Moderado": "#FFD700",
    "Saturado": "#FF3333",
    "Sin datos": "#808080",
}


def _hex_to_rgb(color: str) -> list[int]:
    color = color.lstrip("#")
    return [int(color[index : index + 2], 16) for index in (0, 2, 4)]


def _safe_value(value: object, numeric_format: str | None = None) -> str:
    if pd.isna(value):
        return "-"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%d/%m/%Y %H:%M:%S")
    if numeric_format is not None:
        try:
            return format(float(value), numeric_format)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _resolve_column(df: pd.DataFrame, *candidates: str) -> str | None:
    existing = {str(column).strip().lower(): column for column in df.columns}
    for candidate in candidates:
        match = existing.get(candidate.lower())
        if match is not None:
            return match
    return None


def _classify_ocupacion(value: object) -> str:
    if pd.isna(value):
        return "Sin datos"
    try:
        ocupacion = float(value)
    except (TypeError, ValueError):
        return "Sin datos"
    if ocupacion < 50:
        return "Fluido"
    if ocupacion <= 80:
        return "Moderado"
    return "Saturado"


def _prepare_metadata(estados_df: pd.DataFrame, inventario_df: pd.DataFrame) -> pd.DataFrame:
    estados = normalize_estados_frame(estados_df)
    inventario = build_inventario_dataset(inventario_df, filters=None)

    estados_meta = pd.DataFrame(columns=["ext", "direccion", "zona", "corredor", "nombre", "latitud", "longitud"])
    if not estados.empty:
        estados = estados.copy()
        estados["ext"] = estados["externo"].astype(str).str.strip()
        estados_meta = estados[
            ["ext", "direccion", "zona_auto", "corredor", "equipo", "latitud", "longitud"]
        ].rename(columns={"zona_auto": "zona", "equipo": "nombre"})
        estados_meta = estados_meta.drop_duplicates(subset="ext")

    inventario_meta = pd.DataFrame(columns=["ext", "direccion", "zona", "corredor", "nombre", "latitud", "longitud"])
    if not inventario.empty:
        inventario = inventario.copy()
        inventario["ext"] = inventario["externo"].astype(str).str.strip()
        nombre_column = None
        for candidate in ["NOMBRE", "NOMBRE INTERSECCION", "REFERENCIA EQUIPO", "OPERACION ACTUAL"]:
            if candidate in inventario.columns:
                nombre_column = candidate
                break
        inventario_meta = pd.DataFrame(
            {
                "ext": inventario["ext"],
                "direccion": inventario.get("DIRECCION CORTA"),
                "zona": inventario.get("ZONA AUTO"),
                "corredor": inventario.get("corredor"),
                "nombre": inventario.get(nombre_column) if nombre_column else None,
                "latitud": inventario.get("latitud"),
                "longitud": inventario.get("longitud"),
            }
        ).drop_duplicates(subset="ext")

    metadata = estados_meta.merge(inventario_meta, on="ext", how="outer", suffixes=("", "_inv"))
    for column in ["direccion", "zona", "corredor", "nombre", "latitud", "longitud"]:
        fallback = f"{column}_inv"
        if fallback in metadata.columns:
            metadata[column] = metadata[column].replace("", pd.NA).fillna(metadata[fallback])
            metadata = metadata.drop(columns=[fallback])

    if metadata.empty:
        return pd.DataFrame(columns=["ext", "direccion", "zona", "corredor", "nombre", "latitud", "longitud"])

    metadata["direccion"] = metadata["direccion"].fillna("").astype(str).str.strip()
    metadata["zona"] = metadata["zona"].fillna("").astype(str).str.strip()
    metadata["corredor"] = metadata["corredor"].fillna("").astype(str).str.strip()
    metadata["nombre"] = metadata["nombre"].fillna("").astype(str).str.strip()
    metadata["latitud"] = pd.to_numeric(metadata["latitud"], errors="coerce")
    metadata["longitud"] = pd.to_numeric(metadata["longitud"], errors="coerce")
    return metadata.drop_duplicates(subset="ext")


def _normalize_realtime_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()

    dataset = df.copy()
    dataset.columns = [str(column).strip() for column in dataset.columns]

    ext_column = _resolve_column(dataset, "ext", "est", "externo")
    acceso_column = _resolve_column(dataset, "Acceso", "acceso")
    tiempo_column = _resolve_column(dataset, "Tiempo", "tiempo")
    deteccion_column = _resolve_column(dataset, "Deteccion", "deteccion")
    ocupacion_column = _resolve_column(dataset, "Ocupacion", "ocupacion")
    deteccion_prom_column = _resolve_column(dataset, "Deteccion_prom", "deteccion_prom")
    ocupacion_prom_column = _resolve_column(dataset, "Ocupacion_prom", "ocupacion_prom")
    sensor_column = _resolve_column(dataset, "Sensor", "sensor")

    rename_map = {}
    if ext_column is not None:
        rename_map[ext_column] = "ext"
    if acceso_column is not None:
        rename_map[acceso_column] = "Acceso"
    if tiempo_column is not None:
        rename_map[tiempo_column] = "Tiempo"
    if deteccion_column is not None:
        rename_map[deteccion_column] = "Deteccion"
    if ocupacion_column is not None:
        rename_map[ocupacion_column] = "Ocupacion"
    if deteccion_prom_column is not None:
        rename_map[deteccion_prom_column] = "Deteccion_prom"
    if ocupacion_prom_column is not None:
        rename_map[ocupacion_prom_column] = "Ocupacion_prom"
    if sensor_column is not None:
        rename_map[sensor_column] = "Sensor"

    dataset = dataset.rename(columns=rename_map)

    required_columns = ["ext", "Acceso", "Tiempo", "Deteccion", "Ocupacion"]
    missing = [column for column in required_columns if column not in dataset.columns]
    if missing:
        raise KeyError(missing[0])

    for column in ["Deteccion_prom", "Ocupacion_prom", "Sensor"]:
        if column not in dataset.columns:
            dataset[column] = None

    dataset["ext"] = dataset["ext"].astype(str).str.strip()
    dataset["Acceso"] = dataset["Acceso"].astype(str).str.strip()
    dataset["Tiempo"] = pd.to_datetime(dataset["Tiempo"], errors="coerce")
    dataset["Deteccion"] = pd.to_numeric(dataset["Deteccion"], errors="coerce")
    dataset["Ocupacion"] = pd.to_numeric(dataset["Ocupacion"], errors="coerce")
    dataset["Deteccion_prom"] = pd.to_numeric(dataset["Deteccion_prom"], errors="coerce")
    dataset["Ocupacion_prom"] = pd.to_numeric(dataset["Ocupacion_prom"], errors="coerce")
    dataset["fecha_dia"] = dataset["Tiempo"].dt.date
    return dataset.dropna(subset=["Tiempo", "ext"]).copy()


def build_sema_en_linea_payload(
    realtime_df: pd.DataFrame,
    estados_df: pd.DataFrame,
    inventario_df: pd.DataFrame,
    filters: dict | None = None,
) -> dict[str, object]:
    empty_map = pd.DataFrame(
        columns=[
            "ext",
            "direccion",
            "zona",
            "nombre",
            "latitud",
            "longitud",
            "detecciones_total",
            "ultima_deteccion",
            "ultima_ocupacion",
            "ultimo_tiempo",
            "categoria",
            "color_hex",
            "color_rgb",
            "radius_value",
            "tooltip_html",
        ]
    )
    empty_series = pd.DataFrame(
        columns=["Tiempo", "ext", "Acceso", "Deteccion", "Ocupacion", "Deteccion_prom", "Ocupacion_prom", "Sensor"]
    )
    payload = {
        "map_dataset": empty_map,
        "series_dataset": empty_series,
        "summary": {
            "updated_at": None,
            "externos_activos": 0,
            "detecciones_dia": 0.0,
            "detecciones_ultimo_periodo": 0.0,
            "ocupacion_promedio_dia": 0.0,
            "ocupacion_promedio_ultimo_periodo": 0.0,
        },
    }

    if realtime_df.empty:
        return payload

    realtime = _normalize_realtime_frame(realtime_df)
    metadata = _prepare_metadata(estados_df, inventario_df)
    realtime = realtime.merge(metadata, on="ext", how="left")

    if filters:
        realtime = apply_common_filters(realtime, filters)
    if realtime.empty:
        return payload

    realtime = realtime.dropna(subset=["latitud", "longitud"]).copy()
    if realtime.empty:
        return payload

    realtime["latitud"] = pd.to_numeric(realtime["latitud"], errors="coerce")
    realtime["longitud"] = pd.to_numeric(realtime["longitud"], errors="coerce")
    realtime = realtime.dropna(subset=["latitud", "longitud"]).copy()
    if realtime.empty:
        return payload

    detecciones_total = (
        realtime.groupby("ext", as_index=False)["Deteccion"]
        .sum()
        .rename(columns={"Deteccion": "detecciones_total"})
    )

    ultimo_tiempo_por_ext = realtime.groupby("ext", as_index=False)["Tiempo"].max().rename(
        columns={"Tiempo": "ultimo_tiempo"}
    )
    ultimos = realtime.merge(ultimo_tiempo_por_ext, on="ext", how="inner")
    ultimos = ultimos[ultimos["Tiempo"] == ultimos["ultimo_tiempo"]].copy()

    ultimos_agregados = (
        ultimos.groupby("ext", as_index=False)
        .agg(
            ultima_deteccion=("Deteccion", "sum"),
            ultima_ocupacion=("Ocupacion", "mean"),
            ultimo_tiempo=("ultimo_tiempo", "max"),
            Acceso=("Acceso", "nunique"),
            Sensor=("Sensor", "nunique"),
            direccion=("direccion", "first"),
            zona=("zona", "first"),
            nombre=("nombre", "first"),
            latitud=("latitud", "first"),
            longitud=("longitud", "first"),
        )
    )

    mapa = ultimos_agregados.merge(detecciones_total, on="ext", how="left")
    mapa["categoria"] = mapa["ultima_ocupacion"].apply(_classify_ocupacion)
    mapa["color_hex"] = mapa["categoria"].map(SEMA_EN_LINEA_COLORS).fillna("#808080")
    mapa["color_rgb"] = mapa["color_hex"].map(_hex_to_rgb)

    scale = mapa["detecciones_total"].clip(lower=0).fillna(0)
    if len(scale) > 1 and scale.max() > scale.min():
        mapa["radius_value"] = np.interp(
            scale,
            (float(scale.min()), float(scale.max())),
            (18.0, 420.0),
        )
    else:
        mapa["radius_value"] = 90.0

    mapa["tooltip_html"] = mapa.apply(
        lambda row: (
            "<b>Externo:</b> "
            f"{_safe_value(row['ext'])}<br>"
            "<b>Direccion:</b> "
            f"{_safe_value(row['direccion'])}<br>"
            "<b>Zona:</b> "
            f"{_safe_value(row['zona'])}<br>"
            "<b>Nombre:</b> "
            f"{_safe_value(row['nombre'])}<br><br>"
            "<b>Ultima deteccion:</b> "
            f"{_safe_value(row['ultima_deteccion'], ',.0f')}<br>"
            "<b>Ultima ocupacion:</b> "
            f"{_safe_value(row['ultima_ocupacion'], '.1f')}%<br>"
            "<b>Estado:</b> "
            f"{_safe_value(row['categoria'])}<br>"
            "<b>Tiempo:</b> "
            f"{_safe_value(row['ultimo_tiempo'])}"
        ),
        axis=1,
    )

    updated_at = pd.to_datetime(realtime["Tiempo"], errors="coerce").max()
    latest_global = updated_at
    today = date.today()

    ultimo_periodo = realtime[realtime["Tiempo"] == latest_global].copy() if pd.notna(latest_global) else realtime.iloc[0:0].copy()
    externos_activos = 0
    if not ultimo_periodo.empty:
        externos_activos = int(
            (
                ultimo_periodo.groupby("ext", as_index=False)["Deteccion"]
                .sum()["Deteccion"]
                .fillna(0)
                .gt(0)
                .sum()
            )
        )

    hoy = realtime[realtime["fecha_dia"] == today].copy()
    payload["map_dataset"] = mapa.drop_duplicates(subset="ext").reset_index(drop=True)
    payload["series_dataset"] = realtime.reset_index(drop=True)
    payload["summary"] = {
        "updated_at": updated_at,
        "externos_activos": externos_activos,
        "detecciones_dia": float(hoy["Deteccion"].fillna(0).sum()) if not hoy.empty else 0.0,
        "detecciones_ultimo_periodo": float(ultimo_periodo["Deteccion"].fillna(0).sum()) if not ultimo_periodo.empty else 0.0,
        "ocupacion_promedio_dia": float(hoy["Ocupacion"].dropna().mean()) if not hoy.empty and hoy["Ocupacion"].notna().any() else 0.0,
        "ocupacion_promedio_ultimo_periodo": float(ultimo_periodo["Ocupacion"].dropna().mean()) if not ultimo_periodo.empty and ultimo_periodo["Ocupacion"].notna().any() else 0.0,
    }
    return payload
