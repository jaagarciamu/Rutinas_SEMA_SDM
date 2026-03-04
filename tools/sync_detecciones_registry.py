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
]
DETECCIONES_NAME_PATTERN = (
    r"Detektor-Messquerschnitt|Detector-Measurement Point-Traffic Data - Processed"
)


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

            size_mb = round(int(row.get("size", 0)) / 100000000, 2)
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

    date_token = df["name"].str.extract(r"([0-9]{6})", expand=True).iloc[:, 0]
    df["date"] = pd.to_datetime(date_token, format="%y%m%d", errors="coerce")
    df["mes"] = df["date"].dt.strftime("%y%m")
    df["num"] = "0"
    return df


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def ensure_required_columns(registro: pd.DataFrame) -> pd.DataFrame:
    for column in REQUIRED_COLUMNS:
        if column not in registro.columns:
            registro[column] = ""

    ordered_columns = REQUIRED_COLUMNS + [
        column for column in registro.columns if column not in REQUIRED_COLUMNS
    ]
    return registro[ordered_columns]


def filter_processable_registry_scope(registro_df: pd.DataFrame) -> pd.DataFrame:
    """Mantiene solo archivos que el pipeline 01 puede procesar."""
    df = ensure_required_columns(registro_df.copy())
    df["name"] = df["name"].astype(str)
    df["type_of_file"] = df["type_of_file"].astype(str)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df[
        (df["type_of_file"] == "text/csv")
        & df["name"].str.contains(
            DETECCIONES_NAME_PATTERN,
            case=False,
            na=False,
            regex=True,
        )
    ]
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
) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Replica el criterio de selección de 01_DETECCIONES_SEMA.py."""
    df = ensure_required_columns(registro.copy())
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["name"] = df["name"].astype(str)
    df["type_of_file"] = df["type_of_file"].astype(str)
    df["Estado"] = df["Estado"].astype(str)

    df = df[
        (df["type_of_file"] == "text/csv")
        & df["name"].str.contains(
            DETECCIONES_NAME_PATTERN,
            case=False,
            na=False,
            regex=True,
        )
    ]

    pending = df[df["Estado"].str.lower() == "pendiente"].copy()
    processed = df[df["Estado"].str.lower() == "procesado"].copy()

    max_processed_date = processed["date"].max()
    if pd.notna(max_processed_date):
        pending = pending[pending["date"] > max_processed_date]

    pending = pending[REQUIRED_COLUMNS].sort_values(
        by="date", ascending=False, na_position="last"
    )
    return pending, max_processed_date


def sync_registry(
    registro: pd.DataFrame, drive_df: pd.DataFrame
) -> tuple[pd.DataFrame, SyncStats, pd.DataFrame]:
    drive_df = drive_df.copy()
    drive_df["name"] = drive_df["name"].astype(str)
    drive_df["type_of_file"] = drive_df["type_of_file"].astype(str)
    drive_df = drive_df[
        (drive_df["type_of_file"] == "text/csv")
        & drive_df["name"].str.contains(
            DETECCIONES_NAME_PATTERN,
            case=False,
            na=False,
            regex=True,
        )
    ].copy()

    stats = SyncStats(total_drive_files=len(drive_df))
    registro = dedupe_registry_by_name(filter_processable_registry_scope(registro))
    id_changes: list[dict[str, object]] = []

    if registro.empty:
        registro = pd.DataFrame(columns=REQUIRED_COLUMNS)

    registro["_name_key"] = registro["name"].map(normalize_name)
    drive_df = drive_df.copy()
    drive_df["_name_key"] = drive_df["name"].map(normalize_name)

    name_to_index: dict[str, int] = {}
    for idx, key in registro["_name_key"].items():
        if key and key not in name_to_index:
            name_to_index[key] = idx

    for _, drive_row in drive_df.iterrows():
        key = drive_row["_name_key"]
        if not key:
            continue

        existing_idx = name_to_index.get(key)
        if existing_idx is not None:
            old_id = str(registro.at[existing_idx, "id"])
            new_id = str(drive_row["id"])

            if old_id != new_id:
                id_changes.append(
                    {
                        "name": drive_row["name"],
                        "id_old": old_id,
                        "id_new": new_id,
                        "date": drive_row.get("date"),
                        "mes": drive_row.get("mes"),
                        "estado_actual": registro.at[existing_idx, "Estado"],
                    }
                )
                registro.at[existing_idx, "id"] = new_id
                registro.at[existing_idx, "size_in_MB"] = drive_row["size_in_MB"]
                registro.at[existing_idx, "creation"] = drive_row["creation"]
                registro.at[existing_idx, "last_modification"] = drive_row["last_modification"]
                registro.at[existing_idx, "type_of_file"] = drive_row["type_of_file"]
                if pd.notna(drive_row.get("date")):
                    registro.at[existing_idx, "date"] = drive_row["date"]
                if pd.notna(drive_row.get("mes")):
                    registro.at[existing_idx, "mes"] = drive_row["mes"]
                stats.updated_ids += 1
            else:
                stats.unchanged_rows += 1
            continue

        new_row = {
            "size_in_MB": drive_row["size_in_MB"],
            "id": drive_row["id"],
            "name": drive_row["name"],
            "creation": drive_row["creation"],
            "last_modification": drive_row["last_modification"],
            "type_of_file": drive_row["type_of_file"],
            "date": drive_row.get("date"),
            "mes": drive_row.get("mes"),
            "Estado": "Pendiente",
            "num": "0",
        }
        registro = pd.concat([registro, pd.DataFrame([new_row])], ignore_index=True)
        name_to_index[key] = len(registro) - 1
        stats.new_pending_rows += 1

    registro = registro.drop(columns=["_name_key"], errors="ignore")

    registro = sort_registry_for_output(registro)
    registro = dedupe_registry_by_name(registro)
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

    pending_df, cutoff_date = build_processable_pending_preview(merged_df)

    if args.show_pending > 0:
        print("\n=== Pendientes (10 campos) ===")
        print(
            "Fecha corte (max date en Procesado): "
            + (cutoff_date.isoformat() if pd.notna(cutoff_date) else "None")
        )
        if pending_df.empty:
            print("No hay registros en estado Pendiente.")
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
