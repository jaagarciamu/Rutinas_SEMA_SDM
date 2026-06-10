from __future__ import annotations

import pandas as pd


def _normalize_string_series(values: pd.Series) -> pd.Series:
    return values.fillna("").astype(str).str.strip()


def _resolve_state_filtered_externos(filters: dict) -> set[str] | None:
    if not any(filters.get(key) for key in ["direccion", "estado_concert", "gestion_sema", "zona_auto", "externo"]):
        return None

    from sema_dashboard.repositories.estados_repository import fetch_estados
    from sema_dashboard.transforms.estados import _normalize_estado, normalize_estados_frame
    from sema_dashboard.transforms.novedades import _normalize_estado_interseccion

    estados = normalize_estados_frame(fetch_estados())
    if estados.empty:
        return set()

    estados = estados.copy()
    estados["externo"] = _normalize_string_series(estados["externo"])
    estados["direccion"] = _normalize_string_series(estados["direccion"])
    estados["zona_auto"] = _normalize_string_series(estados["zona_auto"])
    estados["estado"] = estados["estado"].apply(_normalize_estado)
    estados["estado_de_la_interseccion"] = (
        estados["estado_de_la_interseccion"].fillna("EN SERVICIO").apply(_normalize_estado_interseccion)
    )

    if filters.get("externo"):
        estados = estados[estados["externo"] == str(filters["externo"]).strip()]
    if filters.get("direccion"):
        estados = estados[estados["direccion"] == str(filters["direccion"]).strip()]
    if filters.get("zona_auto"):
        estados = estados[estados["zona_auto"] == str(filters["zona_auto"]).strip()]
    if filters.get("estado_concert"):
        estados = estados[estados["estado"] == str(filters["estado_concert"]).strip()]
    if filters.get("gestion_sema"):
        estados = estados[estados["estado_de_la_interseccion"] == str(filters["gestion_sema"]).strip()]

    return set(_normalize_string_series(estados["externo"]))


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

    externos_validos = _resolve_state_filtered_externos(filters)
    if externos_validos is not None:
        if "externo" in result.columns:
            result = result[_normalize_string_series(result["externo"]).isin(externos_validos)]
        if "ext" in result.columns:
            result = result[_normalize_string_series(result["ext"]).isin(externos_validos)]

    if "externo" in result.columns and filters.get("externo"):
        result = result[_normalize_string_series(result["externo"]) == str(filters["externo"]).strip()]
    if "ext" in result.columns and filters.get("externo"):
        result = result[_normalize_string_series(result["ext"]) == str(filters["externo"]).strip()]
    if "direccion" in result.columns and filters.get("direccion"):
        result = result[_normalize_string_series(result["direccion"]) == str(filters["direccion"]).strip()]
    if "DIRECCION CORTA" in result.columns and filters.get("direccion"):
        result = result[_normalize_string_series(result["DIRECCION CORTA"]) == str(filters["direccion"]).strip()]
    if "Acceso" in result.columns and filters.get("acceso"):
        result = result[_normalize_string_series(result["Acceso"]) == str(filters["acceso"]).strip()]
    if "ZONA AUTO" in result.columns and filters.get("zona_auto"):
        result = result[_normalize_string_series(result["ZONA AUTO"]) == str(filters["zona_auto"]).strip()]
    if "zona_auto" in result.columns and filters.get("zona_auto"):
        result = result[_normalize_string_series(result["zona_auto"]) == str(filters["zona_auto"]).strip()]
    if "estado" in result.columns and filters.get("estado_concert"):
        result = result[_normalize_string_series(result["estado"]) == str(filters["estado_concert"]).strip()]
    if "estado_de_la_interseccion" in result.columns and filters.get("gestion_sema"):
        result = result[
            _normalize_string_series(result["estado_de_la_interseccion"]) == str(filters["gestion_sema"]).strip()
        ]
    return result
