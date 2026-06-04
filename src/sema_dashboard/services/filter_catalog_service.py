from __future__ import annotations

import pandas as pd

from sema_dashboard.repositories.detecciones_repository import fetch_detecciones
from sema_dashboard.repositories.estados_repository import fetch_estados
from sema_dashboard.repositories.inventario_repository import fetch_inventario
from sema_dashboard.transforms.detecciones import _resolve_column as resolve_det_column
from sema_dashboard.transforms.estados import normalize_estados_frame
from sema_dashboard.transforms.inventario import build_inventario_dataset


EMPTY_OPTION = ""


def _sorted_unique(values: pd.Series) -> list[str]:
    cleaned = (
        values.dropna()
        .astype(str)
        .str.strip()
    )
    cleaned = cleaned[cleaned != ""]
    return sorted(cleaned.unique().tolist(), key=lambda item: (len(item), item))


def _prepare_detecciones_catalog(fecha_inicio=None, fecha_fin=None) -> pd.DataFrame:
    df = fetch_detecciones(fecha_inicio, fecha_fin).copy()
    if df.empty:
        return df

    ext_column = resolve_det_column(df, "ext", "est", "EXT", "EST")
    acceso_column = resolve_det_column(df, "Acceso", "acceso")
    sensor_column = resolve_det_column(df, "Sensor", "sensor")

    rename_map = {}
    if ext_column is not None:
        rename_map[ext_column] = "ext"
    if acceso_column is not None:
        rename_map[acceso_column] = "Acceso"
    if sensor_column is not None:
        rename_map[sensor_column] = "Sensor"
    df = df.rename(columns=rename_map)

    for column in ["ext", "Acceso", "Sensor"]:
        if column not in df.columns:
            df[column] = None

    df["ext"] = df["ext"].astype(str)
    df["Acceso"] = df["Acceso"].astype(str)
    df["Sensor"] = df["Sensor"].astype(str)
    return df[["ext", "Acceso", "Sensor"]].copy()


def _prepare_spatial_catalog() -> tuple[pd.DataFrame, pd.DataFrame]:
    inventario = build_inventario_dataset(fetch_inventario(), filters=None)
    estados = normalize_estados_frame(fetch_estados())

    if inventario.empty:
        inventario = pd.DataFrame(columns=["externo", "ZONA AUTO"])
    else:
        inventario = inventario.rename(columns={"ZONA AUTO": "zona_auto_catalog"})
        inventario["externo"] = inventario["externo"].astype(str)
        inventario = inventario[["externo", "zona_auto_catalog"]].drop_duplicates()

    if estados.empty:
        estados = pd.DataFrame(columns=["externo", "zona_auto"])
    else:
        estados["externo"] = estados["externo"].astype(str)
        estados = estados[["externo", "zona_auto"]].drop_duplicates()

    return inventario, estados


def _apply_catalog_filters(
    detecciones: pd.DataFrame,
    inventario: pd.DataFrame,
    estados: pd.DataFrame,
    filters: dict,
    *,
    exclude: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    externo = filters.get("externo") if exclude != "externo" else ""
    acceso = filters.get("acceso") if exclude != "acceso" else ""
    sensor = filters.get("sensor") if exclude != "sensor" else ""
    zona_auto = filters.get("zona_auto") if exclude != "zona_auto" else ""

    det = detecciones.copy()
    inv = inventario.copy()
    est = estados.copy()

    if externo:
        det = det[det["ext"].astype(str) == str(externo)]
        if "externo" in inv.columns:
            inv = inv[inv["externo"].astype(str) == str(externo)]
        if "externo" in est.columns:
            est = est[est["externo"].astype(str) == str(externo)]

    if acceso:
        det = det[det["Acceso"].astype(str) == str(acceso)]

    if sensor:
        det = det[det["Sensor"].astype(str) == str(sensor)]

    if zona_auto:
        if "zona_auto_catalog" in inv.columns:
            inv = inv[inv["zona_auto_catalog"].astype(str) == str(zona_auto)]
        if "zona_auto" in est.columns:
            est = est[est["zona_auto"].astype(str) == str(zona_auto)]
        externos_validos = set(inv.get("externo", pd.Series(dtype=str)).astype(str)) | set(
            est.get("externo", pd.Series(dtype=str)).astype(str)
        )
        if externos_validos:
            det = det[det["ext"].astype(str).isin(externos_validos)]

    return det, inv, est


def get_filter_options(filters: dict) -> dict[str, list[str]]:
    fecha_inicio = filters.get("fecha_inicio")
    fecha_fin = filters.get("fecha_fin")
    detecciones = _prepare_detecciones_catalog(fecha_inicio, fecha_fin)
    inventario, estados = _prepare_spatial_catalog()

    det_for_externo, inv_for_externo, est_for_externo = _apply_catalog_filters(
        detecciones, inventario, estados, filters, exclude="externo"
    )
    externo_options = sorted(
        set(_sorted_unique(inv_for_externo.get("externo", pd.Series(dtype=str))))
        | set(_sorted_unique(est_for_externo.get("externo", pd.Series(dtype=str))))
        | set(_sorted_unique(det_for_externo.get("ext", pd.Series(dtype=str))))
    )

    det_for_acceso, _, _ = _apply_catalog_filters(detecciones, inventario, estados, filters, exclude="acceso")
    acceso_options = _sorted_unique(det_for_acceso.get("Acceso", pd.Series(dtype=str)))

    det_for_sensor, _, _ = _apply_catalog_filters(detecciones, inventario, estados, filters, exclude="sensor")
    sensor_options = _sorted_unique(det_for_sensor.get("Sensor", pd.Series(dtype=str)))

    _, inv_for_zona, est_for_zona = _apply_catalog_filters(detecciones, inventario, estados, filters, exclude="zona_auto")
    zona_options = sorted(
        set(_sorted_unique(inv_for_zona.get("zona_auto_catalog", pd.Series(dtype=str))))
        | set(_sorted_unique(est_for_zona.get("zona_auto", pd.Series(dtype=str))))
    )

    return {
        "externo": [EMPTY_OPTION, *externo_options],
        "acceso": [EMPTY_OPTION, *acceso_options],
        "sensor": [EMPTY_OPTION, *sensor_options],
        "zona_auto": [EMPTY_OPTION, *zona_options],
    }


def coerce_filters_to_available_options(filters: dict, options: dict[str, list[str]]) -> dict:
    normalized = dict(filters)
    for key in ["externo", "acceso", "sensor", "zona_auto"]:
        if normalized.get(key, "") not in options.get(key, [EMPTY_OPTION]):
            normalized[key] = EMPTY_OPTION
    return normalized
