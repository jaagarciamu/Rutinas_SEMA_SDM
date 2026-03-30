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

from config.colombia_holidays import build_special_dates
from pipelines.detecciones_file_rules import normalize_name_key
from pipelines.fin_sem_file_rules import (
    REQUIRED_COLUMNS,
    build_fin_sem_pending_preview,
    classify_fin_sem_drive_files,
    ensure_required_columns,
    list_drive_files,
    sort_registry_for_output,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/bigquery",
]


class ConfigurationError(RuntimeError):
    """Error de configuracion para variables faltantes."""


@dataclass
class SyncStats:
    total_drive_files: int = 0
    updated_ids: int = 0
    unchanged_rows: int = 0
    pending_in_scope: int = 0
    duplicated_in_scope: int = 0
    fuera_alcance: int = 0
    no_cumple: int = 0


def load_environment() -> None:
    load_dotenv(ROOT_DIR / "config" / ".env")
    load_dotenv(ROOT_DIR / ".env")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Variable de entorno requerida no definida: {name}")
    return value


def require_any_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    raise ConfigurationError(
        f"No se encontro ninguna variable de entorno valida en: {', '.join(names)}"
    )


def parse_scopes(raw_scopes: str | None) -> list[str]:
    if not raw_scopes:
        return DEFAULT_SCOPES
    return [scope.strip() for scope in raw_scopes.split(",") if scope.strip()]


def parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "si", "on"}


def parse_int_csv_env(name: str) -> list[int]:
    value = require_env(name)
    return [int(item.strip()) for item in value.split(",") if item.strip()]


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
    sheet_url = require_any_env(
        "FIN_SEM_SEMA_REGISTRO_SHEET_URL",
        "GOOGLE_DETECCIONES_SHEET_URL",
    )
    worksheet_name = require_env("FIN_SEM_SEMA_REGISTRO_WORKSHEET")
    return client, sheet_url, worksheet_name


def load_sheet_records(worksheet) -> pd.DataFrame:
    raw = worksheet.get_all_values()
    if not raw:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    registro_df = pd.DataFrame.from_records(raw)
    registro_df.columns = registro_df.iloc[0]
    registro_df = registro_df.drop(registro_df.index[0]).reset_index(drop=True)
    registro_df = ensure_required_columns(registro_df)
    registro_df["size_in_MB"] = (
        registro_df["size_in_MB"].astype(str).str.replace(",", ".", regex=False)
    )
    registro_df["size_in_MB"] = pd.to_numeric(
        registro_df["size_in_MB"], errors="coerce"
    ).fillna(0.0)
    registro_df["num"] = pd.to_numeric(registro_df["num"], errors="coerce").fillna(0).astype(int)
    registro_df["mes"] = pd.to_numeric(registro_df["mes"], errors="coerce").fillna(0).astype(int)
    registro_df["date"] = pd.to_datetime(registro_df["date"], errors="coerce")
    return sort_registry_for_output(registro_df)


def sync_registry(
    registro_df: pd.DataFrame,
    drive_df: pd.DataFrame,
    special_dates,
    min_size_mb: float,
    max_size_mb: float,
    target_size_mb: float,
) -> tuple[pd.DataFrame, SyncStats, pd.DataFrame]:
    previous_registry = ensure_required_columns(registro_df.copy())
    merged_df = classify_fin_sem_drive_files(
        drive_df=drive_df,
        existing_registry=previous_registry,
        special_dates=special_dates,
        min_size_mb=min_size_mb,
        max_size_mb=max_size_mb,
        target_size_mb=target_size_mb,
    )
    stats = SyncStats(total_drive_files=len(merged_df))
    stats.pending_in_scope = int(merged_df["Estado"].eq("Pendiente").sum())
    stats.duplicated_in_scope = int(merged_df["Estado"].eq("Duplicado").sum())
    stats.fuera_alcance = int(merged_df["Estado"].eq("Fuera_Alcance").sum())
    stats.no_cumple = int(merged_df["Estado"].eq("No_Cumple").sum())

    id_changes: list[dict[str, object]] = []
    if not previous_registry.empty:
        prev = previous_registry.copy()
        prev["_name_key"] = prev["name"].astype(str).map(normalize_name_key)
        merged = merged_df.copy()
        merged["_name_key"] = merged["name"].astype(str).map(normalize_name_key)
        prev_ids = prev.set_index("_name_key")["id"]
        existing_keys = set(prev["_name_key"])

        for _, row in merged.iterrows():
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

    id_changes_df = pd.DataFrame(
        id_changes,
        columns=["name", "id_old", "id_new", "date", "mes", "estado_actual"],
    )
    return sort_registry_for_output(merged_df), stats, id_changes_df


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sincroniza y previsualiza el registro de FIN SEM SEMA usando la misma "
            "carpeta historica de detecciones y solo dejando habilitadas fechas de fin "
            "de semana/festivos."
        )
    )
    parser.add_argument(
        "--new-folder",
        default="",
        help=(
            "ID o URL de la carpeta historica. Si se omite, usa "
            "GOOGLE_DETECCIONES_DRIVE_FOLDER_ID."
        ),
    )
    parser.add_argument("--apply", action="store_true", help="Escribe cambios en Google Sheets.")
    parser.add_argument("--preview-csv", default="", help="Exporta preview completo del registro.")
    parser.add_argument("--pending-csv", default="", help="Exporta solo pendientes FIN SEM.")
    parser.add_argument("--id-changes-csv", default="", help="Exporta cambios de ID detectados.")
    parser.add_argument("--show-pending", type=int, default=50)
    parser.add_argument("--show-id-changes", type=int, default=50)
    parser.add_argument("--min-size-mb", type=float, default=50.0)
    parser.add_argument("--max-size-mb", type=float, default=100.0)
    parser.add_argument("--target-size-mb", type=float, default=67.0)
    args = parser.parse_args()

    load_environment()
    folder_raw = args.new_folder or require_any_env(
        "GOOGLE_DETECCIONES_DRIVE_FOLDER_ID",
        "FIN_SEM_SEMA_HISTORICO_DRIVE_FOLDER_ID",
    )
    folder_id = extract_folder_id(folder_raw)
    years = parse_int_csv_env("FIN_SEM_SEMA_YEARS")
    include_friday = parse_bool_env("FIN_SEM_SEMA_INCLUDE_FRIDAY", default=True)
    special_dates = build_special_dates(years, include_friday)

    drive_service = build_drive_client()
    gsheet_client, sheet_url, worksheet_name = build_sheet_client()
    drive_df = list_drive_files(drive_service, folder_id)
    worksheet = gsheet_client.open_by_url(sheet_url).worksheet(worksheet_name)
    registro_df = load_sheet_records(worksheet)

    merged_df, stats, id_changes_df = sync_registry(
        registro_df=registro_df,
        drive_df=drive_df,
        special_dates=special_dates,
        min_size_mb=args.min_size_mb,
        max_size_mb=args.max_size_mb,
        target_size_mb=args.target_size_mb,
    )
    pending_df, cutoff_date = build_fin_sem_pending_preview(merged_df, special_dates)

    print("=== Resultado sincronizacion FIN SEM (dry-run) ===")
    print(f"Carpeta evaluada: {folder_id}")
    print(f"Archivos encontrados en carpeta: {stats.total_drive_files}")
    print(f"IDs actualizados por nombre: {stats.updated_ids}")
    print(f"Pendientes FIN SEM: {stats.pending_in_scope}")
    print(f"Duplicados FIN SEM: {stats.duplicated_in_scope}")
    print(f"Fuera_Alcance: {stats.fuera_alcance}")
    print(f"No_Cumple: {stats.no_cumple}")
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

    if args.show_pending > 0:
        print("\n=== Pendientes FIN SEM ===")
        print(
            "Fecha corte (max date en Procesado): "
            + (cutoff_date.isoformat() if pd.notna(cutoff_date) else "None")
        )
        if pending_df.empty:
            print("No hay registros pendientes dentro del alcance FIN SEM.")
        else:
            print(f"Total pendientes: {len(pending_df)}")
            print(
                "Rango fechas pendientes: "
                f"{pending_df['date'].min()} -> {pending_df['date'].max()}"
            )
            print(pending_df.head(args.show_pending).to_string(index=False))

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
