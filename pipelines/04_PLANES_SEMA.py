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
import numpy as np
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
PIPELINE_ID = "04_PLANES_SEMA"
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
    "num_plan",
]

logger = logging.getLogger("planes_sema")


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


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default


def normalize_numeric_column(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    text = text.replace({"": np.nan, "None": np.nan, "nan": np.nan, "NaT": np.nan})
    text = text.str.replace(",", ".", regex=False)
    return pd.to_numeric(text, errors="coerce")


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


def build_oracle_engine(prefix: str, fallback_to_default: bool = False):
    user = os.getenv(f"{prefix}_USER", "").strip()
    password = os.getenv(f"{prefix}_PASSWORD", "").strip()
    dsn = os.getenv(f"{prefix}_DSN", "").strip()

    if fallback_to_default and (not user or not password or not dsn):
        user = user or os.getenv("ORACLE_USER", "").strip()
        password = password or os.getenv("ORACLE_PASSWORD", "").strip()
        dsn = dsn or os.getenv("ORACLE_DSN", "").strip()

    missing = [
        name
        for name, value in {
            f"{prefix}_USER": user,
            f"{prefix}_PASSWORD": password,
            f"{prefix}_DSN": dsn,
        }.items()
        if not value
    ]
    if missing:
        raise ConfigurationError(f"Variables requeridas ausentes para conexion Oracle: {', '.join(missing)}")

    safe_password = quote_plus(password)
    return create_engine(f"oracle+oracledb://{user}:{safe_password}@{dsn}", thick_mode={})


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
    drive_df["guia"] = drive_df["date"].dt.strftime("%Y%m%d%H%M").fillna("").astype(str)
    drive_df = drive_df.sort_values(by=["date"], ascending=False)
    drive_df = drive_df[drive_df["type_of_file"] == "text/csv"]
    drive_df = drive_df[drive_df["name"].str.contains("Controladores_", na=False)]
    return drive_df.reset_index(drop=True)


def parse_plan_file(raw_text: str, guia: str) -> pd.DataFrame:
    clean = re.sub(r"\([^()]*\)", "", raw_text)
    clean = re.sub(r"\r\n", " ", clean)
    clean = re.sub(r"\n", " ", clean)
    clean = re.sub(r'undefined"', " ", clean)
    clean = clean.replace('*** "', "")
    clean = clean.replace("***", "SEPARAR")
    blocks = clean.split("SEPARAR")
    blocks = [item.split("InformaciÃ²n")[0].strip() for item in blocks if item.strip()]

    data_rows: list[dict[str, object]] = []
    for block in blocks:
        tiempo = block.split(",")[0].strip()
        externo = "".join(re.findall(r"(?s)(?<=TSS).*?(?=SP)", block)).strip()
        plan_sale = "".join(re.findall(r"(?s)(?<=SP).*?(?=off)", block)).strip()
        plan_entra = "".join(re.findall(r"(?s)(?<=off SP).*?(?=on)", block)).strip()
        actor = block.split("on ,")[-1].strip()
        if not externo:
            continue
        data_rows.append(
            {
                "Tiempo": tiempo,
                "Externo": externo,
                "Plan_ingresa": plan_entra,
                "Plan_finaliza": plan_sale,
                "Actor": actor,
            }
        )

    if not data_rows:
        return pd.DataFrame(columns=["Tiempo", "Externo", "Plan_ingresa", "Plan_finaliza", "Actor", "Referencia"])

    base = pd.DataFrame(data_rows)
    base["counter"] = (np.arange(len(base)) + 1000).astype(str)
    guia_clean = re.sub(r"\D", "", str(guia))
    if not guia_clean:
        guia_clean = datetime.now().strftime("%Y%m%d%H%M")
    base["Referencia"] = (guia_clean + base["counter"]).astype(np.int64)
    base = base.drop(columns=["counter"])
    return base


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
            "No hay actualizacion de planes en mas de "
            f"{max_staleness_hours} horas. Ultima fecha procesada: {last_processed.isoformat()}"
        )


def main() -> None:
    load_environment()
    setup_logging()

    scopes = parse_scopes(os.getenv("GOOGLE_SCOPES"))
    gspread_client, drive_service, access_token = build_google_clients(scopes)
    main_engine = build_oracle_engine("PLANES_ORACLE", fallback_to_default=True)
    suanet_sgm_enabled = parse_bool(
        os.getenv("PLANES_ACTUALIZACION_SUANET_SGM_ENABLED"),
        default=False,
    )
    sgm_engine = (
        build_oracle_engine("PLANES_SGM_ORACLE", fallback_to_default=False)
        if suanet_sgm_enabled
        else None
    )

    sheet_url = require_env("PLANES_SHEET_URL")
    registro_tab = os.getenv("PLANES_REGISTRO_WORKSHEET", "Registro").strip() or "Registro"
    planes_tab = os.getenv("PLANES_ACTUAL_WORKSHEET", "Planes").strip() or "Planes"
    drive_folder_id = require_env("PLANES_DRIVE_FOLDER_ID")
    max_staleness_hours = int(os.getenv("PLANES_MAX_STALENESS_HOURS", "24"))
    lookback_weeks = int(os.getenv("PLANES_HISTORY_LOOKBACK_WEEKS", "1"))
    sgm_schema = os.getenv("PLANES_SGM_SCHEMA", "SGMEDICION").strip() or "SGMEDICION"

    registro_df = load_registry_sheet(gspread_client, sheet_url, registro_tab)
    drive_df = list_drive_files(drive_service, drive_folder_id)
    if drive_df.empty:
        ensure_recent_updates(registro_df, max_staleness_hours)
        logger.info("No se encontraron archivos CSV de planes en la carpeta origen")
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
        logger.info("No hay registros nuevos de planes - SEMA")
        return

    parsed_data: list[pd.DataFrame] = []
    registros_count: list[pd.DataFrame] = []
    for file_id in ids:
        file_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
        response = requests.get(
            file_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=120,
        )
        response.raise_for_status()
        guia = pending.loc[pending["id"] == file_id, "guia"].iloc[0]
        parsed = parse_plan_file(response.text, str(guia))
        if parsed.empty:
            logger.warning("Archivo sin planes parseables id=%s", file_id)
            continue
        parsed_data.append(parsed)
        registros_count.append(pd.DataFrame({"id": [file_id], "num_plan": [len(parsed)]}))
        logger.info("Archivo procesado id=%s planes=%s", file_id, len(parsed))

    if not parsed_data:
        raise DataAvailabilityError("No se lograron parsear planes desde los archivos pendientes")

    result_df = pd.concat(parsed_data, axis=0).drop_duplicates()
    result_df = result_df[result_df["Externo"].astype(bool)]
    result_df["Tiempo"] = pd.to_datetime(result_df["Tiempo"], format="%d.%m.%Y %H:%M:%S", errors="coerce")
    for col in ["Externo", "Plan_ingresa", "Plan_finaliza"]:
        result_df[col] = pd.to_numeric(result_df[col], errors="coerce")
    result_df = result_df.dropna(subset=["Tiempo", "Externo", "Plan_ingresa", "Plan_finaliza"])
    result_df["Externo"] = result_df["Externo"].astype(np.int64)
    result_df["Plan_ingresa"] = result_df["Plan_ingresa"].astype(np.int64)
    result_df["Plan_finaliza"] = result_df["Plan_finaliza"].astype(np.int64)

    dtype_hist = {
        "Tiempo": TIMESTAMP,
        "Externo": NUMBER,
        "Plan_ingresa": NUMBER,
        "Plan_finaliza": NUMBER,
        "Actor": VARCHAR2(50),
        "Referencia": NUMBER,
    }
    result_df.to_sql(
        name="plan_hist_sema",
        con=main_engine,
        if_exists="append",
        index=False,
        dtype=dtype_hist,
    )

    now = datetime.now()
    day_base = (now - timedelta(weeks=lookback_weeks)).strftime("%d/%m/%y")
    day_fin = (now + timedelta(days=1)).strftime("%d/%m/%y")
    query = (
        'SELECT * FROM PLAN_HIST_SEMA WHERE "Tiempo" >= TO_DATE(:day_base, \'DD/MM/RR\') '
        'AND "Tiempo" <= TO_DATE(:day_fin, \'DD/MM/RR\')'
    )
    hist_sem = pd.read_sql(query, main_engine, params={"day_base": day_base, "day_fin": day_fin})
    planes = hist_sem.sort_values("Referencia").drop_duplicates("Externo", keep="last").sort_values("Externo")
    planes["Duracion"] = datetime.now() - planes["Tiempo"]
    planes["Duracion"] = planes["Duracion"].apply(lambda x: f"{x.days} {str(x).split()[-1]}")

    dtype_act = {
        "Tiempo": TIMESTAMP,
        "Externo": NUMBER,
        "Plan_ingresa": NUMBER,
        "Plan_finaliza": NUMBER,
        "Actor": VARCHAR2(50),
        "Referencia": NUMBER,
        "Duracion": VARCHAR2(32),
    }
    planes.to_sql(
        name="plan_act_sema",
        con=main_engine,
        if_exists="replace",
        index=False,
        dtype=dtype_act,
    )

    write_dataframe_to_sheet(gspread_client, sheet_url, planes_tab, planes)

    num_reg = pd.concat(registros_count, axis=0)
    processed_updates = pending.copy()
    processed_updates.loc[:, "Estado"] = "Procesado"
    processed_updates = pd.merge(
        processed_updates,
        num_reg[["id", "num_plan"]],
        left_on="id",
        right_on="id",
        how="left",
    )
    registro_n = pd.concat([registro_df, processed_updates], ignore_index=True)
    registro_n["date"] = pd.to_datetime(registro_n["date"], errors="coerce")
    for col in ["size_in_MB", "guia", "num_plan"]:
        if col in registro_n.columns:
            registro_n[col] = normalize_numeric_column(registro_n[col])
    registro_n = registro_n.sort_values(by="date", ascending=False)
    write_dataframe_to_sheet(gspread_client, sheet_url, registro_tab, registro_n)

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
        "num_plan": NUMBER,
    }
    registro_n.to_sql(
        name="plan_reg_sema",
        con=main_engine,
        if_exists="replace",
        index=False,
        dtype=dtype_reg,
    )

    if suanet_sgm_enabled and sgm_engine is not None:
        dtype_sgm = {
            "Tiempo": TIMESTAMP,
            "Externo": NUMBER,
            "Plan_ingresa": NUMBER,
            "Plan_finaliza": NUMBER,
            "Actor": VARCHAR2(50),
            "Referencia": NUMBER,
            "Duracion": VARCHAR2(32),
        }
        planes.to_sql(
            schema=sgm_schema,
            name="plan_act_sema",
            con=sgm_engine,
            if_exists="replace",
            index=False,
            dtype=dtype_sgm,
        )
    else:
        logger.info("Actualizacion SUANET-SGM deshabilitada por configuracion")

    logger.info("Se actualizan %s registro(s) de planes - SEMA", len(ids))


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
