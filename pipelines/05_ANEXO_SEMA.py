from __future__ import annotations

import logging
import os
import sys
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
from sqlalchemy.dialects.oracle import FLOAT, NUMBER, TIMESTAMP, VARCHAR2
from sqlalchemy.engine import create_engine

ROOT_DIR = Path(__file__).resolve().parents[1]
PIPELINE_ID = "05_ANEXO_SEMA"
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/bigquery",
]

logger = logging.getLogger("anexo_sema")


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


def load_anexo_sheet(gspread_client: gspread.Client, sheet_url: str, worksheet_name: str, data_range: str) -> pd.DataFrame:
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
    raw = worksheet.batch_get((data_range,))[0]
    if not raw or len(raw) < 2:
        return pd.DataFrame()
    return pd.DataFrame.from_records(raw[1:], columns=raw[0])


def transform_anexo(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    selected_columns = [
        "COD_ID",
        "EXTERNO",
        "DIRECCION CORTA",
        "LOCALIDAD",
        "ZONA PLANEAMIENTO",
        "REFERENCIA EQUIPO",
        "# INTERSECCIONES POR EQUIPO",
        "UBICACIÓN EQUIPO",
        "FECHA DE INSTALACION",
        "OPERACION ACTUAL",
        "ZONA AUTO",
        "FUNCIONAMIENTO",
        "Implementacion",
        "TIPO DE INTERSECCION",
        "Grupos de Señales Intersección",
        "Total Grupos de Señales Equipo",
        "Numero de Fases ",
        "Grupos Vehiculares",
        "Grupos BRT (TM)",
        "Grupos Peatonales",
        "Grupos Sonoros",
        "Grupos Ciclista",
        "Cantidad de contadores",
        "Grupos peatonales con contadores ",
        "Grupos con Botones",
        "Wide",
        "Grupos Wide",
        "Narrow",
        "grupos Narrow",
        "Función Detectores",
        "INES\n Shut Down",
        "FECHA INICIO VIGENCIA PLANEAMIENTO",
        "TIPOMALLA",
        "LINK CONFIG VD",
        "LINK DATEM",
        "LINK ESQUEMAS",
        "LINK REPOSITORIO",
        "LINK AUTOMATICO",
        "LINK ESQUEMAS ELECTRICOS",
        "CAMARAS CGT",
        "PRIORIDAD DE ATENCION ",
        "VALIDACIÓN DE PMT",
        "LONGITUD",
        "LATITUD",
        "Modo de operación",
        "Modo de operación2",
        "UBICACION GEOGRAFICA",
        "INTERSECCIONES AFECTADAS POR PMT",
        "FASE VEHICULO/PEATON",
        "CONFLICTO",
        "GRUPOS CONFLICTIVOS",
        "CIRCUITO PEATONAL",
        "TAMAÑO",
        "TIPO DE FASES PEATONAL ",
        "INTERSECCIONES CRITICAS REGULACION POLICIA",
        "INFRAESTRUCTURA CICLISTAS ",
        "TIPO DE REGULACION CICLISTA ",
        "AÑO INSTALACION",
    ]
    if "CORREDOR" in df.columns and "CORREDOR" not in selected_columns:
        selected_columns.append("CORREDOR")
    existing_cols = [col for col in selected_columns if col in df.columns]
    espejo = df[existing_cols].copy()

    fecha_default = pd.Timestamp("1900-01-01")
    for col in ["FECHA DE INSTALACION", "FECHA INICIO VIGENCIA PLANEAMIENTO"]:
        if col in espejo.columns:
            espejo[col] = pd.to_datetime(espejo[col], format="%d-%b-%Y", errors="coerce").fillna(
                fecha_default
            )

    espejo = espejo.rename(
        columns={
            "INES\n Shut Down": "Shut Down",
            "Numero de Fases ": "Numero de Fases",
            "PRIORIDAD DE ATENCION ": "PRIORIDAD DE ATENCION",
            "Función Detectores": "Funcion Detectores",
            "Grupos peatonales con contadores ": "Grupos peat contador",
            "Grupos con Botones": "Grupos Botones",
        }
    )

    espejo = espejo.mask(espejo.eq("None"))
    if "INTERSECCIONES AFECTADAS POR PMT" in espejo.columns:
        espejo["INTERSECCIONES AFECTADAS POR PMT"] = espejo[
            "INTERSECCIONES AFECTADAS POR PMT"
        ].fillna("PENDIENTE")

    for col in ["COD_ID", "EXTERNO", "ZONA AUTO"]:
        if col in espejo.columns:
            espejo[col] = espejo[col].replace("", "0", regex=True)

    for col in [
        "Grupos de Señales Intersección",
        "Total Grupos de Señales Equipo",
        "Numero de Fases",
        "Wide",
        "Narrow",
        "CAMARAS CGT",
        "Cantidad de contadores",
    ]:
        if col in espejo.columns:
            espejo[col] = pd.to_numeric(espejo[col].replace("", "0", regex=True), errors="coerce").fillna(0)

    for col in ["LONGITUD", "LATITUD"]:
        if col in espejo.columns:
            espejo[col] = (
                espejo[col]
                .astype(str)
                .str.replace(",", ".", regex=False)
                .replace({"": "0"})
            )
            espejo[col] = pd.to_numeric(espejo[col], errors="coerce").fillna(0.0)

    return espejo


def write_anexo_table(engine, espejo: pd.DataFrame, table_name: str) -> None:
    dtype_map = {
        "COD_ID": VARCHAR2(50),
        "EXTERNO": VARCHAR2(50),
        "DIRECCION CORTA": VARCHAR2(100),
        "LOCALIDAD": VARCHAR2(50),
        "ZONA PLANEAMIENTO": VARCHAR2(50),
        "REFERENCIA EQUIPO": VARCHAR2(100),
        "# INTERSECCIONES POR EQUIPO": VARCHAR2(50),
        "UBICACIÓN EQUIPO": VARCHAR2(100),
        "FECHA DE INSTALACION": TIMESTAMP,
        "OPERACION ACTUAL": VARCHAR2(100),
        "ZONA AUTO": VARCHAR2(50),
        "FUNCIONAMIENTO": VARCHAR2(50),
        "Implementacion": VARCHAR2(50),
        "TIPO DE INTERSECCION": VARCHAR2(100),
        "Grupos de Señales Intersección": NUMBER,
        "Total Grupos de Señales Equipo": NUMBER,
        "Numero de Fases": NUMBER,
        "Grupos Vehiculares": VARCHAR2(50),
        "Grupos BRT (TM)": VARCHAR2(50),
        "Grupos Peatonales": VARCHAR2(50),
        "Grupos Sonoros": VARCHAR2(65),
        "Grupos Ciclista": VARCHAR2(50),
        "Cantidad de contadores": NUMBER,
        "Grupos peat contador": VARCHAR2(50),
        "Grupos Botones": VARCHAR2(65),
        "Wide": NUMBER,
        "Grupos Wide": VARCHAR2(50),
        "Narrow": NUMBER,
        "grupos Narrow": VARCHAR2(50),
        "Funcion Detectores": VARCHAR2(100),
        "Shut Down": VARCHAR2(50),
        "FECHA INICIO VIGENCIA PLANEAMIENTO": TIMESTAMP,
        "TIPOMALLA": VARCHAR2(50),
        "LINK CONFIG VD": VARCHAR2(200),
        "LINK DATEM": VARCHAR2(200),
        "LINK ESQUEMAS": VARCHAR2(200),
        "LINK REPOSITORIO": VARCHAR2(200),
        "LINK AUTOMATICO": VARCHAR2(200),
        "LINK ESQUEMAS ELECTRICOS": VARCHAR2(200),
        "CAMARAS CGT": NUMBER,
        "PRIORIDAD DE ATENCION": VARCHAR2(50),
        "VALIDACIÓN DE PMT": VARCHAR2(50),
        "LONGITUD": FLOAT,
        "LATITUD": FLOAT,
        "Modo de operación": VARCHAR2(50),
        "Modo de operación2": VARCHAR2(50),
        "UBICACION GEOGRAFICA": VARCHAR2(100),
        "INTERSECCIONES AFECTADAS POR PMT": VARCHAR2(100),
        "FASE VEHICULO/PEATON": VARCHAR2(50),
        "CONFLICTO": VARCHAR2(50),
        "GRUPOS CONFLICTIVOS": VARCHAR2(50),
        "CIRCUITO PEATONAL": VARCHAR2(50),
        "TAMAÑO": VARCHAR2(50),
        "TIPO DE FASES PEATONAL ": VARCHAR2(50),
        "INTERSECCIONES CRITICAS REGULACION POLICIA": VARCHAR2(100),
        "INFRAESTRUCTURA CICLISTAS ": VARCHAR2(100),
        "TIPO DE REGULACION CICLISTA ": VARCHAR2(100),
        "AÑO INSTALACION": VARCHAR2(50),
    }
    dtype_map["CORREDOR"] = VARCHAR2(50)
    typed_columns = {col: dtype_map[col] for col in espejo.columns if col in dtype_map}
    espejo.to_sql(
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

    sheet_url = require_env("ANEXO_SHEET_URL")
    worksheet_name = os.getenv("ANEXO_WORKSHEET", "1.INTERSECCIONES.").strip() or "1.INTERSECCIONES."
    data_range = os.getenv("ANEXO_DATA_RANGE", "A4:CH2500").strip() or "A4:CH2500"
    oracle_table = os.getenv("ANEXO_ORACLE_TABLE", "esp_int_sema").strip() or "esp_int_sema"

    raw_df = load_anexo_sheet(gspread_client, sheet_url, worksheet_name, data_range)
    if raw_df.empty:
        raise RuntimeError("No se obtuvo data del anexo para actualizar")

    espejo = transform_anexo(raw_df)
    if espejo.empty:
        raise RuntimeError("No se obtuvo data transformada para actualizar")

    write_anexo_table(engine, espejo, oracle_table)
    logger.info("Se actualizo espejo SEMA filas=%s tabla=%s", len(espejo), oracle_table)


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
