from __future__ import annotations

import re
import unicodedata

import pandas as pd

MANUAL_SELECTION_COLUMN = "Seleccion_manual"
PROCESSABLE_PATTERN = re.compile(
    r"^(?:detektor-messquerschnitt(?:-verkehrsmesswerte - verarbeitet)?|"
    r"detector-measurement point-traffic data - processed|"
    r"detector-punto de medici.n-datos de tr.fico - procesados)-(\d{6})_\1\.csv$"
)


def normalize_detector_name(value: object) -> str:
    text = str(value).strip().lower()
    replacements = {
        "ÃƒÂ¡": "Ã¡",
        "ÃƒÂ©": "Ã©",
        "ÃƒÂ­": "Ã­",
        "ÃƒÂ³": "Ã³",
        "ÃƒÂº": "Ãº",
        "Ä‚Ä„": "Ã¡",
        "Ä‚Å‚": "Ã³",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text)


def normalize_name_key(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def normalize_manual_selection_value(value: object) -> str:
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text)


def is_processable_name(series: pd.Series) -> pd.Series:
    normalized = series.astype(str).map(normalize_detector_name)
    return normalized.str.match(PROCESSABLE_PATTERN, na=False)


def extract_processable_date_token(series: pd.Series) -> pd.Series:
    normalized = series.astype(str).map(normalize_detector_name)
    return normalized.str.extract(PROCESSABLE_PATTERN, expand=True).iloc[:, 0]


def is_manual_selection(series: pd.Series) -> pd.Series:
    normalized = series.astype(str).map(normalize_manual_selection_value)
    return normalized.isin({"1", "si", "s", "x", "true", "manual", "forzar"})


def preserve_registry_metadata(
    classified_df: pd.DataFrame,
    existing_registry: pd.DataFrame | None,
) -> pd.DataFrame:
    if existing_registry is None or existing_registry.empty:
        return classified_df

    registry_df = existing_registry.copy()
    registry_df["name"] = registry_df["name"].astype(str)
    registry_df["_name_key"] = registry_df["name"].map(normalize_name_key)
    metadata_columns = [
        column
        for column in registry_df.columns
        if column == MANUAL_SELECTION_COLUMN
        or (column not in classified_df.columns and column != "_name_key")
    ]
    if not metadata_columns:
        return classified_df

    metadata_df = registry_df[["_name_key", *metadata_columns]].drop_duplicates(
        subset=["_name_key"], keep="first"
    )
    manual_registry_column = f"{MANUAL_SELECTION_COLUMN}_registry"
    if MANUAL_SELECTION_COLUMN in metadata_df.columns and MANUAL_SELECTION_COLUMN in classified_df.columns:
        metadata_df = metadata_df.rename(columns={MANUAL_SELECTION_COLUMN: manual_registry_column})

    merged_df = classified_df.merge(metadata_df, on="_name_key", how="left")
    if manual_registry_column in merged_df.columns:
        merged_df[MANUAL_SELECTION_COLUMN] = merged_df[manual_registry_column].where(
            merged_df[manual_registry_column].notna(),
            merged_df[MANUAL_SELECTION_COLUMN],
        )
        merged_df = merged_df.drop(columns=[manual_registry_column], errors="ignore")

    return merged_df


def select_processable_pending_rows(
    registro_df: pd.DataFrame,
    min_size_mb: float,
    max_size_mb: float,
    target_size_mb: float,
) -> tuple[pd.DataFrame, pd.Timestamp | None, pd.DataFrame]:
    df = registro_df.copy()
    if df.empty:
        return df.copy(), None, df.copy()

    if "Estado" not in df.columns:
        df["Estado"] = ""
    if "date" not in df.columns:
        df["date"] = pd.Series(dtype="datetime64[ns]")
    if "last_modification" not in df.columns:
        df["last_modification"] = ""
    if "size_in_MB" not in df.columns:
        df["size_in_MB"] = pd.Series(dtype="float64")
    if MANUAL_SELECTION_COLUMN not in df.columns:
        df[MANUAL_SELECTION_COLUMN] = ""

    df["Estado"] = df["Estado"].astype(str)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["last_modification"] = pd.to_datetime(df["last_modification"], errors="coerce")
    df["size_in_MB"] = pd.to_numeric(df["size_in_MB"], errors="coerce")
    df["_manual_selected"] = is_manual_selection(df[MANUAL_SELECTION_COLUMN])

    pending = df[df["Estado"].str.lower() == "pendiente"].copy()
    processed = df[df["Estado"].str.lower() == "procesado"].copy()

    max_processed_date = processed["date"].max()
    if pd.notna(max_processed_date):
        pending = pending[
            pending["_manual_selected"] | (pending["date"] > max_processed_date)
        ].copy()

    valid_mask = pending["size_in_MB"].between(min_size_mb, max_size_mb, inclusive="both")
    pending_df = pending[valid_mask].copy()
    excluded_by_size_df = pending[~valid_mask].copy()

    if not pending_df.empty:
        pending_df["_size_gap_mb"] = (pending_df["size_in_MB"] - target_size_mb).abs()
        pending_df = pending_df.sort_values(
            by=["_manual_selected", "date", "_size_gap_mb", "last_modification"],
            ascending=[False, False, True, False],
            na_position="last",
        ).drop(columns=["_size_gap_mb"])

    return (
        pending_df.drop(columns=["_manual_selected"], errors="ignore"),
        max_processed_date,
        excluded_by_size_df.drop(columns=["_manual_selected"], errors="ignore"),
    )


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

    df = preserve_registry_metadata(df, existing_registry)
    return df.drop(columns=["_name_key", "_name_ok", "_is_csv", "_size_ok", "_eligible"], errors="ignore")
