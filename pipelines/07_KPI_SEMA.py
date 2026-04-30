from __future__ import annotations

import logging
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import gspread
import pandas as pd
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2 import service_account

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.alerting import record_pipeline_failure, record_pipeline_success
from sqlalchemy.dialects.oracle import FLOAT, NUMBER, VARCHAR2
from sqlalchemy.engine import create_engine

ROOT_DIR = Path(__file__).resolve().parents[1]
PIPELINE_ID = "07_KPI_SEMA"
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/bigquery",
]

logger = logging.getLogger("kpi_sema")


class ConfigurationError(RuntimeError):
    """Error funcional para variables de entorno faltantes o invalidas."""


def setup_logging() -> None:
    if logger.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def load_environment() -> None:
    load_dotenv(ROOT_DIR / "config" / ".env")
    load_dotenv(ROOT_DIR / ".env")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Variable de entorno requerida no definida: {name}")
    return value


def load_kpi_settings() -> tuple[str, str, str, str]:
    return (
        require_env("KPI_SHEET_URL"),
        require_env("KPI_WORKSHEET"),
        require_env("KPI_DATA_RANGE"),
        require_env("KPI_ORACLE_TABLE"),
    )


def parse_scopes(raw_scopes: str | None) -> list[str]:
    if not raw_scopes:
        return DEFAULT_SCOPES
    return [scope.strip() for scope in raw_scopes.split(",") if scope.strip()]


def build_google_client(scopes: list[str]) -> gspread.Client:
    credentials_path = require_env("GOOGLE_APPLICATION_CREDENTIALS")
    credentials_file = (ROOT_DIR / credentials_path).resolve()
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_file),
        scopes=scopes,
    )
    credentials.refresh(Request())
    return gspread.service_account(filename=str(credentials_file))


def build_oracle_engine():
    user = require_env("ORACLE_USER")
    password = require_env("ORACLE_PASSWORD")
    dsn = require_env("ORACLE_DSN")
    safe_password = quote_plus(password)
    return create_engine(f"oracle+oracledb://{user}:{safe_password}@{dsn}", thick_mode={})


def load_kpi_sheet(
    gspread_client: gspread.Client,
    sheet_url: str,
    worksheet_name: str,
    data_range: str,
) -> pd.DataFrame:
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
    raw = worksheet.batch_get((data_range,))[0]
    if not raw or len(raw) < 2:
        return pd.DataFrame()
    return pd.DataFrame.from_records(raw[1:], columns=raw[0])


def normalize_column_name(column_name: str) -> str:
    normalized = unicodedata.normalize("NFD", str(column_name))
    normalized = normalized.encode("ascii", "ignore").decode("utf-8")
    normalized = normalized.upper().replace(" ", "_")
    normalized = re.sub(r"[^A-Z0-9_]", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized)
    return normalized.strip("_")


def transform_kpi(dataframe: pd.DataFrame) -> pd.DataFrame:
    if dataframe.empty:
        return dataframe

    kpi_df = dataframe.copy()
    excluded_columns = {"MES","DIRECCION", "KPI_DISPONIBILIDAD_REAL", "KPI_CONFIABILIDAD_OPERATIVA", "KPI_MANTENIBILIDAD_EJECUTADA","KPI_CONFIABILIDAD_PROYECTADA","KPI_MANTENIBILIDAD_ESTIMADA","MES_PRONOSTICO","ESCENARIO_DE_ACCION"}

    kpi_df.columns = [normalize_column_name(col) for col in kpi_df.columns]

    object_columns = [
        col
        for col in kpi_df.select_dtypes(include=["object"]).columns
        if col not in excluded_columns
    ]
    percent_columns = [
        col
        for col in object_columns
        if kpi_df[col].astype(str).str.contains("%", regex=False).any()
    ]

    if object_columns:
        kpi_df[object_columns] = kpi_df[object_columns].replace(
            {",": ".", "%": ""},
            regex=True,
        )
        kpi_df[object_columns] = kpi_df[object_columns].apply(
            lambda col: pd.to_numeric(col, errors="coerce")
        )

    if percent_columns:
        kpi_df[percent_columns] = kpi_df[percent_columns] / 100

    for column in ["EXTERNO"]:
        if column in kpi_df.columns:
            kpi_df[column] = kpi_df[column].map(
                lambda value: (
                    None
                    if pd.isna(value)
                    else str(int(value))
                    if isinstance(value, float) and value.is_integer()
                    else str(value).strip()
                )
            )

    return kpi_df




def write_kpi_table(engine, dataframe: pd.DataFrame, table_name: str) -> None:
    dtype_kpi = {
        "MES": VARCHAR2(50),
        "EXTERNO": VARCHAR2(50),
        "DIRECCION": VARCHAR2(250),
        "CANTIDAD_EVENTOS_OBSERVADOS": FLOAT,
        "DURACION_EVENTOS_OBSERVADOS": FLOAT,
        "MTBF_OBSERVADO": FLOAT,
        "MTTR_OBSERVADO": FLOAT,
        "DISPONIBILIDAD_INHERENTE": FLOAT,
        "MANTENIBILIDAD_OBSERVADA": FLOAT,
        "CONFIABILIDAD_OPERATIVA": FLOAT,
		"KPI_DISPONIBILIDAD_REAL": VARCHAR2(100),
        "KPI_CONFIABILIDAD_OPERATIVA": VARCHAR2(100),
		"KPI_MANTENIBILIDAD_EJECUTADA": VARCHAR2(100),
        "CANTIDAD_EVENTOS_P": NUMBER,
        "DURACION_EVENTOS_P": FLOAT,
        "MTBF_ESTIMADO": FLOAT,
        "MTTR_ESTIMADO": FLOAT,
		"MANTENIBILIDAD_ESTIMADA": FLOAT,
		"CONFIABILIDAD_PROYECTADA": FLOAT,
        "KPI_CONFIABILIDAD_PROYECTADA": VARCHAR2(100),
        "KPI_MANTENIBILIDAD_ESTIMADA": VARCHAR2(100),
        "MES_PRONOSTICO": VARCHAR2(50),
		"ESCENARIO_DE_ACCION": VARCHAR2(100),
        #"PMT": VARCHAR2(250),
    }
    typed_columns = {col: dtype_kpi[col] for col in dataframe.columns if col in dtype_kpi}
    dataframe.to_sql(
        name=table_name,
        con=engine,
        if_exists="replace",
        index=False,
        dtype=typed_columns,
    )


def main() -> None:
    load_environment()
    setup_logging()

    scopes = parse_scopes(os.getenv("GOOGLE_SCOPES"))
    gspread_client = build_google_client(scopes)
    engine = build_oracle_engine()

    sheet_url, worksheet_name, data_range, oracle_table = load_kpi_settings()

    raw_df = load_kpi_sheet(gspread_client, sheet_url, worksheet_name, data_range)
    if raw_df.empty:
        raise RuntimeError("No se obtuvo data de KPI para actualizar")

    kpi_df = transform_kpi(raw_df)
    if kpi_df.empty:
        raise RuntimeError("No se obtuvo data transformada de KPI para actualizar")

    write_kpi_table(engine, kpi_df, oracle_table)
    logger.info("Se actualizo la tabla KPI SEMA filas=%s tabla=%s", len(kpi_df), oracle_table)


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
