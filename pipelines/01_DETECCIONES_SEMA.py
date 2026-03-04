from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import gspread
import gspread_dataframe as gd
import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from pipelines.alerting import record_pipeline_failure, record_pipeline_success
from sqlalchemy import inspect, text
from sqlalchemy.dialects.oracle import FLOAT, NUMBER, TIMESTAMP, VARCHAR2
from sqlalchemy.engine import create_engine
from sqlalchemy.exc import SQLAlchemyError

pd.options.mode.chained_assignment = None  # default='warn'

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
ORACLE_FLOAT = FLOAT(binary_precision=126)

logger = logging.getLogger("detecciones_sema")


class ConfigurationError(RuntimeError):
    """Error funcional para variables de entorno faltantes o inválidas."""


class DataAvailabilityError(RuntimeError):
    """Error de negocio cuando no existe data mínima esperada."""


@dataclass
class SyncStats:
    total_drive_files: int = 0
    updated_ids: int = 0
    new_pending_rows: int = 0
    unchanged_rows: int = 0


def setup_logging() -> None:
    if logger.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Variable de entorno requerida no definida: {name}")
    return value


def parse_scopes(raw_scopes: str | None) -> list[str]:
    if not raw_scopes:
        return DEFAULT_SCOPES
    return [scope.strip() for scope in raw_scopes.split(",") if scope.strip()]


def load_environment() -> None:
    """Carga variables desde config/.env y luego .env en raíz (si existe)."""
    load_dotenv(ROOT_DIR / "config" / ".env")
    load_dotenv(ROOT_DIR / ".env")


def build_oracle_engine():
    user = require_env("ORACLE_USER")
    password = require_env("ORACLE_PASSWORD")
    dsn = require_env("ORACLE_DSN")
    safe_password = quote_plus(password)
    engine_url = f"oracle+oracledb://{user}:{safe_password}@{dsn}"
    return create_engine(engine_url, thick_mode={})


def build_google_clients(scopes: list[str]):
    credentials_path = require_env("GOOGLE_APPLICATION_CREDENTIALS")
    credentials_file = (ROOT_DIR / credentials_path).resolve()
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_file), scopes=scopes
    )
    auth_req = Request()
    credentials.refresh(auth_req)
    access_token = credentials.token

    gspread_client = gspread.service_account(filename=str(credentials_file))
    drive_service = build("drive", "v3", credentials=credentials)
    return gspread_client, drive_service, access_token


def safe_to_float(series):
    return (
        series.astype(str)
        .str.strip()
        .str.replace(",", ".", regex=False)
        .where(lambda x: x.str.match(r"^-?\d+(\.\d+)?$"))
        .astype(float)
    )


def ensure_required_columns(registro: pd.DataFrame) -> pd.DataFrame:
    for column in REQUIRED_COLUMNS:
        if column not in registro.columns:
            registro[column] = ""
    ordered_columns = REQUIRED_COLUMNS + [
        column for column in registro.columns if column not in REQUIRED_COLUMNS
    ]
    return registro[ordered_columns]


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def filter_processable_registry_scope(registro_df: pd.DataFrame) -> pd.DataFrame:
    """Mantiene solo archivos que este pipeline puede procesar."""
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


def load_registry_sheet(gspread_client: gspread.Client, sheet_url: str, sheet_tab: str) -> pd.DataFrame:
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(sheet_tab)
    raw = worksheet.get_all_values()
    if not raw:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    registro_df = pd.DataFrame.from_records(raw)
    registro_df.columns = registro_df.iloc[0]
    registro_df = registro_df.drop(registro_df.index[0]).reset_index(drop=True)
    return ensure_required_columns(registro_df)


def write_registry_sheet(
    gspread_client: gspread.Client,
    sheet_url: str,
    sheet_tab: str,
    registro_df: pd.DataFrame,
) -> None:
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(sheet_tab)
    registro_df = sort_registry_for_output(registro_df)
    worksheet.clear()
    gd.set_with_dataframe(worksheet=worksheet, dataframe=registro_df)


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
            data.append(
                [
                    round(int(row.get("size", 0)) / 100000000, 2),
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

    drive_df = pd.DataFrame(
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

    if drive_df.empty:
        drive_df["date"] = pd.Series(dtype="datetime64[ns]")
        drive_df["mes"] = pd.Series(dtype="object")
        drive_df["num"] = pd.Series(dtype="object")
        return drive_df

    date_token = drive_df["name"].str.extract(r"([0-9]{6})", expand=True).iloc[:, 0]
    drive_df["date"] = pd.to_datetime(date_token, format="%y%m%d", errors="coerce")
    drive_df["mes"] = drive_df["date"].dt.strftime("%y%m")
    drive_df["num"] = "0"
    return drive_df


def sync_registry_by_name(registro_df: pd.DataFrame, drive_df: pd.DataFrame) -> tuple[pd.DataFrame, SyncStats]:
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
    registro_df = dedupe_registry_by_name(filter_processable_registry_scope(registro_df))

    if registro_df.empty:
        registro_df = pd.DataFrame(columns=REQUIRED_COLUMNS)

    registro_df["_name_key"] = registro_df["name"].map(normalize_name)
    drive_df = drive_df.copy()
    drive_df["_name_key"] = drive_df["name"].map(normalize_name)

    name_to_index: dict[str, int] = {}
    for idx, key in registro_df["_name_key"].items():
        if key and key not in name_to_index:
            name_to_index[key] = idx

    for _, drive_row in drive_df.iterrows():
        key = drive_row["_name_key"]
        if not key:
            continue

        existing_idx = name_to_index.get(key)
        if existing_idx is not None:
            old_id = str(registro_df.at[existing_idx, "id"])
            new_id = str(drive_row["id"])
            if old_id != new_id:
                registro_df.at[existing_idx, "id"] = new_id
                registro_df.at[existing_idx, "size_in_MB"] = drive_row["size_in_MB"]
                registro_df.at[existing_idx, "creation"] = drive_row["creation"]
                registro_df.at[existing_idx, "last_modification"] = drive_row["last_modification"]
                registro_df.at[existing_idx, "type_of_file"] = drive_row["type_of_file"]
                if pd.notna(drive_row.get("date")):
                    registro_df.at[existing_idx, "date"] = drive_row["date"]
                if pd.notna(drive_row.get("mes")):
                    registro_df.at[existing_idx, "mes"] = drive_row["mes"]
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
        registro_df = pd.concat([registro_df, pd.DataFrame([new_row])], ignore_index=True)
        name_to_index[key] = len(registro_df) - 1
        stats.new_pending_rows += 1

    registro_df = registro_df.drop(columns=["_name_key"], errors="ignore")
    registro_df = sort_registry_for_output(registro_df)
    registro_df = dedupe_registry_by_name(registro_df)
    registro_df = sort_registry_for_output(registro_df)
    return registro_df.reset_index(drop=True), stats


def build_processable_pending_preview(registro_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    df = filter_processable_registry_scope(registro_df)
    df["Estado"] = df["Estado"].astype(str)
    pending = df[df["Estado"].str.lower() == "pendiente"].copy()
    processed = df[df["Estado"].str.lower() == "procesado"].copy()
    max_processed_date = processed["date"].max()
    if pd.notna(max_processed_date):
        pending = pending[pending["date"] > max_processed_date]
    return pending.sort_values(by="date", ascending=False, na_position="last"), max_processed_date


def apply_processed_updates(registro_df: pd.DataFrame, processed_updates: pd.DataFrame) -> pd.DataFrame:
    """Actualiza estado/num en la misma fila del archivo (sin duplicar)."""
    registro_df = ensure_required_columns(registro_df.copy())
    updates = processed_updates.copy()
    updates = updates[["name", "id", "num"]]
    updates["Estado"] = "Procesado"
    updates["_name_key"] = updates["name"].map(normalize_name)

    registro_df["_name_key"] = registro_df["name"].map(normalize_name)
    updates_map = updates.set_index("_name_key")
    common_keys = registro_df["_name_key"].isin(updates_map.index)

    registro_df.loc[common_keys, "Estado"] = "Procesado"
    registro_df.loc[common_keys, "num"] = registro_df.loc[common_keys, "_name_key"].map(
        updates_map["num"]
    )
    registro_df.loc[common_keys, "id"] = registro_df.loc[common_keys, "_name_key"].map(
        updates_map["id"]
    )

    registro_df = registro_df.drop(columns=["_name_key"], errors="ignore")
    return dedupe_registry_by_name(registro_df)


def write_det_reg_table(engine, registro_df: pd.DataFrame, dtype_reg: dict) -> None:
    """Evita perder la tabla por fallos de `replace`."""
    table_name = "det_reg_sema"
    try:
        if inspect(engine).has_table(table_name):
            with engine.begin() as connection:
                connection.execute(text("TRUNCATE TABLE det_reg_sema"))
        registro_df.to_sql(
            name=table_name,
            con=engine,
            if_exists="append",
            index=False,
            dtype=dtype_reg,
        )
    except SQLAlchemyError:
        logger.exception("Error escribiendo DET_REG_SEMA")
        raise


def main() -> None:
    load_environment()
    setup_logging()

    scopes = parse_scopes(os.getenv("GOOGLE_SCOPES"))
    gspread_client, drive_service, access_token = build_google_clients(scopes)

    sheet_url = require_env("GOOGLE_DETECCIONES_SHEET_URL")
    sheet_tab = require_env("GOOGLE_DETECCIONES_WORKSHEET")
    drive_folder_id = require_env("GOOGLE_DETECCIONES_DRIVE_FOLDER_ID")

    registro_sheet = load_registry_sheet(gspread_client, sheet_url, sheet_tab)
    drive_df = list_drive_files(drive_service, drive_folder_id)
    Registro, sync_stats = sync_registry_by_name(registro_sheet, drive_df)
    Registro = dedupe_registry_by_name(filter_processable_registry_scope(Registro))

    if sync_stats.updated_ids > 0 or sync_stats.new_pending_rows > 0:
        write_registry_sheet(gspread_client, sheet_url, sheet_tab, Registro)
        logger.info(
            "Registro sincronizado por nombre: files=%s ids_actualizados=%s nuevos_pendientes=%s",
            sync_stats.total_drive_files,
            sync_stats.updated_ids,
            sync_stats.new_pending_rows,
        )
    else:
        # Mantener hoja normalizada (sin duplicados ni archivos fuera de alcance del pipeline).
        write_registry_sheet(gspread_client, sheet_url, sheet_tab, Registro)
        logger.info(
            "Registro sin cambios por sincronizacion: files=%s sin_cambios=%s",
            sync_stats.total_drive_files,
            sync_stats.unchanged_rows,
        )

    processable_pending, cutoff_date = build_processable_pending_preview(Registro)
    logger.info(
        "Pendientes potenciales segun criterio pipeline: total=%s cutoff_procesado=%s",
        len(processable_pending),
        cutoff_date.isoformat() if pd.notna(cutoff_date) else "None",
    )

    cleared_df = drive_df.copy()
    cleared_df = cleared_df.sort_values(by=["date"], ascending=False)
    cleared_df = cleared_df[cleared_df["type_of_file"] == "text/csv"]
    cleared_df = cleared_df[
        cleared_df["name"].str.contains(
            DETECCIONES_NAME_PATTERN,
            case=False,
            na=False,
            regex=True,
        )
    ]
    cleared_df = pd.merge(
        cleared_df, Registro[["id", "Estado"]], left_on="id", right_on="id", how="left"
    ).fillna("Pendiente")

    yesterday_date = (datetime.now() - timedelta(days=1)).date()
    has_yesterday = (cleared_df["date"].dt.date == yesterday_date).any()
    if not has_yesterday:
        raise DataAvailabilityError(
            f"No se encontró archivo de detecciones del día anterior ({yesterday_date})"
        )

    select_data = cleared_df[cleared_df["Estado"] == "Pendiente"]

    processed_data = cleared_df[cleared_df["Estado"] == "Procesado"].copy()
    processed_data["date"] = pd.to_datetime(processed_data["date"])
    max_date = processed_data["date"].max()
    if pd.notna(max_date):
        select_data = select_data[select_data["date"] > max_date]

    MESES = select_data["mes"].unique().tolist()
    ids = select_data["id"].tolist()

    ids2 = []
    data = []
    registros_count = []

    numeric_cols = [
        "processed_all_vol",
        "processed_all_occ",
        "processed_all_spd",
        "processed_car_vol",
        "processed_car_occ",
        "processed_car_spd",
        "processed_truck_vol",
        "processed_truck_occ",
        "processed_truck_spd",
    ]

    if not ids:
        logger.info("No hay registros nuevos")
        return

    engine = build_oracle_engine()

    for mes in MESES:
        seleccion = select_data[select_data["mes"] == mes]
        ids2 = seleccion["id"].tolist()
        for file_id in ids2:
            file_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
            res = requests.get(
                file_url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=120,
            )
            res.raise_for_status()
            df = pd.read_csv(io.StringIO(res.text), sep=";", encoding="latin1")

            present_numeric_cols = [col for col in numeric_cols if col in df.columns]
            for col in present_numeric_cols:
                df[col] = safe_to_float(df[col])
            if present_numeric_cols:
                df[present_numeric_cols] = df[present_numeric_cols].fillna(0)

            df = df.iloc[:, :8].drop(["UTC"], axis=1)
            df = df.set_axis(
                [
                    "Nombre",
                    "Tiempo",
                    "Sensor",
                    "Deteccion",
                    "Estado",
                    "Num_deteccion",
                    "Ocupacion",
                ],
                axis="columns",
            )
            df["Deteccion"] = pd.to_numeric(df["Deteccion"], errors="coerce")
            df["Ocupacion"] = pd.to_numeric(df["Ocupacion"], errors="coerce")
            df["Num_deteccion"] = df["Num_deteccion"].astype("int64")
            df[["Deteccion", "Num_deteccion", "Ocupacion"]] = (
                df[["Deteccion", "Num_deteccion", "Ocupacion"]]
                .replace([np.inf, -np.inf], 0)
                .fillna(0)
            )
            df = df.query("(Num_deteccion > 8) & ~(Ocupacion <= 0.01 & Deteccion > 1)")
            df["Tiempo"] = pd.to_datetime(df["Tiempo"], format="%d.%m.%Y %H:%M:%S") - timedelta(
                hours=0, minutes=1
            )
            df.loc[:, "IG"] = (
                df["Nombre"]
                .str.extract("(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)", expand=True)
                .iloc[:, 1]
                .str.extract("(\\d+)")
                .astype(str)
            )
            df.loc[:, "EXT"] = (
                df["Nombre"]
                .str.extract("(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)", expand=True)
                .iloc[:, 2]
                .str.extract("(\\d+)")
                .astype(str)
            )
            df.loc[:, "Movimiento"] = df["Nombre"].str.extract(
                "(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)", expand=True
            ).iloc[:, 3]
            df.loc[:, "Acceso"] = (
                df["Sensor"]
                .str.extract("(\\d+(?=.))")
                .iloc[:, 0]
                .str.extract(r"([0-9]+)")
                .infer_objects()
                .fillna(0)
                .astype(str)
            )
            df.loc[df["Acceso"] == "0", "Acceso"] = (
                df["Sensor"].str.extract("(\\d+)").iloc[:, 0]
            )
            df.loc[:, "Tipo_sensor"] = df["Sensor"].str.extract("(^[A-Z])").iloc[:, 0]
            df = df.drop(["Estado"], axis=1)
            df.loc[:, "Deteccion"] = np.round((df["Deteccion"] / 4), 0).astype(int)
            df.loc[:, "Brecha"] = np.where(
                (df["Deteccion"] == 0) | (df["Ocupacion"] == 0),
                0,
                np.round(
                    (900 / df["Deteccion"])
                    - ((900 * (df["Ocupacion"] / 100)) / df["Deteccion"]),
                    3,
                ),
            )
            df.loc[:, "Velocidad"] = np.where(
                (df["Deteccion"] == 0) | (df["Ocupacion"] == 0),
                0,
                np.round(6 / ((900 * (df["Ocupacion"] / 100)) / df["Deteccion"]) * 3.6, 3),
            )
            df.loc[:, "Ocupacion"] = np.round(df["Ocupacion"], 4)
            df = df[
                [
                    "Nombre",
                    "Tiempo",
                    "IG",
                    "EXT",
                    "Movimiento",
                    "Acceso",
                    "Tipo_sensor",
                    "Sensor",
                    "Num_deteccion",
                    "Deteccion",
                    "Ocupacion",
                    "Brecha",
                    "Velocidad",
                ]
            ]
            data.append(df)

            num = len(df["EXT"])
            registros_counta = pd.DataFrame({"id": [file_id], "num": [num]})
            registros_count.append(registros_counta)
            logger.info("Archivo procesado id=%s", file_id)

        num_reg = pd.concat(registros_count, axis=0)
        df_mes = pd.concat(data, axis=0)

        # Estimación Base de Datos Diaria.
        det_diarias = df_mes.copy()
        det_diarias = det_diarias.drop(["Nombre", "Movimiento", "Tipo_sensor"], axis=1)
        det_diarias["Mes"] = (det_diarias["Tiempo"].dt.strftime("%m")) + " " + (
            det_diarias["Tiempo"].dt.strftime("%B")
        )
        det_diarias["Ano"] = det_diarias["Tiempo"].dt.year.astype(str)

        det_diarias = det_diarias.set_index(det_diarias["Tiempo"]).drop(["Tiempo"], axis=1)
        det_diarias = np.round(
            det_diarias.groupby(["Mes", "Ano", "IG", "EXT", "Acceso"]).resample("D").agg(
                Num_deteccion=("Num_deteccion", "sum"),
                Deteccion=("Deteccion", "sum"),
                Ocupacion=("Ocupacion", "mean"),
                Brecha=("Brecha", "mean"),
                Velocidad=("Velocidad", "mean"),
            ),
            3,
        )
        det_diarias = det_diarias.reset_index()

        det_general = det_diarias.set_index(det_diarias["Tiempo"]).drop(["Tiempo"], axis=1)
        det_general = np.round(
            det_general.groupby(["Mes", "Ano"]).resample("D").agg(
                Num_deteccion=("Num_deteccion", "sum"),
                Deteccion=("Deteccion", "sum"),
                Ocupacion=("Ocupacion", "mean"),
                Brecha=("Brecha", "mean"),
                Velocidad=("Velocidad", "mean"),
            ),
            3,
        )
        det_general = det_general.reset_index()
        det_general.loc[:, "EXT"] = "DIARIO"
        det_general.loc[:, "IG"] = "DIARIO"
        det_general.loc[:, "Acceso"] = "DIARIO"
        det_general = det_general[
            [
                "Mes",
                "Ano",
                "IG",
                "EXT",
                "Acceso",
                "Tiempo",
                "Num_deteccion",
                "Deteccion",
                "Ocupacion",
                "Brecha",
                "Velocidad",
            ]
        ]

        det_general2 = det_diarias.set_index(det_diarias["Tiempo"]).drop(["Tiempo"], axis=1)
        det_general2 = np.round(
            det_general2.groupby(["Mes", "Ano", "EXT"]).resample("D").agg(
                Num_deteccion=("Num_deteccion", "sum"),
                Deteccion=("Deteccion", "sum"),
                Ocupacion=("Ocupacion", "mean"),
                Brecha=("Brecha", "mean"),
                Velocidad=("Velocidad", "mean"),
            ),
            3,
        )
        det_general2 = det_general2.reset_index()
        det_general2.loc[:, "IG"] = "DIARIO_EXT"
        det_general2.loc[:, "Acceso"] = "DIARIO_EXT"
        det_general2 = det_general2[
            [
                "Mes",
                "Ano",
                "IG",
                "EXT",
                "Acceso",
                "Tiempo",
                "Num_deteccion",
                "Deteccion",
                "Ocupacion",
                "Brecha",
                "Velocidad",
            ]
        ]

        det_diarias = pd.concat([det_diarias, det_general, det_general2])
        det_diarias["Mes_Ano"] = det_diarias["Mes"] + " " + det_diarias["Ano"]

        # Estimación Base de Datos 15 min transaccional
        df_resum = df_mes.copy()
        df_resum = df_resum.dropna(subset=["Ocupacion"])
        df_resum["Dia_sem"] = (((df_resum["Tiempo"].dt.dayofweek) + 1).astype(str)) + " " + (
            df_resum["Tiempo"].dt.day_name()
        )
        df_resum["Hora"] = df_resum["Tiempo"].dt.hour
        df_resum["Minuto"] = (df_resum["Tiempo"].dt.time).astype(str)
        df_resum["Fecha"] = df_resum["Tiempo"].dt.date
        df_resum = df_resum.reset_index()
        df_resum = df_resum.drop(["Nombre", "index"], axis=1)

        # Estimación Base de Datos Hora transaccional
        df_hora = df_mes.copy()
        df_hora = df_hora.set_index(df_hora["Tiempo"]).drop(["Tiempo"], axis=1)
        df_hora = np.round(
            df_hora.groupby(["IG", "EXT", "Movimiento", "Acceso", "Tipo_sensor", "Sensor"]).resample(
                "h"
            ).agg(
                Num_deteccion=("Num_deteccion", "sum"),
                Deteccion=("Deteccion", "sum"),
                Ocupacion=("Ocupacion", "mean"),
                Brecha=("Brecha", "mean"),
                Velocidad=("Velocidad", "mean"),
            ),
            3,
        )
        df_hora = df_hora.reset_index()
        df_hora = df_hora.dropna(subset=["Ocupacion"])
        df_hora["Dia_sem"] = (((df_hora["Tiempo"].dt.dayofweek) + 1).astype(str)) + " " + (
            df_hora["Tiempo"].dt.day_name()
        )
        df_hora["Hora"] = df_hora["Tiempo"].dt.hour
        df_hora["Fecha"] = df_hora["Tiempo"].dt.date
        df_hora = df_hora.set_index(df_hora["Tiempo"]).drop(["Tiempo"], axis=1)
        df_hora = df_hora.reset_index()

        # Actualización base de datos historicas mensuales
        dtype_det = {
            "Nombre": VARCHAR2(50),
            "Tiempo": TIMESTAMP,
            "IG": VARCHAR2(50),
            "EXT": VARCHAR2(50),
            "Movimiento": VARCHAR2(20),
            "Acceso": VARCHAR2(50),
            "Tipo_sensor": VARCHAR2(5),
            "Sensor": VARCHAR2(20),
            "Num_deteccion": NUMBER,
            "Deteccion": NUMBER,
            "Ocupacion": ORACLE_FLOAT,
            "Brecha": ORACLE_FLOAT,
            "Velocidad": ORACLE_FLOAT,
        }
        df_mes.to_sql(
            name=f"det_15m_sema_{mes}",
            con=engine,
            if_exists="append",
            index=False,
            dtype=dtype_det,
        )

        logger.info("Carga mensual creada: det_15m_sema_%s", mes)

        # Actualización base de datos historicas diario
        dtype_dia = {
            "Tiempo": TIMESTAMP,
            "Ano": VARCHAR2(20),
            "Mes": VARCHAR2(20),
            "IG": VARCHAR2(20),
            "EXT": VARCHAR2(50),
            "Acceso": VARCHAR2(20),
            "Num_deteccion": NUMBER,
            "Deteccion": NUMBER,
            "Ocupacion": ORACLE_FLOAT,
            "Brecha": ORACLE_FLOAT,
            "Velocidad": ORACLE_FLOAT,
            "Mes_Ano": VARCHAR2(20),
        }
        det_diarias.to_sql(
            name="det_diar_sema",
            con=engine,
            if_exists="append",
            index=False,
            dtype=dtype_dia,
        )

        # Actualización base de Datos 15 min transaccional
        dtype_resum_15m = {
            "Tiempo": TIMESTAMP,
            "IG": VARCHAR2(50),
            "EXT": VARCHAR2(50),
            "Movimiento": VARCHAR2(20),
            "Acceso": VARCHAR2(50),
            "Tipo_sensor": VARCHAR2(5),
            "Sensor": VARCHAR2(20),
            "Num_deteccion": NUMBER,
            "Deteccion": NUMBER,
            "Ocupacion": ORACLE_FLOAT,
            "Brecha": ORACLE_FLOAT,
            "Velocidad": ORACLE_FLOAT,
            "Dia_sem": VARCHAR2(20),
            "Hora": NUMBER,
            "Minuto": VARCHAR2(50),
            "Fecha": TIMESTAMP,
        }
        df_resum.to_sql(
            name="det_resum_sema_15m",
            con=engine,
            if_exists="append",
            index=False,
            dtype=dtype_resum_15m,
        )

        # Actualización base de Datos Hora transaccional
        dtype_resum_hora = {
            "Tiempo": TIMESTAMP,
            "IG": VARCHAR2(50),
            "EXT": VARCHAR2(50),
            "Movimiento": VARCHAR2(20),
            "Acceso": VARCHAR2(50),
            "Tipo_sensor": VARCHAR2(5),
            "Sensor": VARCHAR2(20),
            "Num_deteccion": NUMBER,
            "Deteccion": NUMBER,
            "Ocupacion": ORACLE_FLOAT,
            "Brecha": ORACLE_FLOAT,
            "Velocidad": ORACLE_FLOAT,
            "Dia_sem": VARCHAR2(20),
            "Hora": NUMBER,
            "Fecha": TIMESTAMP,
        }
        df_hora.to_sql(
            name="det_resum_sema_hora",
            con=engine,
            if_exists="append",
            index=False,
            dtype=dtype_resum_hora,
        )

        data = []

    processed_updates = pd.merge(
        select_data.copy(), num_reg[["id", "num"]], left_on="id", right_on="id", how="left"
    )
    registro_n = apply_processed_updates(Registro, processed_updates)
    registro_n = filter_processable_registry_scope(registro_n)
    registro_n = dedupe_registry_by_name(registro_n)
    registro_n["date"] = pd.to_datetime(registro_n["date"])
    registro_n["size_in_MB"] = pd.to_numeric(registro_n["size_in_MB"], errors="coerce")
    registro_n["num"] = registro_n["num"].astype(str)
    registro_n = sort_registry_for_output(registro_n)

    # Se actualiza la base de datos de los registros procesados en Google Sheets.
    write_registry_sheet(gspread_client, sheet_url, sheet_tab, registro_n)

    # Actualización base registros
    dtype_reg = {
        "size_in_MB": ORACLE_FLOAT,
        "id": VARCHAR2(50),
        "name": VARCHAR2(200),
        "creation": VARCHAR2(100),
        "last_modification": VARCHAR2(100),
        "type_of_file": VARCHAR2(50),
        "date": TIMESTAMP,
        "mes": NUMBER,
        "Estado": VARCHAR2(50),
        "num": NUMBER,
    }
    write_det_reg_table(engine, registro_n, dtype_reg)
    logger.info(
        "Se actualizo desde %s hasta %s en registros de Detecciones - Sema",
        select_data["date"].min(),
        select_data["date"].max(),
    )

    # BORRAR REGISTROS TRANSACCIONALES ANTIGUOS
    now = datetime.now()
    day1 = (now - timedelta(days=20)).strftime("%d/%m/%y")

    stmt1 = text(
        """DELETE FROM DET_RESUM_SEMA_15M WHERE \"Tiempo\" < (TRUNC(CURRENT_DATE) - INTERVAL '20' DAY)"""
    )
    stmt2 = text(
        """DELETE FROM DET_RESUM_SEMA_HORA WHERE \"Tiempo\" < (TRUNC(CURRENT_DATE) - INTERVAL '20' DAY)"""
    )

    with engine.connect() as connection:
        connection.execute(stmt1)
        connection.execute(stmt2)
        connection.commit()
        logger.info(
            "Se borraron de las tablas transaccionales fechas menores a %s Detecciones - Sema",
            day1,
        )


if __name__ == "__main__":
    started_at_dt = datetime.now()
    started_at = started_at_dt.isoformat()
    try:
        main()
        if os.getenv("PIPELINE_INVOKED_BY_SCHEDULER", "") != "1":
            ended_at_dt = datetime.now()
            record_pipeline_success(
                "01_DETECCIONES_SEMA",
                source="pipeline",
                started_at=started_at,
                ended_at=ended_at_dt.isoformat(),
                duration_sec=round((ended_at_dt - started_at_dt).total_seconds(), 3),
            )
    except Exception as exc:
        if os.getenv("PIPELINE_INVOKED_BY_SCHEDULER", "") != "1":
            ended_at_dt = datetime.now()
            record_pipeline_failure(
                pipeline_id="01_DETECCIONES_SEMA",
                message=str(exc),
                error_type="pipeline_exception",
                source="pipeline",
                started_at=started_at,
                ended_at=ended_at_dt.isoformat(),
                duration_sec=round((ended_at_dt - started_at_dt).total_seconds(), 3),
            )
        raise
