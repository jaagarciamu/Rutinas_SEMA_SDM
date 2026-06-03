from __future__ import annotations

import pandas as pd


def apply_common_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    result = df.copy()
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
