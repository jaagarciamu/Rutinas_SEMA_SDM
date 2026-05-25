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
from gspread.exceptions import WorksheetNotFound
from google.auth.transport.requests import Request
from google.oauth2 import service_account

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.alerting import record_pipeline_failure, record_pipeline_success
from sqlalchemy.dialects.oracle import VARCHAR2
from sqlalchemy.engine import create_engine

ROOT_DIR = Path(__file__).resolve().parents[1]
PIPELINE_ID = "06_MANTENIMIENTO_SEMA"
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/bigquery",
]

logger = logging.getLogger("mantenimiento_sema")


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


def load_sheet_data(
    gspread_client: gspread.Client,
    sheet_url: str,
    worksheet_name: str,
    data_range: str,
) -> pd.DataFrame:
    try:
        worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
    except WorksheetNotFound:
        return pd.DataFrame()
    raw = worksheet.batch_get((data_range,))[0]
    if not raw or len(raw) < 2:
        return pd.DataFrame()
    headers = raw[0]
    normalized_rows: list[list[object]] = []
    truncated_rows = 0
    padded_rows = 0
    expected_len = len(headers)
    for row in raw[1:]:
        row_values = list(row)
        if len(row_values) > expected_len:
            row_values = row_values[:expected_len]
            truncated_rows += 1
        elif len(row_values) < expected_len:
            row_values.extend([None] * (expected_len - len(row_values)))
            padded_rows += 1
        normalized_rows.append(row_values)
    if truncated_rows or padded_rows:
        logger.warning(
            "Ajuste de columnas en hoja=%s rango=%s encabezados=%s filas_recortadas=%s filas_completadas=%s",
            worksheet_name,
            data_range,
            expected_len,
            truncated_rows,
            padded_rows,
        )
    return pd.DataFrame.from_records(normalized_rows, columns=headers)


def transform_mtto(mtto: pd.DataFrame) -> pd.DataFrame:
    if mtto.empty:
        return mtto
    renamed = mtto.rename(
        columns={
            "Reportado \na": "Reportado a",
            "Hora \nReporte": "Hora Reporte",
            "Grupo en \nTerreno": "Grupo en Terreno",
            "Hora\nInicio": "Hora Inicio",
            "Hora\nFin": "Hora Fin",
        }
    ).copy()
    for col in renamed.select_dtypes(include="object"):
        renamed[col] = renamed[col].map(lambda x: x[:3950] if isinstance(x, str) else x)
    return renamed


def truncate_columns_to_limits(dataframe: pd.DataFrame, limits: dict[str, int]) -> pd.DataFrame:
    df = dataframe.copy()
    for col, max_len in limits.items():
        if col not in df.columns:
            continue
        series = df[col]
        mask = series.map(lambda v: isinstance(v, str) and len(v) > max_len)
        truncated_count = int(mask.sum())
        if truncated_count > 0:
            logger.warning(
                "Se truncaron %s registros en columna '%s' al maximo %s caracteres",
                truncated_count,
                col,
                max_len,
            )
            df.loc[mask, col] = df.loc[mask, col].str.slice(0, max_len)
    return df


def normalize_mtto_types(dataframe: pd.DataFrame) -> pd.DataFrame:
    df = dataframe.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
            continue
        df[col] = df[col].where(df[col].notna(), None)
        df[col] = df[col].map(lambda value: str(value) if value is not None else None)
    return df


def write_mtto_table(engine, dataframe: pd.DataFrame, table_name: str) -> None:
    dtype_mtto = {
        "S.S No": VARCHAR2(50),
        "O.T No": VARCHAR2(50),
        "ID_SIG": VARCHAR2(50),
        "NEI": VARCHAR2(50),
        "Dirección": VARCHAR2(100),
        "Reportado a": VARCHAR2(50),
        "Hora Reporte": VARCHAR2(50),
        "Tipo de Reporte": VARCHAR2(50),
        "Grupo en Terreno": VARCHAR2(50),
        "Hora Inicio": VARCHAR2(50),
        "Diagnóstico": VARCHAR2(500),
        "Solución": VARCHAR2(4000),
        "Hora Fin": VARCHAR2(50),
        "Fecha Solicitud S.S.": VARCHAR2(50),
        "Tiempo Ejecución": VARCHAR2(50),
        "Equipo": VARCHAR2(65),
        "Localidad": VARCHAR2(65),
        "Zona Operación": VARCHAR2(50),
        "Ubicación": VARCHAR2(50),
        "Detalle": VARCHAR2(50),
        "Clasificación": VARCHAR2(65),
        "CTO": VARCHAR2(50),
        "TIpo de SS": VARCHAR2(65),
        "ROBOS": VARCHAR2(50),
        "ESTADO DE INTERSECCIÓN": VARCHAR2(65),
        "Hora de registro operador": VARCHAR2(65),
        "DISPONIBILIDAD MENSUAL": VARCHAR2(65),
        "ANIO_FUENTE": VARCHAR2(4),
    }
    typed_columns = {col: dtype_mtto[col] for col in dataframe.columns if col in dtype_mtto}
    text_limits = {
        "S.S No": 50,
        "O.T No": 50,
        "ID_SIG": 50,
        "NEI": 50,
        "Dirección": 100,
        "Reportado a": 50,
        "Hora Reporte": 50,
        "Tipo de Reporte": 50,
        "Grupo en Terreno": 50,
        "Hora Inicio": 50,
        "Diagnóstico": 500,
        "Solución": 4000,
        "Hora Fin": 50,
        "Fecha Solicitud S.S.": 50,
        "Tiempo Ejecución": 50,
        "Equipo": 65,
        "Localidad": 65,
        "Zona Operación": 50,
        "Ubicación": 50,
        "Detalle": 50,
        "Clasificación": 65,
        "CTO": 50,
        "TIpo de SS": 65,
        "ESTADO INICIAL": 65,
        "Hora de registro operador": 65,
        "DISPONIBILIDAD MENSUAL": 65,
    }
    safe_df = truncate_columns_to_limits(dataframe, text_limits)
    safe_df = normalize_mtto_types(safe_df)
    safe_df.to_sql(
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

    sheet_url = require_env("MANTENIMIENTO_SHEET_URL")
    data_range = os.getenv("MANTENIMIENTO_DATA_RANGE", "A1:Z1000000").strip() or "A1:Z1000000"
    oracle_table = os.getenv("MANTENIMIENTO_ORACLE_TABLE", "mtto_hist_sema").strip() or "mtto_hist_sema"

    current_year = datetime.now().year
    yearly_frames: list[pd.DataFrame] = []
    for year in range(2023, current_year + 1):
        worksheet_name = str(year)
        year_df = load_sheet_data(gspread_client, sheet_url, worksheet_name, data_range)
        if year_df.empty:
            logger.warning("Sin datos o sin pestaña para anio=%s", year)
            continue
        year_df["ANIO_FUENTE"] = str(year)
        yearly_frames.append(year_df)
        logger.info("Datos cargados de pestana anio=%s filas=%s", year, len(year_df))
    if not yearly_frames:
        raise RuntimeError("No se obtuvo data de mantenimiento entre 2023 y el anio actual")
    raw_df = pd.concat(yearly_frames, ignore_index=True, sort=False)

    mtto = transform_mtto(raw_df)
    if mtto.empty:
        raise RuntimeError("No se obtuvo data transformada de mantenimiento para actualizar")

    write_mtto_table(engine, mtto, oracle_table)
    logger.info("Se actualizo la base de Mantenimiento SEMA filas=%s tabla=%s", len(mtto), oracle_table)


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
