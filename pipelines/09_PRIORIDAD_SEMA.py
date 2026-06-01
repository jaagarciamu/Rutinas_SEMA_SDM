from __future__ import annotations

import logging
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus

import gspread
import pandas as pd
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from sqlalchemy.dialects.oracle import FLOAT, NUMBER, VARCHAR2
from sqlalchemy.engine import create_engine

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.alerting import record_pipeline_failure, record_pipeline_success

ROOT_DIR = Path(__file__).resolve().parents[1]
PIPELINE_ID = "09_PRIORIDAD_SEMA"
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/bigquery",
]

logger = logging.getLogger("prioridad_sema")


class ConfigurationError(RuntimeError):
    pass


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


def parse_scopes(raw_scopes: str | None) -> list[str]:
    if not raw_scopes:
        return DEFAULT_SCOPES
    return [scope.strip() for scope in raw_scopes.split(",") if scope.strip()]


def normalizar_columna(col: str) -> str:
    col = "".join(
        c for c in unicodedata.normalize("NFD", str(col))
        if unicodedata.category(c) != "Mn"
    )
    col = col.lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    return col.strip("_")


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


def truncate_columns_to_limits(df: pd.DataFrame, limits: dict[str, int]) -> pd.DataFrame:
    safe_df = df.copy()
    for col, max_len in limits.items():
        if col in safe_df.columns:
            safe_df[col] = safe_df[col].apply(
                lambda x: str(x)[:max_len] if pd.notna(x) else None
            )
    return safe_df


def normalize_prioridad_df(prioridad: pd.DataFrame) -> pd.DataFrame:
    df = prioridad.copy()
    df.columns = [normalizar_columna(col) for col in df.columns]

    required_defaults = {
        "prioridad_operacional": "2",
        "bc_redondeado": "2",
        "indice_operacional_traspuesto": "2",
        "prioridad": "2",
        "cactt": "0",
        "gogev": "0",
    }
    for col, default in required_defaults.items():
        if col not in df.columns:
            df[col] = default
        df[col] = (
            df[col]
            .replace(r"^\s*$", default, regex=True)
            .replace("#N/A", default)
            .fillna(default)
        )

    if "bc_calculado" not in df.columns:
        df["bc_calculado"] = 2.0
    df["bc_calculado"] = (
        pd.to_numeric(
            df["bc_calculado"].replace(r"^\s*$", "2", regex=True).replace("#N/A", "2"),
            errors="coerce",
        )
        .fillna(2.0)
        .astype(float)
    )

    numeric_cols = [
        "prioridad_operacional",
        "bc_redondeado",
        "indice_operacional_traspuesto",
        "prioridad",
        "cactt",
        "gogev",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    text_cols = [col for col in df.columns if col not in numeric_cols + ["bc_calculado"]]
    for col in text_cols:
        df[col] = df[col].where(df[col].notna(), None)

    return df


def load_priority_sheet(
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


def write_priority_table(engine, dataframe: pd.DataFrame, table_name: str) -> None:
    safe_df = normalize_prioridad_df(dataframe)
    text_limits = {col: 80 for col in safe_df.columns}
    text_limits.update(
        {
            "cod_id": 50,
            "externo": 50,
            "direccion_corta": 80,
            "iotbc_cualitativa": 50,
        }
    )
    safe_df = truncate_columns_to_limits(safe_df, text_limits)

    dtype_pri = {
        "cod_id": VARCHAR2(50),
        "externo": VARCHAR2(50),
        "direccion_corta": VARCHAR2(80),
        "prioridad_operacional": NUMBER,
        "indice_operacional_traspuesto": NUMBER,
        "bc_calculado": FLOAT,
        "bc_redondeado": NUMBER,
        "prioridad": NUMBER,
        "iotbc_cualitativa": VARCHAR2(50),
        "cactt": NUMBER,
        "gogev": NUMBER,
    }

    safe_df.to_sql(
        name=table_name,
        con=engine,
        if_exists="replace",
        index=False,
        dtype=dtype_pri,
        chunksize=1000,
    )


def main() -> None:
    load_environment()
    setup_logging()

    scopes = parse_scopes(os.getenv("GOOGLE_SCOPES"))
    gspread_client = build_google_client(scopes)
    engine = build_oracle_engine()

    sheet_url = require_env("PRIORIDAD_SEMA_SHEET_URL")
    worksheet_name = os.getenv("PRIORIDAD_SEMA_WORKSHEET", "PRIORIZACIÓN").strip() or "PRIORIZACIÓN"
    data_range = os.getenv("PRIORIDAD_SEMA_DATA_RANGE", "A1:L5000").strip() or "A1:L5000"
    oracle_table = os.getenv("PRIORIDAD_SEMA_ORACLE_TABLE", "prio_ext_sema").strip() or "prio_ext_sema"

    prioridad = load_priority_sheet(gspread_client, sheet_url, worksheet_name, data_range)
    if prioridad.empty:
        raise RuntimeError("No se obtuvo data de la hoja de prioridad")

    write_priority_table(engine, prioridad, oracle_table)
    logger.info("Se actualizo la prioridad SEMA filas=%s tabla=%s", len(prioridad), oracle_table)


if __name__ == "__main__":
    started_at_dt = pd.Timestamp.now().to_pydatetime()
    started_at = started_at_dt.isoformat()
    try:
        main()
        if os.getenv("PIPELINE_INVOKED_BY_SCHEDULER", "") != "1":
            ended_at_dt = pd.Timestamp.now().to_pydatetime()
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
            ended_at_dt = pd.Timestamp.now().to_pydatetime()
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
