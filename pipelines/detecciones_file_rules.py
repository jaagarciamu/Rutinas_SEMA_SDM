from __future__ import annotations

import re
import unicodedata

import pandas as pd

PROCESSABLE_PATTERN = re.compile(
    r"^(?:detektor-messquerschnitt(?:-verkehrsmesswerte - verarbeitet)?|"
    r"detector-measurement point-traffic data - processed|"
    r"detector-punto de medici.n-datos de tr.fico - procesados)-(\d{6})_\1\.csv$"
)


def normalize_detector_name(value: object) -> str:
    text = str(value).strip().lower()
    replacements = {
        "Ã¡": "á",
        "Ã©": "é",
        "Ã­": "í",
        "Ã³": "ó",
        "Ãº": "ú",
        "ĂĄ": "á",
        "Ăł": "ó",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text)


def normalize_name_key(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def is_processable_name(series: pd.Series) -> pd.Series:
    normalized = series.astype(str).map(normalize_detector_name)
    return normalized.str.match(PROCESSABLE_PATTERN, na=False)


def extract_processable_date_token(series: pd.Series) -> pd.Series:
    normalized = series.astype(str).map(normalize_detector_name)
    return normalized.str.extract(PROCESSABLE_PATTERN, expand=True).iloc[:, 0]


def classify_drive_files(
    drive_df: pd.DataFrame,
    min_size_mb: float,
    max_size_mb: float,
    target_size_mb: float,
    existing_registry: pd.DataFrame | None = None,
) -> pd.DataFrame:
    df = drive_df.copy()
    if df.empty:
        if "Estado" not in df.columns:
            df["Estado"] = pd.Series(dtype="object")
        return df

    df["name"] = df["name"].astype(str)
    df["type_of_file"] = df["type_of_file"].astype(str)
    df["size_in_MB"] = pd.to_numeric(df["size_in_MB"], errors="coerce")
    df["last_modification"] = pd.to_datetime(df["last_modification"], errors="coerce")
    df["_name_key"] = df["name"].map(normalize_name_key)
    df["_name_ok"] = is_processable_name(df["name"])
    date_token = extract_processable_date_token(df["name"])
    df["date"] = pd.to_datetime(date_token, format="%y%m%d", errors="coerce")
    df["mes"] = df["date"].dt.strftime("%y%m")
    df["_is_csv"] = df["type_of_file"].eq("text/csv")
    df["_size_ok"] = df["size_in_MB"].between(min_size_mb, max_size_mb, inclusive="both")
    df["_eligible"] = df["_is_csv"] & df["_name_ok"] & df["_size_ok"] & df["date"].notna()
    df["Estado"] = "No_Cumple"

    prev_processed_keys: set[str] = set()
    prev_processed_dates: set[pd.Timestamp] = set()
    if existing_registry is not None and not existing_registry.empty and "Estado" in existing_registry.columns:
        registry_df = existing_registry.copy()
        registry_df["name"] = registry_df["name"].astype(str)
        registry_df["_name_key"] = registry_df["name"].map(normalize_name_key)
        registry_df["date"] = pd.to_datetime(registry_df.get("date"), errors="coerce")
        prev_processed_keys = set(
            registry_df.loc[
                registry_df["Estado"].astype(str).str.lower().eq("procesado"),
                "_name_key",
            ].dropna()
        )
        prev_processed_dates = set(
            registry_df.loc[
                registry_df["Estado"].astype(str).str.lower().eq("procesado"),
                "date",
            ].dropna()
        )

    eligible_df = df[df["_eligible"]].copy()
    if not eligible_df.empty:
        eligible_df["_size_gap_mb"] = (eligible_df["size_in_MB"] - target_size_mb).abs()
        eligible_df = eligible_df.sort_values(
            by=["date", "_size_gap_mb", "last_modification", "name"],
            ascending=[False, True, False, True],
            na_position="last",
        )
        canonical_idx = eligible_df.groupby("date", sort=False).head(1).index
        duplicate_idx = eligible_df.index.difference(canonical_idx)
        df.loc[canonical_idx, "Estado"] = "Pendiente"
        df.loc[duplicate_idx, "Estado"] = "Duplicado"
        if prev_processed_keys:
            processed_mask = df.index.isin(canonical_idx) & df["_name_key"].isin(prev_processed_keys)
            df.loc[processed_mask, "Estado"] = "Procesado"
        if prev_processed_dates:
            processed_date_mask = df.index.isin(canonical_idx) & df["date"].isin(prev_processed_dates)
            df.loc[processed_date_mask, "Estado"] = "Procesado"

    return df.drop(columns=["_name_key", "_name_ok", "_is_csv", "_size_ok", "_eligible"], errors="ignore")
