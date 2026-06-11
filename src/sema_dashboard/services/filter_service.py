from __future__ import annotations

import pandas as pd


def _normalize_string_series(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.strip()


def _has_meaningful_values(df: pd.DataFrame, column: str) -> bool:
    if column not in df.columns:
        return False
    values = _normalize_string_series(df[column])
    return bool((values != "").any())


def _resolve_filtered_externos(filters: dict) -> set[str] | None:
    if not any(filters.get(key) for key in ["direccion", "corredor", "estado_concert", "gestion_sema", "zona_auto", "externo"]):
        return None

    from sema_dashboard.repositories.estados_repository import fetch_estados
    from sema_dashboard.repositories.inventario_repository import fetch_inventario
    from sema_dashboard.transforms.estados import _normalize_estado, normalize_estados_frame
    from sema_dashboard.transforms.inventario import build_inventario_dataset
    from sema_dashboard.transforms.novedades import _normalize_estado_interseccion

    estados = normalize_estados_frame(fetch_estados())
    inventario = build_inventario_dataset(fetch_inventario(), filters=None)

    if estados.empty and inventario.empty:
        return set()

    estados_meta = pd.DataFrame(columns=["externo", "direccion", "zona_auto", "estado", "estado_de_la_interseccion", "corredor"])
    if not estados.empty:
        estados = estados.copy()
        estados["externo"] = _normalize_string_series(estados["externo"])
        estados["direccion"] = _normalize_string_series(estados["direccion"])
        estados["zona_auto"] = _normalize_string_series(estados["zona_auto"])
        estados["corredor"] = _normalize_string_series(estados["corredor"])
        estados["estado"] = estados["estado"].apply(_normalize_estado)
        estados["estado_de_la_interseccion"] = (
            estados["estado_de_la_interseccion"].fillna("EN SERVICIO").apply(_normalize_estado_interseccion)
        )
        estados_meta = estados[
            ["externo", "direccion", "zona_auto", "estado", "estado_de_la_interseccion", "corredor"]
        ].drop_duplicates()

    inventario_meta = pd.DataFrame(columns=["externo", "direccion", "zona_auto", "corredor"])
    if not inventario.empty:
        inventario = inventario.copy()
        inventario["externo"] = _normalize_string_series(inventario["externo"])
        direccion_column = "DIRECCION CORTA" if "DIRECCION CORTA" in inventario.columns else "direccion"
        zona_column = "ZONA AUTO" if "ZONA AUTO" in inventario.columns else "zona_auto"
        corredor_column = "corredor" if "corredor" in inventario.columns else "CORREDOR"
        inventario["direccion"] = _normalize_string_series(inventario.get(direccion_column, pd.Series(dtype=str)))
        inventario["zona_auto"] = _normalize_string_series(inventario.get(zona_column, pd.Series(dtype=str)))
        inventario["corredor"] = _normalize_string_series(inventario.get(corredor_column, pd.Series(dtype=str)))
        inventario_meta = inventario[["externo", "direccion", "zona_auto", "corredor"]].drop_duplicates()

    metadata = estados_meta.merge(inventario_meta, on="externo", how="outer", suffixes=("", "_inv"))
    for column in ["direccion", "zona_auto", "corredor"]:
        fallback = f"{column}_inv"
        if fallback in metadata.columns:
            metadata[column] = metadata[column].replace("", pd.NA).fillna(metadata[fallback])
            metadata = metadata.drop(columns=[fallback])

    metadata["externo"] = _normalize_string_series(metadata.get("externo", pd.Series(dtype=str)))
    metadata["direccion"] = _normalize_string_series(metadata.get("direccion", pd.Series(dtype=str)))
    metadata["zona_auto"] = _normalize_string_series(metadata.get("zona_auto", pd.Series(dtype=str)))
    metadata["corredor"] = _normalize_string_series(metadata.get("corredor", pd.Series(dtype=str)))
    metadata["estado"] = _normalize_string_series(metadata.get("estado", pd.Series(dtype=str)))
    metadata["estado_de_la_interseccion"] = _normalize_string_series(
        metadata.get("estado_de_la_interseccion", pd.Series(dtype=str))
    )

    if filters.get("externo"):
        metadata = metadata[metadata["externo"] == str(filters["externo"]).strip()]
    if filters.get("direccion"):
        metadata = metadata[metadata["direccion"] == str(filters["direccion"]).strip()]
    if filters.get("corredor"):
        metadata = metadata[metadata["corredor"] == str(filters["corredor"]).strip()]
    if filters.get("zona_auto"):
        metadata = metadata[metadata["zona_auto"] == str(filters["zona_auto"]).strip()]
    if filters.get("estado_concert"):
        metadata = metadata[metadata["estado"] == str(filters["estado_concert"]).strip()]
    if filters.get("gestion_sema"):
        metadata = metadata[metadata["estado_de_la_interseccion"] == str(filters["gestion_sema"]).strip()]

    return set(_normalize_string_series(metadata["externo"]))


def apply_common_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    result = df.copy()
    fecha_inicio = filters.get("fecha_inicio")
    fecha_fin = filters.get("fecha_fin")

    if "Tiempo" in result.columns and (fecha_inicio or fecha_fin):
        result["Tiempo"] = pd.to_datetime(result["Tiempo"], errors="coerce")
        if fecha_inicio:
            result = result[result["Tiempo"] >= pd.to_datetime(fecha_inicio)]
        if fecha_fin:
            result = result[result["Tiempo"] < (pd.to_datetime(fecha_fin) + pd.Timedelta(days=1))]

    if "fecha" in result.columns and (fecha_inicio or fecha_fin):
        fechas = pd.to_datetime(result["fecha"], errors="coerce")
        if fecha_inicio:
            result = result[fechas >= pd.to_datetime(fecha_inicio)]
        if fecha_fin:
            result = result[fechas < (pd.to_datetime(fecha_fin) + pd.Timedelta(days=1))]

    externos_validos = _resolve_filtered_externos(filters)
    if externos_validos is not None:
        if "externo" in result.columns:
            result = result[_normalize_string_series(result["externo"]).isin(externos_validos)]
        if "ext" in result.columns:
            result = result[_normalize_string_series(result["ext"]).isin(externos_validos)]

    if "externo" in result.columns and filters.get("externo"):
        result = result[_normalize_string_series(result["externo"]) == str(filters["externo"]).strip()]
    if "ext" in result.columns and filters.get("externo"):
        result = result[_normalize_string_series(result["ext"]) == str(filters["externo"]).strip()]
    if "direccion" in result.columns and filters.get("direccion") and _has_meaningful_values(result, "direccion"):
        result = result[_normalize_string_series(result["direccion"]) == str(filters["direccion"]).strip()]
    if "DIRECCION CORTA" in result.columns and filters.get("direccion") and _has_meaningful_values(result, "DIRECCION CORTA"):
        result = result[_normalize_string_series(result["DIRECCION CORTA"]) == str(filters["direccion"]).strip()]
    if "corredor" in result.columns and filters.get("corredor") and _has_meaningful_values(result, "corredor"):
        result = result[_normalize_string_series(result["corredor"]) == str(filters["corredor"]).strip()]
    if "CORREDOR" in result.columns and filters.get("corredor") and _has_meaningful_values(result, "CORREDOR"):
        result = result[_normalize_string_series(result["CORREDOR"]) == str(filters["corredor"]).strip()]
    if "Acceso" in result.columns and filters.get("acceso"):
        result = result[_normalize_string_series(result["Acceso"]) == str(filters["acceso"]).strip()]
    if "ZONA AUTO" in result.columns and filters.get("zona_auto") and _has_meaningful_values(result, "ZONA AUTO"):
        result = result[_normalize_string_series(result["ZONA AUTO"]) == str(filters["zona_auto"]).strip()]
    if "zona_auto" in result.columns and filters.get("zona_auto") and _has_meaningful_values(result, "zona_auto"):
        result = result[_normalize_string_series(result["zona_auto"]) == str(filters["zona_auto"]).strip()]
    if "estado" in result.columns and filters.get("estado_concert"):
        result = result[_normalize_string_series(result["estado"]) == str(filters["estado_concert"]).strip()]
    if "estado_de_la_interseccion" in result.columns and filters.get("gestion_sema"):
        result = result[
            _normalize_string_series(result["estado_de_la_interseccion"]) == str(filters["gestion_sema"]).strip()
        ]
    return result
