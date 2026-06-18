from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path

import gspread
import gspread_dataframe as gd
import pandas as pd
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

if __package__ is None or __package__ == "":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.detecciones_file_rules import (
    MANUAL_SELECTION_COLUMN,
    classify_drive_files,
    extract_processable_date_token,
    is_manual_selection,
    is_processable_name,
    normalize_name_key,
    select_processable_pending_rows,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/bigquery",
]
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
    MANUAL_SELECTION_COLUMN,
]


class ConfigurationError(RuntimeError):
    """Error de configuración para variables de entorno faltantes."""


@dataclass
class SyncStats:
    total_drive_files: int = 0
    updated_ids: int = 0
    new_pending_rows: int = 0
    unchanged_rows: int = 0


def load_environment() -> None:
    load_dotenv(ROOT_DIR / "config" / ".env")
    load_dotenv(ROOT_DIR / ".env")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Variable de entorno requerida no definida: {name}")
    return value


def parse_scopes(raw_scopes: str | None) -> list[str]:
    if not raw_scopes:
        return DEFAULT_SCOPES
    return [scope.strip() for scope in raw_scopes.split(",") if scope.strip()]


def extract_folder_id(value: str) -> str:
    value = value.strip()
    if "/folders/" in value:
        match = re.search(r"/folders/([a-zA-Z0-9_-]+)", value)
        if not match:
            raise ValueError(f"No se pudo extraer ID de carpeta desde URL: {value}")
        return match.group(1)
    return value


def build_drive_client():
    credentials_path = require_env("GOOGLE_APPLICATION_CREDENTIALS")
    credentials_file = (ROOT_DIR / credentials_path).resolve()
    scopes = parse_scopes(os.getenv("GOOGLE_SCOPES"))
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_file), scopes=scopes
    )
    return build("drive", "v3", credentials=credentials)


def build_sheet_client() -> tuple[gspread.Client, str, str]:
    credentials_path = require_env("GOOGLE_APPLICATION_CREDENTIALS")
    credentials_file = (ROOT_DIR / credentials_path).resolve()
    client = gspread.service_account(filename=str(credentials_file))
    sheet_url = require_env("GOOGLE_DETECCIONES_SHEET_URL")
    worksheet_name = require_env("GOOGLE_DETECCIONES_WORKSHEET")
    return client, sheet_url, worksheet_name


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

    df = pd.DataFrame(
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

    if df.empty:
        return df

    date_token = extract_processable_date_token(df["name"])
    df["date"] = pd.to_datetime(date_token, format="%y%m%d", errors="coerce")
    df["mes"] = df["date"].dt.strftime("%y%m")
    df["num"] = "0"
    return df


def normalize_name(value: str) -> str:
    return normalize_name_key(value)


def ensure_required_columns(registro: pd.DataFrame) -> pd.DataFrame:
    for column in REQUIRED_COLUMNS:
        if column not in registro.columns:
            registro[column] = ""

    ordered_columns = REQUIRED_COLUMNS + [
        column for column in registro.columns if column not in REQUIRED_COLUMNS
    ]
    return registro[ordered_columns]


def filter_by_size_window(
    df: pd.DataFrame,
    min_size_mb: float,
    max_size_mb: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    scoped = df.copy()
    scoped["size_in_MB"] = pd.to_numeric(scoped["size_in_MB"], errors="coerce")
    valid_mask = scoped["size_in_MB"].between(min_size_mb, max_size_mb, inclusive="both")
    return scoped[valid_mask].copy(), scoped[~valid_mask].copy()


def filter_processable_registry_scope(registro_df: pd.DataFrame) -> pd.DataFrame:
    """Mantiene solo archivos canonicos relevantes para el pipeline 01."""
    df = ensure_required_columns(registro_df.copy())
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["Estado"] = df["Estado"].astype(str)
    df = df[df["Estado"].str.lower().isin({"pendiente", "procesado"})]
    return df.reset_index(drop=True)


def dedupe_registry_by_name(registro_df: pd.DataFrame) -> pd.DataFrame:
    """Conserva una sola fila por archivo (`name`) priorizando estado Procesado."""
    df = ensure_required_columns(registro_df.copy())
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["last_modification"] = pd.to_datetime(df["last_modification"], errors="coerce")
    df["Estado"] = df["Estado"].astype(str)
    df["_name_key"] = df["name"].map(normalize_name)
    df["_estado_rank"] = df["Estado"].str.lower().map({"procesado": 2, "pendiente": 1}).fillna(0)
    df = df.sort_values(
        by=["_name_key", "_estado_rank", "date", "last_modification"],
        ascending=[True, False, False, False],
        na_position="last",
    )
    df = df.drop_duplicates(subset=["_name_key"], keep="first")
    df = df.drop(columns=["_name_key", "_estado_rank"], errors="ignore")
    return df.reset_index(drop=True)


def sort_registry_for_output(registro_df: pd.DataFrame) -> pd.DataFrame:
    """Ordena para seguimiento: fecha mas reciente primero."""
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


def build_processable_pending_preview(
    registro: pd.DataFrame,
    min_size_mb: float,
    max_size_mb: float,
    target_size_mb: float,
) -> tuple[pd.DataFrame, pd.Timestamp | None, pd.DataFrame]:
    """Replica el criterio de selección de 01_DETECCIONES_SEMA.py."""
    pending, max_processed_date, excluded_by_size = select_processable_pending_rows(
        ensure_required_columns(registro.copy()),
        min_size_mb=min_size_mb,
        max_size_mb=max_size_mb,
        target_size_mb=target_size_mb,
    )
    pending = pending[REQUIRED_COLUMNS].sort_values(
        by="date", ascending=False, na_position="last"
    )
    excluded_by_size = excluded_by_size[REQUIRED_COLUMNS].sort_values(
        by="date", ascending=False, na_position="last"
    )
    return pending, max_processed_date, excluded_by_size


def sync_registry(
    registro: pd.DataFrame, drive_df: pd.DataFrame
) -> tuple[pd.DataFrame, SyncStats, pd.DataFrame]:
    min_size_mb = float(os.getenv("DETECCIONES_SEMA_MIN_SIZE_MB", "50"))
    max_size_mb = float(os.getenv("DETECCIONES_SEMA_MAX_SIZE_MB", "100"))
    target_size_mb = float(os.getenv("DETECCIONES_SEMA_TARGET_SIZE_MB", "67"))
    previous_registry = ensure_required_columns(registro.copy())
    classified_df = classify_drive_files(
        ensure_required_columns(drive_df.copy()),
        min_size_mb=min_size_mb,
        max_size_mb=max_size_mb,
        target_size_mb=target_size_mb,
        existing_registry=previous_registry,
    )
    stats = SyncStats(total_drive_files=len(classified_df))
    id_changes: list[dict[str, object]] = []
    previous_registry["_name_key"] = previous_registry["name"].astype(str).map(normalize_name)
    classified_df["_name_key"] = classified_df["name"].astype(str).map(normalize_name)
    prev_ids = previous_registry.set_index("_name_key")["id"] if not previous_registry.empty else pd.Series(dtype="object")
    existing_keys = set(previous_registry["_name_key"]) if not previous_registry.empty else set()

    for _, row in classified_df.iterrows():
        row_key = row["_name_key"]
        if row_key in existing_keys:
            old_id = str(prev_ids.get(row_key, ""))
            new_id = str(row["id"])
            if old_id and old_id != new_id:
                stats.updated_ids += 1
                id_changes.append(
                    {
                        "name": row["name"],
                        "id_old": old_id,
                        "id_new": new_id,
                        "date": row["date"],
                        "mes": row["mes"],
                        "estado_actual": row["Estado"],
                    }
                )
            else:
                stats.unchanged_rows += 1
        elif str(row["Estado"]).lower() == "pendiente":
            stats.new_pending_rows += 1

    registro = classified_df.drop(columns=["_name_key"], errors="ignore")
    registro["num"] = registro.get("num", "0")
    registro["num"] = registro["num"].fillna("0").astype(str)
    registro = ensure_required_columns(registro)
    registro = sort_registry_for_output(registro)

    id_changes_df = pd.DataFrame(
        id_changes,
        columns=["name", "id_old", "id_new", "date", "mes", "estado_actual"],
    )

    return registro.reset_index(drop=True), stats, id_changes_df


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sincroniza IDs de archivos de detecciones por nombre en la hoja de registro. "
            "Por defecto ejecuta dry-run (no escribe en Sheets)."
        )
    )
    parser.add_argument(
        "--new-folder",
        default="",
        help=(
            "ID o URL de la carpeta de Drive con archivos de detecciones. "
            "Si se omite, usa GOOGLE_DETECCIONES_DRIVE_FOLDER_ID desde config/.env."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica los cambios sobre la hoja de Google Sheets (si no se define, solo simula).",
    )
    parser.add_argument(
        "--preview-csv",
        default="",
        help="Ruta opcional para exportar preview CSV del resultado (ej. data/tmp/registro_preview.csv).",
    )
    parser.add_argument(
        "--show-pending",
        type=int,
        default=50,
        help=(
            "Cantidad maxima de filas pendientes a mostrar en consola "
            "(0 para no mostrar). Default: 50."
        ),
    )
    parser.add_argument(
        "--pending-csv",
        default="",
        help=(
            "Ruta opcional para exportar SOLO filas pendientes con 10 campos "
            "(ej. data/tmp/pendientes_preview.csv)."
        ),
    )
    parser.add_argument(
        "--min-size-mb",
        type=float,
        default=50.0,
        help="Tamano minimo en MB para considerar un archivo pendiente procesable. Default: 50.",
    )
    parser.add_argument(
        "--max-size-mb",
        type=float,
        default=100.0,
        help="Tamano maximo en MB para considerar un archivo pendiente procesable. Default: 100.",
    )
    parser.add_argument(
        "--target-size-mb",
        type=float,
        default=67.0,
        help="Tamano objetivo en MB para ordenar primero los archivos mas cercanos al valor esperado. Default: 67.",
    )
    parser.add_argument(
        "--show-id-changes",
        type=int,
        default=50,
        help=(
            "Cantidad maxima de cambios de ID a mostrar en consola "
            "(0 para no mostrar). Default: 50."
        ),
    )
    parser.add_argument(
        "--id-changes-csv",
        default="",
        help=(
            "Ruta opcional para exportar cambios de ID detectados "
            "(ej. data/tmp/id_changes_preview.csv)."
        ),
    )
    args = parser.parse_args()

    load_environment()
    folder_raw = args.new_folder or require_env("GOOGLE_DETECCIONES_DRIVE_FOLDER_ID")
    folder_id = extract_folder_id(folder_raw)

    drive_service = build_drive_client()
    gsheet_client, sheet_url, worksheet_name = build_sheet_client()

    drive_df = list_drive_files(drive_service, folder_id)
    worksheet = gsheet_client.open_by_url(sheet_url).worksheet(worksheet_name)
    raw = worksheet.get_all_values()

    if not raw:
        registro_df = pd.DataFrame(columns=REQUIRED_COLUMNS)
    else:
        registro_df = pd.DataFrame.from_records(raw)
        registro_df.columns = registro_df.iloc[0]
        registro_df = registro_df.drop(registro_df.index[0]).reset_index(drop=True)

    merged_df, stats, id_changes_df = sync_registry(registro_df, drive_df)

    print("=== Resultado sincronizacion (dry-run) ===")
    print(f"Carpeta evaluada: {folder_id}")
    print(f"Archivos encontrados en carpeta: {stats.total_drive_files}")
    print(f"IDs actualizados por nombre: {stats.updated_ids}")
    print(f"Nuevos registros marcados Pendiente: {stats.new_pending_rows}")
    print(f"Registros sin cambios: {stats.unchanged_rows}")
    print(f"Total filas finales registro: {len(merged_df)}")

    if args.preview_csv:
        preview_path = (ROOT_DIR / args.preview_csv).resolve()
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        merged_df.to_csv(preview_path, index=False)
        print(f"Preview exportado en: {preview_path}")

    if args.show_id_changes > 0:
        print("\n=== Cambios de ID detectados ===")
        if id_changes_df.empty:
            print("No hay cambios de ID para esta corrida.")
        else:
            print(f"Total cambios de ID: {len(id_changes_df)}")
            print(id_changes_df.head(args.show_id_changes).to_string(index=False))

    if args.id_changes_csv:
        id_changes_path = (ROOT_DIR / args.id_changes_csv).resolve()
        id_changes_path.parent.mkdir(parents=True, exist_ok=True)
        id_changes_df.to_csv(id_changes_path, index=False)
        print(f"Cambios de ID exportados en: {id_changes_path}")

    pending_df, cutoff_date, excluded_by_size_df = build_processable_pending_preview(
        merged_df,
        min_size_mb=args.min_size_mb,
        max_size_mb=args.max_size_mb,
        target_size_mb=args.target_size_mb,
    )

    if args.show_pending > 0:
        print("\n=== Pendientes seleccionables ===")
        print(
            "Fecha corte (max date en Procesado): "
            + (cutoff_date.isoformat() if pd.notna(cutoff_date) else "None")
        )
        print(
            f"Filtro tamano aplicado: {args.min_size_mb} MB a {args.max_size_mb} MB "
            f"(objetivo {args.target_size_mb} MB)"
        )
        if pending_df.empty:
            print("No hay registros en estado Pendiente.")
        else:
            print(f"Total pendientes: {len(pending_df)}")
            manual_count = (
                pending_df[MANUAL_SELECTION_COLUMN]
                .astype(str)
                .str.strip()
                .str.lower()
                .isin({"1", "si", "sí", "s", "x", "true", "manual", "forzar"})
                .sum()
            )
            print(f"Pendientes seleccionados manualmente: {manual_count}")
            print(
                "Rango fechas pendientes: "
                f"{pending_df['date'].min()} -> {pending_df['date'].max()}"
            )
            print(pending_df.head(args.show_pending).to_string(index=False))
        if not excluded_by_size_df.empty:
            print(
                f"Pendientes excluidos por tamano fuera de rango: {len(excluded_by_size_df)}"
            )
            print(excluded_by_size_df.head(args.show_pending).to_string(index=False))

    if args.pending_csv:
        pending_path = (ROOT_DIR / args.pending_csv).resolve()
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_df.to_csv(pending_path, index=False)
        print(f"Pendientes exportados en: {pending_path}")

    if not args.apply:
        print("Dry-run completado. No se escribieron cambios en Google Sheets.")
        return 0

    worksheet.clear()
    gd.set_with_dataframe(worksheet=worksheet, dataframe=merged_df)
    print("Cambios aplicados en Google Sheets correctamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
