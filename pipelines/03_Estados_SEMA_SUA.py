from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import gspread
import gspread_dataframe as gd
import pandas as pd
import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.alerting import record_pipeline_failure, record_pipeline_success
from sqlalchemy.dialects.oracle import FLOAT, NUMBER, TIMESTAMP, VARCHAR2
from sqlalchemy.engine import create_engine

ROOT_DIR = Path(__file__).resolve().parents[1]
PIPELINE_ID = "03_ESTADOS_SEMA_SUA"
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/bigquery",
]
REQUIRED_REGISTRY_COLUMNS = [
    "size_in_MB",
    "id",
    "name",
    "creation",
    "last_modification",
    "type_of_file",
    "date",
    "guia",
    "Estado",
    "num",
]
STATE_REPLACEMENTS = {
    "Error": "Equipo en Error",
    "Alarm": "En falla",
    "TSS": "Intersecciones semaforizadas",
    "Offline": "Sin conexion ETB",
    "Off": "Equipo Off",
    "Note": "Operacion sin conexion",
}

logger = logging.getLogger("estados_sua")


class ConfigurationError(RuntimeError):
    """Error funcional para variables de entorno faltantes o invalidas."""


class DataAvailabilityError(RuntimeError):
    """Error funcional cuando faltan datos esperados para operar."""


def setup_logging() -> None:
    if logger.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)


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


def build_oracle_engine():
    user = require_env("ORACLE_USER")
    password = require_env("ORACLE_PASSWORD")
    dsn = require_env("ORACLE_DSN")
    safe_password = quote_plus(password)
    return create_engine(f"oracle+oracledb://{user}:{safe_password}@{dsn}", thick_mode={})


def build_google_clients(scopes: list[str]):
    credentials_path = require_env("GOOGLE_APPLICATION_CREDENTIALS")
    credentials_file = (ROOT_DIR / credentials_path).resolve()
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_file), scopes=scopes
    )
    credentials.refresh(Request())
    access_token = credentials.token
    gspread_client = gspread.service_account(filename=str(credentials_file))
    drive_service = build("drive", "v3", credentials=credentials)
    return gspread_client, drive_service, access_token


def ensure_registry_columns(registro_df: pd.DataFrame) -> pd.DataFrame:
    registro = registro_df.copy()
    for column in REQUIRED_REGISTRY_COLUMNS:
        if column not in registro.columns:
            registro[column] = ""
    ordered_columns = REQUIRED_REGISTRY_COLUMNS + [
        column for column in registro.columns if column not in REQUIRED_REGISTRY_COLUMNS
    ]
    return registro[ordered_columns]


def load_registry_sheet(gspread_client: gspread.Client, sheet_url: str, worksheet_name: str) -> pd.DataFrame:
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
    raw = worksheet.get_all_values()
    if not raw:
        return pd.DataFrame(columns=REQUIRED_REGISTRY_COLUMNS)
    registro_df = pd.DataFrame.from_records(raw)
    registro_df.columns = registro_df.iloc[0]
    registro_df = registro_df.drop(registro_df.index[0]).reset_index(drop=True)
    return ensure_registry_columns(registro_df)


def write_dataframe_to_sheet(
    gspread_client: gspread.Client,
    sheet_url: str,
    worksheet_name: str,
    dataframe: pd.DataFrame,
) -> None:
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
    worksheet.clear()
    gd.set_with_dataframe(worksheet=worksheet, dataframe=dataframe, include_index=False)


def list_drive_files(drive_service, folder_id: str) -> pd.DataFrame:
    results = drive_service.files().list(
        q=f"'{folder_id}' in parents",
        pageSize=1000,
        fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, createdTime)",
    ).execute()
    items = results.get("files", [])

    data: list[list[object]] = []
    for row in items:
        if row["mimeType"] == "application/vnd.google-apps.folder":
            continue
        try:
            size_mb = round(int(row["size"]) / 100000000, 2)
        except (KeyError, TypeError, ValueError):
            size_mb = 0.00
        data.append(
            [
                size_mb,
                row["id"],
                row["name"],
                row["createdTime"],
                row["modifiedTime"],
                row["mimeType"],
            ]
        )

    if not data:
        return pd.DataFrame(
            columns=[
                "size_in_MB",
                "id",
                "name",
                "creation",
                "last_modification",
                "type_of_file",
                "date",
                "guia",
            ]
        )

    drive_df = pd.DataFrame(
        data,
        columns=["size_in_MB", "id", "name", "creation", "last_modification", "type_of_file"],
    )
    date_candidate = (
        drive_df["name"]
        .str.extract(r"^(?:\w+\s+){1}([^\n\r]*?(?=GMT))", expand=True)
        .iloc[:, 0]
        .str.strip()
    )
    drive_df["date"] = pd.to_datetime(date_candidate, format="%b %d %Y %H:%M:%S", errors="coerce")
    drive_df["guia"] = (
        drive_df["date"].dt.strftime("%Y%m%d%H%M").fillna("").astype(str)
    )
    drive_df = drive_df.sort_values(by=["date"], ascending=False)
    drive_df = drive_df[drive_df["type_of_file"] == "text/csv"]
    drive_df = drive_df[drive_df["name"].str.contains("Controladores_", na=False)]
    return drive_df.reset_index(drop=True)


def extract_states_table(raw_text: str, file_date: pd.Timestamp) -> pd.DataFrame:
    clean = re.sub(r"\([^()]*\)", "", raw_text)
    clean = re.sub(r"\([^()]*\)", "", clean)
    clean = re.sub(r"\([^()]*\)", "", clean)
    clean = re.sub(r"\r\n", "", clean)
    clean = "".join(re.findall(r"(?s)(?<=TSS).*?(?=Nota: )", clean))
    clean = re.sub(r" +", " ", clean).strip()

    keys = re.findall(r"[a-zA-Z]+", clean)
    values = re.split(r"Error |Alarm |TSS |Offline |Off |Note |\n", clean)
    if values:
        values.pop(0)
    values = [item.strip().split(" ") for item in values if item.strip()]

    if not keys or not values:
        return pd.DataFrame(columns=["ESTADO", "EXTERNO", "FECHA"])
    if len(keys) != len(values):
        logger.warning(
            "Bloques de estados desalineados en archivo: states=%s values=%s",
            len(keys),
            len(values),
        )

    rows: list[dict[str, object]] = []
    for raw_state, tokens in zip(keys, values):
        state = STATE_REPLACEMENTS.get(raw_state, raw_state)
        for token in tokens:
            externo = str(token).strip()
            if not externo:
                continue
            rows.append({"ESTADO": state, "EXTERNO": externo, "FECHA": file_date})

    if not rows:
        return pd.DataFrame(columns=["ESTADO", "EXTERNO", "FECHA"])
    return pd.DataFrame(rows, columns=["ESTADO", "EXTERNO", "FECHA"])


def ensure_recent_updates(registro_df: pd.DataFrame, max_staleness_hours: int) -> None:
    now = datetime.now()
    registro = ensure_registry_columns(registro_df)
    registro["Estado"] = registro["Estado"].astype(str)
    procesados = registro[registro["Estado"].str.lower() == "procesado"].copy()
    if procesados.empty:
        raise DataAvailabilityError(
            "No existen registros en estado 'Procesado' para validar recencia de actualizacion"
        )

    procesados["date"] = pd.to_datetime(procesados["date"], errors="coerce")
    last_processed = procesados["date"].max()
    if pd.isna(last_processed):
        raise DataAvailabilityError(
            "No fue posible determinar la fecha del ultimo registro procesado en la hoja Registro"
        )

    lag_hours = (now - last_processed.to_pydatetime()).total_seconds() / 3600
    if lag_hours > max_staleness_hours:
        raise DataAvailabilityError(
            "No hay actualizacion de estados en mas de "
            f"{max_staleness_hours} horas. Ultima fecha procesada: {last_processed.isoformat()}"
        )


def main() -> None:
    load_environment()
    setup_logging()

    scopes = parse_scopes(os.getenv("GOOGLE_SCOPES"))
    gspread_client, drive_service, access_token = build_google_clients(scopes)
    engine = build_oracle_engine()

    sheet_url = require_env("ESTADOS_SUA_SHEET_URL")
    registro_tab = os.getenv("ESTADOS_SUA_REGISTRO_WORKSHEET", "Registro").strip() or "Registro"
    estados_tab = os.getenv("ESTADOS_SUA_ESTADOS_WORKSHEET", "Estados").strip() or "Estados"
    drive_folder_id = require_env("ESTADOS_SUA_DRIVE_FOLDER_ID")
    max_staleness_hours = int(os.getenv("ESTADOS_SUA_MAX_STALENESS_HOURS", "24"))

    query_base = """
        SELECT COD_ID, EXTERNO, "DIRECCION CORTA", "ZONA AUTO", "LOCALIDAD",
               "REFERENCIA EQUIPO", "TIPO DE INTERSECCION", "OPERACION ACTUAL",
               "Wide", "Narrow", "Shut Down", "LONGITUD", "LATITUD"
        FROM ESP_INT_SEMA
    """
    base = pd.read_sql(query_base, engine)
    base.columns = [
        "COD_ID",
        "EXTERNO",
        "DIRECCION",
        "ZONA_AUTO",
        "LOCALIDAD",
        "EQUIPO",
        "INTERSECCION",
        "OPERACION",
        "NUM_WIDE",
        "NUM_NARROW",
        "SHUTDOWN",
        "LONGITUD",
        "LATITUD",
    ]
    base = base.astype(
        {
            "COD_ID": "int64",
            "EXTERNO": "str",
            "ZONA_AUTO": "str",
            "NUM_WIDE": "int64",
            "NUM_NARROW": "int64",
            "LONGITUD": "float64",
            "LATITUD": "float64",
        }
    )

    registro_df = load_registry_sheet(gspread_client, sheet_url, registro_tab)
    drive_df = list_drive_files(drive_service, drive_folder_id)
    if drive_df.empty:
        ensure_recent_updates(registro_df, max_staleness_hours)
        logger.info("No se encontraron archivos CSV en la carpeta origen")
        return

    merged = pd.merge(
        drive_df,
        registro_df[["id", "Estado"]],
        left_on="id",
        right_on="id",
        how="left",
    ).fillna("Pendiente")
    pending = merged[merged["Estado"] == "Pendiente"].copy()
    pending = pending.sort_values(by=["date"], ascending=False)
    ids = pending["id"].tolist()

    if not ids:
        ensure_recent_updates(registro_df, max_staleness_hours)
        logger.info("No hay registros de estados nuevos - SEMA")
        return

    parsed_data: list[pd.DataFrame] = []
    count_data: list[pd.DataFrame] = []

    for file_id in ids:
        file_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        response = requests.get(
            file_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=120,
        )
        response.raise_for_status()

        file_date = pending.loc[pending["id"] == file_id, "date"].iloc[0]
        parsed = extract_states_table(response.text, file_date)
        if parsed.empty:
            logger.warning("Archivo sin estados parseables id=%s", file_id)
            continue

        parsed_data.append(parsed)
        count_data.append(pd.DataFrame({"id": [file_id], "num": [len(parsed["EXTERNO"])]}))
        logger.info("Archivo procesado id=%s", file_id)

    if not parsed_data:
        raise DataAvailabilityError("No se lograron parsear estados desde los archivos pendientes")

    result_df = pd.concat(parsed_data, axis=0).drop_duplicates()
    result_df = result_df[result_df["EXTERNO"] != "Off"]
    result_df["EXTERNO"] = result_df["EXTERNO"].astype(str)
    num_reg = pd.concat(count_data, axis=0)

    last_time = pending["date"].max()
    ultimo = result_df[result_df["FECHA"] == last_time].copy()
    union = pd.merge(base, ultimo, on="EXTERNO", how="left").fillna(
        {"ESTADO": "Operando", "FECHA": last_time}
    )

    processed_updates = pending.copy()
    processed_updates.loc[:, "Estado"] = "Procesado"
    processed_updates = pd.merge(
        processed_updates,
        num_reg[["id", "num"]],
        left_on="id",
        right_on="id",
        how="left",
    )
    registro_n = pd.concat([registro_df, processed_updates], ignore_index=True)
    registro_n["date"] = pd.to_datetime(registro_n["date"], errors="coerce")
    registro_n = registro_n.sort_values(by="date", ascending=False)

    write_dataframe_to_sheet(gspread_client, sheet_url, registro_tab, registro_n)
    write_dataframe_to_sheet(gspread_client, sheet_url, estados_tab, union)

    dtype_reg = {
        "size_in_MB": FLOAT,
        "id": VARCHAR2(100),
        "name": VARCHAR2(255),
        "creation": VARCHAR2(100),
        "last_modification": VARCHAR2(100),
        "type_of_file": VARCHAR2(50),
        "date": TIMESTAMP,
        "guia": NUMBER,
        "Estado": VARCHAR2(50),
        "num": NUMBER,
    }
    registro_n.to_sql(
        name="est_sua_reg_sema",
        con=engine,
        if_exists="replace",
        index=False,
        dtype=dtype_reg,
    )

    dtype_act = {
        "COD_ID": NUMBER,
        "EXTERNO": VARCHAR2(50),
        "DIRECCION": VARCHAR2(150),
        "ZONA_AUTO": VARCHAR2(50),
        "LOCALIDAD": VARCHAR2(80),
        "EQUIPO": VARCHAR2(80),
        "INTERSECCION": VARCHAR2(80),
        "OPERACION": VARCHAR2(50),
        "NUM_WIDE": NUMBER,
        "NUM_NARROW": NUMBER,
        "SHUTDOWN": VARCHAR2(20),
        "LONGITUD": FLOAT,
        "LATITUD": FLOAT,
        "ESTADO": VARCHAR2(80),
        "FECHA": TIMESTAMP,
    }
    union.to_sql(
        name="est_sua_act_sema",
        con=engine,
        if_exists="replace",
        index=False,
        dtype=dtype_act,
    )

    dtype_hist = {"ESTADO": VARCHAR2(80), "EXTERNO": VARCHAR2(50), "FECHA": TIMESTAMP}
    result_df.to_sql(
        name="est_sua_hist_sema",
        con=engine,
        if_exists="append",
        index=False,
        dtype=dtype_hist,
    )

    logger.info("Se actualizan %s registro(s) de estados - SEMA", len(ids))


if __name__ == "__main__":
    started_at_dt = datetime.now()
    started_at = started_at_dt.isoformat()
    try:
        main()
        if os.getenv("PIPELINE_INVOKED_BY_SCHEDULER", "") != "1":
            ended_at_dt = datetime.now()
            record_pipeline_success(
                PIPELINE_ID,
                source="pipeline",
                started_at=started_at,
                ended_at=ended_at_dt.isoformat(),
                duration_sec=round((ended_at_dt - started_at_dt).total_seconds(), 3),
            )
    except Exception as exc:
        logger.exception("Fallo %s", PIPELINE_ID)
        if os.getenv("PIPELINE_INVOKED_BY_SCHEDULER", "") != "1":
            ended_at_dt = datetime.now()
            record_pipeline_failure(
                pipeline_id=PIPELINE_ID,
                message=str(exc),
                error_type="pipeline_exception",
                source="pipeline",
                started_at=started_at,
                ended_at=ended_at_dt.isoformat(),
                duration_sec=round((ended_at_dt - started_at_dt).total_seconds(), 3),
            )
        raise
