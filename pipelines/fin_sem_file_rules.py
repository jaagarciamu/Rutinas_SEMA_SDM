from __future__ import annotations

from datetime import date

import pandas as pd

from pipelines.detecciones_file_rules import classify_drive_files, normalize_name_key

REQUIRED_COLUMNS = [
    "size_in_MB",
    "id",
    "name",
    "creation",
    "last_modification",
    "type_of_file",
    "date",
    "mes",
    "Estado",
    "num",
]


def ensure_required_columns(registro: pd.DataFrame) -> pd.DataFrame:
    for column in REQUIRED_COLUMNS:
        if column not in registro.columns:
            registro[column] = ""

    ordered_columns = REQUIRED_COLUMNS + [
        column for column in registro.columns if column not in REQUIRED_COLUMNS
    ]
    return registro[ordered_columns]


def list_drive_files(drive_service, folder_id: str) -> pd.DataFrame:
    data: list[list[object]] = []
    page_token = None

    while True:
        response = (
            drive_service.files()
            .list(
                q=f"'{folder_id}' in parents",
                pageSize=1000,
                fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, createdTime)",
                pageToken=page_token,
            )
            .execute()
        )

        for row in response.get("files", []):
            if row.get("mimeType") == "application/vnd.google-apps.folder":
                continue

            size_mb = round(int(row.get("size", 0)) / 1_000_000, 2)
            data.append(
                [
                    size_mb,
                    str(row.get("id", "")),
                    str(row.get("name", "")),
                    str(row.get("createdTime", "")),
                    str(row.get("modifiedTime", "")),
                    str(row.get("mimeType", "")),
                ]
            )

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return pd.DataFrame(
        data,
        columns=[
            "size_in_MB",
            "id",
            "name",
            "creation",
            "last_modification",
            "type_of_file",
        ],
    )


def sort_registry_for_output(registro_df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_required_columns(registro_df.copy())
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["last_modification"] = pd.to_datetime(df["last_modification"], errors="coerce")
    df["name"] = df["name"].astype(str)
    df = df.sort_values(
        by=["date", "last_modification", "name"],
        ascending=[False, False, True],
        na_position="last",
    )
    return df.reset_index(drop=True)


def classify_fin_sem_drive_files(
    drive_df: pd.DataFrame,
    existing_registry: pd.DataFrame,
    special_dates: list[date],
    min_size_mb: float,
    max_size_mb: float,
    target_size_mb: float,
) -> pd.DataFrame:
    previous_registry = ensure_required_columns(existing_registry.copy())
    classified_df = classify_drive_files(
        ensure_required_columns(drive_df.copy()),
        min_size_mb=min_size_mb,
        max_size_mb=max_size_mb,
        target_size_mb=target_size_mb,
        existing_registry=previous_registry,
    )
    classified_df = ensure_required_columns(classified_df)
    classified_df["date"] = pd.to_datetime(classified_df["date"], errors="coerce")

    allowed_dates = {pd.Timestamp(current_date).date() for current_date in special_dates}
    if allowed_dates:
        in_scope = classified_df["date"].dt.date.isin(allowed_dates)
        eligible_out_of_scope = classified_df["Estado"].isin(["Pendiente", "Procesado", "Duplicado"]) & ~in_scope
        classified_df.loc[eligible_out_of_scope, "Estado"] = "Fuera_Alcance"
    else:
        eligible_any = classified_df["Estado"].isin(["Pendiente", "Procesado", "Duplicado"])
        classified_df.loc[eligible_any, "Estado"] = "Fuera_Alcance"

    return merge_with_historical_registry(previous_registry, classified_df)


def merge_with_historical_registry(
    previous_registry: pd.DataFrame,
    current_registry: pd.DataFrame,
) -> pd.DataFrame:
    previous = ensure_required_columns(previous_registry.copy())
    current = ensure_required_columns(current_registry.copy())

    if previous.empty:
        return sort_registry_for_output(current)
    if current.empty:
        return sort_registry_for_output(previous)

    previous["_name_key"] = previous["name"].astype(str).map(normalize_name_key)
    current["_name_key"] = current["name"].astype(str).map(normalize_name_key)

    previous_unique = previous.sort_values(
        by=["_name_key", "date", "last_modification"],
        ascending=[True, False, False],
        na_position="last",
    ).drop_duplicates(subset=["_name_key"], keep="first")

    current = current.set_index("_name_key")
    previous_unique = previous_unique.set_index("_name_key")

    overlapping_keys = current.index.intersection(previous_unique.index)
    if len(overlapping_keys) > 0:
        # Preserve historical status and counters for known rows while refreshing metadata/IDs.
        current.loc[overlapping_keys, "Estado"] = previous_unique.loc[overlapping_keys, "Estado"]
        current.loc[overlapping_keys, "num"] = previous_unique.loc[overlapping_keys, "num"]

    historical_only = previous_unique.loc[previous_unique.index.difference(current.index)].copy()
    merged = pd.concat([current.reset_index(), historical_only.reset_index()], ignore_index=True)
    merged = merged.drop(columns=["_name_key"], errors="ignore")
    return sort_registry_for_output(merged)


def build_fin_sem_pending_preview(
    registro_df: pd.DataFrame,
    special_dates: list[date],
) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    df = ensure_required_columns(registro_df.copy())
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["Estado"] = df["Estado"].astype(str)

    allowed_dates = {pd.Timestamp(current_date).date() for current_date in special_dates}
    if allowed_dates:
        df = df[df["date"].dt.date.isin(allowed_dates)].copy()
    else:
        df = df.iloc[0:0].copy()

    pending = df[df["Estado"].str.lower() == "pendiente"].copy()
    processed = df[df["Estado"].str.lower() == "procesado"].copy()
    max_processed_date = processed["date"].max()
    if pd.notna(max_processed_date):
        pending = pending[pending["date"] > max_processed_date]

    pending = sort_registry_for_output(pending)
    return pending, max_processed_date


def mark_registry_processed(
    registro_df: pd.DataFrame,
    processed_counts: pd.DataFrame,
) -> pd.DataFrame:
    registro = ensure_required_columns(registro_df.copy())
    registro["num"] = pd.to_numeric(registro["num"], errors="coerce").fillna(0).astype(int)
    if processed_counts.empty:
        return sort_registry_for_output(registro)

    count_map = (
        processed_counts[["id", "num"]]
        .drop_duplicates(subset=["id"], keep="last")
        .set_index("id")["num"]
        .to_dict()
    )
    mask = registro["id"].isin(count_map.keys())
    registro.loc[mask, "Estado"] = "Procesado"
    registro.loc[mask, "num"] = registro.loc[mask, "id"].map(count_map).fillna(
        registro.loc[mask, "num"]
    )
    return sort_registry_for_output(registro)
