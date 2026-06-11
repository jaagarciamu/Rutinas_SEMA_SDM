from __future__ import annotations

import pandas as pd

from sema_dashboard.repositories.detecciones_repository import fetch_detecciones_catalog
from sema_dashboard.repositories.estados_repository import fetch_estados
from sema_dashboard.repositories.inventario_repository import fetch_inventario
from sema_dashboard.repositories.sema_en_linea_repository import fetch_sema_en_linea_catalog
from sema_dashboard.transforms.estados import _normalize_estado, normalize_estados_frame
from sema_dashboard.transforms.inventario import build_inventario_dataset
from sema_dashboard.transforms.novedades import _normalize_estado_interseccion


EMPTY_OPTION = ""


def _sorted_unique(values: pd.Series) -> list[str]:
    cleaned = values.dropna().astype(str).str.strip()
    cleaned = cleaned[cleaned != ""]
    return sorted(cleaned.unique().tolist())


def _valid_corredor_values(values: pd.Series) -> list[str]:
    cleaned = values.dropna().astype(str).str.strip()
    cleaned = cleaned[(cleaned != "") & (cleaned.str.casefold() != "no")]
    return sorted(cleaned.unique().tolist())


def _prepare_detecciones_catalog(fecha_inicio=None, fecha_fin=None, externo: str | None = None) -> pd.DataFrame:
    df = fetch_detecciones_catalog(fecha_inicio, fecha_fin, externo).copy()
    if df.empty:
        return pd.DataFrame(columns=["externo", "acceso"])

    df.columns = [str(column).strip() for column in df.columns]
    rename_map = {}
    for column in df.columns:
        lowered = column.lower()
        if lowered in {"ext", "est"}:
            rename_map[column] = "externo"
        elif lowered == "acceso":
            rename_map[column] = "acceso"
    df = df.rename(columns=rename_map)

    for column in ["externo", "acceso"]:
        if column not in df.columns:
            df[column] = None

    df["externo"] = df["externo"].astype(str).str.strip()
    df["acceso"] = df["acceso"].astype(str).str.strip()
    return df[["externo", "acceso"]].drop_duplicates().copy()


def _prepare_inventario_catalog() -> pd.DataFrame:
    inventario = build_inventario_dataset(fetch_inventario(), filters=None)
    if inventario.empty:
        return pd.DataFrame(columns=["externo", "zona_auto", "corredor"])

    inventario = inventario.copy()
    inventario["externo"] = inventario["externo"].astype(str).str.strip()
    zona_column = "zona_auto" if "zona_auto" in inventario.columns else "ZONA AUTO"
    inventario["zona_auto"] = inventario.get(zona_column, pd.Series(dtype=str)).fillna("").astype(str).str.strip()
    corredor_column = "corredor" if "corredor" in inventario.columns else "CORREDOR"
    inventario["corredor"] = inventario.get(corredor_column, pd.Series(dtype=str)).fillna("").astype(str).str.strip()
    return inventario[["externo", "zona_auto", "corredor"]].drop_duplicates().copy()


def _prepare_estados_catalog() -> pd.DataFrame:
    estados = normalize_estados_frame(fetch_estados())
    if estados.empty:
        return pd.DataFrame(
            columns=["externo", "direccion", "zona_auto", "corredor", "estado_concert", "gestion_sema"]
        )

    estados = estados.copy()
    estados["externo"] = estados["externo"].astype(str).str.strip()
    estados["direccion"] = estados["direccion"].fillna("").astype(str).str.strip()
    estados["zona_auto"] = estados["zona_auto"].fillna("").astype(str).str.strip()
    estados["corredor"] = estados["corredor"].fillna("").astype(str).str.strip()
    estados["estado_concert"] = estados["estado"].apply(_normalize_estado)
    estados["gestion_sema"] = (
        estados["estado_de_la_interseccion"].fillna("EN SERVICIO").apply(_normalize_estado_interseccion)
    )
    return estados[
        ["externo", "direccion", "zona_auto", "corredor", "estado_concert", "gestion_sema"]
    ].drop_duplicates().copy()


def _prepare_sema_en_linea_catalog(fecha_inicio=None, fecha_fin=None, externo: str | None = None) -> pd.DataFrame:
    realtime = fetch_sema_en_linea_catalog(fecha_inicio, fecha_fin, externo).copy()
    if realtime.empty:
        return pd.DataFrame(
            columns=["externo", "direccion", "corredor", "acceso", "zona_auto", "estado_concert", "gestion_sema"]
        )

    realtime.columns = [str(column).strip() for column in realtime.columns]
    rename_map = {}
    for column in realtime.columns:
        lowered = column.lower()
        if lowered in {"ext", "est"}:
            rename_map[column] = "externo"
        elif lowered == "acceso":
            rename_map[column] = "acceso"
    realtime = realtime.rename(columns=rename_map)

    if "externo" not in realtime.columns:
        realtime["externo"] = ""
    if "acceso" not in realtime.columns:
        realtime["acceso"] = ""

    realtime["externo"] = realtime["externo"].fillna("").astype(str).str.strip()
    realtime["acceso"] = realtime["acceso"].fillna("").astype(str).str.strip()

    metadata = _build_catalog_base(fecha_inicio, fecha_fin)[
        ["externo", "direccion", "corredor", "zona_auto", "estado_concert", "gestion_sema"]
    ].drop_duplicates()
    realtime = realtime.merge(metadata, on="externo", how="left")
    realtime["direccion"] = realtime["direccion"].fillna("").astype(str).str.strip()
    for column in ["corredor", "zona_auto", "estado_concert", "gestion_sema"]:
        realtime[column] = realtime[column].fillna("").astype(str).str.strip()
    return realtime[
        ["externo", "direccion", "corredor", "acceso", "zona_auto", "estado_concert", "gestion_sema"]
    ].drop_duplicates().copy()


def _build_catalog_base(fecha_inicio=None, fecha_fin=None) -> pd.DataFrame:
    detecciones = _prepare_detecciones_catalog(fecha_inicio, fecha_fin)
    inventario = _prepare_inventario_catalog()
    estados = _prepare_estados_catalog()

    metadata = estados.merge(inventario, on="externo", how="outer", suffixes=("", "_inv"))
    if "zona_auto_inv" in metadata.columns:
        metadata["zona_auto"] = metadata["zona_auto"].replace("", pd.NA).fillna(metadata["zona_auto_inv"])
        metadata = metadata.drop(columns=["zona_auto_inv"])
    if "corredor_inv" in metadata.columns:
        metadata["corredor"] = metadata["corredor"].replace("", pd.NA).fillna(metadata["corredor_inv"])
        metadata = metadata.drop(columns=["corredor_inv"])

    if metadata.empty:
        metadata = pd.DataFrame(
            columns=["externo", "direccion", "zona_auto", "corredor", "estado_concert", "gestion_sema"]
        )

    if detecciones.empty:
        base = metadata.copy()
        base["acceso"] = ""
    else:
        base = metadata.merge(detecciones, on="externo", how="outer")

    for column in ["externo", "direccion", "zona_auto", "corredor", "estado_concert", "gestion_sema", "acceso"]:
        if column not in base.columns:
            base[column] = ""

    for column in ["externo", "direccion", "zona_auto", "corredor", "estado_concert", "gestion_sema", "acceso"]:
        base[column] = base[column].fillna("").astype(str).str.strip()

    return base.drop_duplicates().copy()


def _apply_base_filters(base: pd.DataFrame, filters: dict, *, exclude: str | None = None) -> pd.DataFrame:
    filtered = base.copy()
    mapping = {
        "externo": "externo",
        "direccion": "direccion",
        "corredor": "corredor",
        "acceso": "acceso",
        "zona_auto": "zona_auto",
        "estado_concert": "estado_concert",
        "gestion_sema": "gestion_sema",
    }

    for filter_key, column in mapping.items():
        if exclude == filter_key:
            continue
        value = str(filters.get(filter_key, "")).strip()
        if value and column in filtered.columns:
            filtered = filtered[filtered[column] == value]
    return filtered


def get_filter_options(filters: dict, map_key: str | None = None) -> dict[str, list[str]]:
    base = _build_catalog_base(filters.get("fecha_inicio"), filters.get("fecha_fin"))

    options: dict[str, list[str]] = {}
    for key in ["externo", "direccion", "corredor", "acceso", "zona_auto", "estado_concert", "gestion_sema"]:
        filtered = _apply_base_filters(base, filters, exclude=key)
        if key == "corredor":
            options[key] = [EMPTY_OPTION, *_valid_corredor_values(filtered.get(key, pd.Series(dtype=str)))]
        else:
            options[key] = [EMPTY_OPTION, *_sorted_unique(filtered.get(key, pd.Series(dtype=str)))]

    if map_key == "sema_en_linea":
        realtime_base = _prepare_sema_en_linea_catalog(
            filters.get("fecha_inicio"),
            filters.get("fecha_fin"),
            None,
        )
        for key in ["externo", "direccion", "corredor", "acceso"]:
            filtered = _apply_base_filters(realtime_base, filters, exclude=key)
            if key == "corredor":
                options[key] = [EMPTY_OPTION, *_valid_corredor_values(filtered.get(key, pd.Series(dtype=str)))]
            else:
                options[key] = [EMPTY_OPTION, *_sorted_unique(filtered.get(key, pd.Series(dtype=str)))]
    return options


def coerce_filters_to_available_options(filters: dict, options: dict[str, list[str]]) -> dict:
    normalized = dict(filters)
    normalized.pop("sensor", None)
    for key in ["externo", "direccion", "corredor", "acceso", "zona_auto", "estado_concert", "gestion_sema"]:
        if normalized.get(key, "") not in options.get(key, [EMPTY_OPTION]):
            normalized[key] = EMPTY_OPTION
    return normalized
