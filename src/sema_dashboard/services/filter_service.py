from __future__ import annotations

import pandas as pd


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

    if "externo" in result.columns and filters.get("externo"):
        result = result[result["externo"].astype(str) == str(filters["externo"])]
    if "ext" in result.columns and filters.get("externo"):
        result = result[result["ext"].astype(str) == str(filters["externo"])]
    if "Acceso" in result.columns and filters.get("acceso"):
        result = result[result["Acceso"].astype(str) == str(filters["acceso"])]
    if "Sensor" in result.columns and filters.get("sensor"):
        result = result[result["Sensor"].astype(str) == str(filters["sensor"])]
    if "ZONA AUTO" in result.columns and filters.get("zona_auto"):
        result = result[result["ZONA AUTO"].astype(str) == str(filters["zona_auto"])]
    if "zona_auto" in result.columns and filters.get("zona_auto"):
        result = result[result["zona_auto"].astype(str) == str(filters["zona_auto"])]
    return result
