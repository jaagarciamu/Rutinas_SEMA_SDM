from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv
from sqlalchemy import inspect, text
from sqlalchemy.dialects.oracle import FLOAT, NUMBER, VARCHAR2
from sqlalchemy.engine import create_engine

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipelines.alerting import record_pipeline_failure, record_pipeline_success

ROOT_DIR = Path(__file__).resolve().parents[1]
PIPELINE_ID = "10_PROMEDIOS_SEMA"

logger = logging.getLogger("promedios_sema")
METRIC_COLUMNS = ["Num_deteccion", "Deteccion", "Ocupacion", "Brecha", "Velocidad"]
PROMEDIOS_COLUMNS = [
    "Nombre",
    "EXT",
    "Acceso",
    "Tipo_sensor",
    "Sensor",
    "Num_deteccion",
    "Deteccion",
    "Ocupacion",
    "Brecha",
    "Velocidad",
    "Dia_sem",
    "Horas",
]
SENSOR_GROUP_BY = ["Nombre", "EXT", "Acceso", "Tipo_sensor", "Sensor", "Dia_sem", "Horas"]
ACCESO_GROUP_BY = ["EXT", "Acceso", "Dia_sem", "Horas"]
EXT_GROUP_BY = ["EXT", "Dia_sem", "Horas"]
SYSTEM_GROUP_BY = ["Dia_sem", "Horas"]
LEVEL_AGGREGATIONS = {
    "sensor": {
        "group_by": SENSOR_GROUP_BY,
        "metrics": {
            "Num_deteccion": "mean",
            "Deteccion": "mean",
            "Ocupacion": "mean",
            "Brecha": "mean",
            "Velocidad": "mean",
        },
    },
    "acceso": {
        "group_by": ACCESO_GROUP_BY,
        "metrics": {
            "Num_deteccion": "sum",
            "Deteccion": "sum",
            "Ocupacion": "mean",
            "Brecha": "mean",
            "Velocidad": "mean",
        },
    },
    "ext": {
        "group_by": EXT_GROUP_BY,
        "metrics": {
            "Num_deteccion": "sum",
            "Deteccion": "sum",
            "Ocupacion": "mean",
            "Brecha": "mean",
            "Velocidad": "mean",
        },
    },
    "system": {
        "group_by": SYSTEM_GROUP_BY,
        "metrics": {
            "Num_deteccion": "sum",
            "Deteccion": "sum",
            "Ocupacion": "mean",
            "Brecha": "mean",
            "Velocidad": "mean",
        },
    },
}
SYSTEM_EXT_LABEL = "PROMEDIO_SISTEMA"


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


def get_env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"Variable {name} debe ser numerica") from exc


def build_oracle_engine():
    user = require_env("ORACLE_USER")
    password = require_env("ORACLE_PASSWORD")
    dsn = require_env("ORACLE_DSN")
    safe_password = quote_plus(password)
    return create_engine(f"oracle+oracledb://{user}:{safe_password}@{dsn}", thick_mode={})


def get_previous_closed_month() -> datetime:
    return datetime.now().replace(day=1) - relativedelta(days=1)


def build_source_table_name(prefix: str, yy_mm: str) -> str:
    return f"{prefix}{yy_mm}"


def iter_source_month_tables(prefix: str, months_back: int = 12) -> list[str]:
    end_month = get_previous_closed_month().replace(day=1)
    tables: list[str] = []
    for delta in range(months_back - 1, -1, -1):
        month = end_month - relativedelta(months=delta)
        tables.append(build_source_table_name(prefix, month.strftime("%y%m")))
    return tables


def normalize_source_frame(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    if frame.empty:
        return frame

    rename_map = {}
    for col in frame.columns:
        if col.lower() == "ext":
            rename_map[col] = "EXT"
        elif col.lower() == "tiempo":
            rename_map[col] = "Tiempo"
    if rename_map:
        frame = frame.rename(columns=rename_map)

    if "Tiempo" in frame.columns:
        frame["Tiempo"] = pd.to_datetime(frame["Tiempo"], errors="coerce")
        frame = frame.dropna(subset=["Tiempo"])

    if "EXT" in frame.columns:
        frame["EXT"] = frame["EXT"].astype(str).str.strip()
        frame = frame[frame["EXT"] != ""]

    numeric_cols = ["Num_deteccion", "Deteccion", "Ocupacion", "Brecha", "Velocidad"]
    for col in numeric_cols:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")

    return frame


def round_grouped_frame(df: pd.DataFrame, group_by: list[str], agg_map: dict[str, str]) -> pd.DataFrame:
    grouped = df.groupby(group_by, as_index=False).agg(agg_map)
    return grouped.round(3)


def empty_promedios_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=PROMEDIOS_COLUMNS)


def promedio_dtype_map() -> dict[str, object]:
    return {
        "Nombre": VARCHAR2(100),
        "EXT": VARCHAR2(20),
        "Acceso": VARCHAR2(30),
        "Tipo_sensor": VARCHAR2(30),
        "Sensor": VARCHAR2(30),
        "Num_deteccion": NUMBER,
        "Deteccion": NUMBER,
        "Ocupacion": FLOAT,
        "Brecha": FLOAT,
        "Velocidad": FLOAT,
        "Dia_sem": VARCHAR2(20),
        "Horas": VARCHAR2(20),
    }


def write_table(engine, dataframe: pd.DataFrame, table_name: str, if_exists: str = "replace") -> None:
    dataframe.to_sql(
        name=table_name,
        con=engine,
        if_exists=if_exists,
        index=False,
        dtype=promedio_dtype_map(),
        chunksize=1000,
    )


def table_exists(engine, table_name: str) -> bool:
    return inspect(engine).has_table(table_name)


def ensure_registry_table(engine, table_name: str) -> None:
    if table_exists(engine, table_name):
        return

    create_table_sql = f"""
    CREATE TABLE {table_name} (
        id NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        ext VARCHAR2(20) NOT NULL,
        tiempo TIMESTAMP NOT NULL,
        procesado VARCHAR2(7) NOT NULL
    )
    """
    create_unique_sql = f"CREATE UNIQUE INDEX {table_name}_uk1 ON {table_name} (ext, procesado)"

    with engine.begin() as connection:
        connection.execute(text(create_table_sql))
        connection.execute(text(create_unique_sql))


def initialize_temp_table(engine, table_name: str) -> None:
    write_table(engine, empty_promedios_frame(), table_name, if_exists="replace")


def get_processed_exts(engine, table_name: str, period: str) -> set[str]:
    if not table_exists(engine, table_name):
        return set()

    query = text(f"SELECT ext FROM {table_name} WHERE procesado = :period")
    frame = pd.read_sql(query, engine, params={"period": period})
    if frame.empty:
        return set()
    ext_col = frame.columns[0]
    return {str(value).strip() for value in frame[ext_col].dropna().tolist() if str(value).strip()}


def clear_registry_period(engine, table_name: str, period: str) -> None:
    if not table_exists(engine, table_name):
        return

    with engine.begin() as connection:
        connection.execute(text(f"DELETE FROM {table_name} WHERE procesado = :period"), {"period": period})


def register_processed_ext(engine, table_name: str, ext_value: str, period: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(f"DELETE FROM {table_name} WHERE ext = :ext AND procesado = :period"),
            {"ext": ext_value, "period": period},
        )
        connection.execute(
            text(f"INSERT INTO {table_name} (ext, tiempo, procesado) VALUES (:ext, :tiempo, :period)"),
            {"ext": ext_value, "tiempo": datetime.now(), "period": period},
        )


def collect_source_exts(engine, source_tables: list[str]) -> tuple[list[str], int, int]:
    ext_values: set[str] = set()
    tables_ok = 0
    tables_skipped = 0

    for table_name in source_tables:
        try:
            query = text(f"SELECT DISTINCT EXT FROM {table_name} WHERE EXT IS NOT NULL")
            frame = pd.read_sql(query, engine)
            if frame.empty:
                tables_skipped += 1
                continue

            ext_col = frame.columns[0]
            for value in frame[ext_col].tolist():
                clean_value = str(value).strip()
                if clean_value:
                    ext_values.add(clean_value)
            tables_ok += 1
        except Exception as exc:
            tables_skipped += 1
            logger.warning("Se omite tabla origen %s detalle=%s", table_name, exc)

    return sorted(ext_values), tables_ok, tables_skipped


def read_month_source_for_ext(engine, table_name: str, ext_value: str) -> pd.DataFrame:
    query = text(f"SELECT * FROM {table_name} WHERE EXT = :ext")
    return normalize_source_frame(pd.read_sql(query, engine, params={"ext": ext_value}))


def aggregate_promedios(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    enriched = df.assign(
        Dia_sem=df["Tiempo"].dt.day_name(),
        Horas=df["Tiempo"].dt.strftime("%H:%M"),
    )

    sensor_level = round_grouped_frame(
        enriched,
        LEVEL_AGGREGATIONS["sensor"]["group_by"],
        LEVEL_AGGREGATIONS["sensor"]["metrics"],
    )

    acceso_level = round_grouped_frame(
        sensor_level,
        LEVEL_AGGREGATIONS["acceso"]["group_by"],
        LEVEL_AGGREGATIONS["acceso"]["metrics"],
    )
    acceso_level["Nombre"] = "PROMEDIO"
    acceso_level["Tipo_sensor"] = "PROMEDIO"
    acceso_level["Sensor"] = "PROMEDIO"
    acceso_level = acceso_level[PROMEDIOS_COLUMNS]

    ext_level = round_grouped_frame(
        sensor_level,
        LEVEL_AGGREGATIONS["ext"]["group_by"],
        LEVEL_AGGREGATIONS["ext"]["metrics"],
    )
    ext_level["Acceso"] = "PROMEDIO_EXT"
    ext_level["Nombre"] = "PROMEDIO"
    ext_level["Tipo_sensor"] = "PROMEDIO"
    ext_level["Sensor"] = "PROMEDIO"
    ext_level = ext_level[PROMEDIOS_COLUMNS]

    sensor_level = sensor_level[PROMEDIOS_COLUMNS]
    return sensor_level, acceso_level, ext_level


def get_monthly_promedios(source_df: pd.DataFrame) -> pd.DataFrame:
    sensor_level, acceso_level, ext_level = aggregate_promedios(source_df)
    return pd.concat([sensor_level, acceso_level, ext_level], ignore_index=True)


def build_ext_temp_rows(engine, source_tables: list[str], ext_value: str) -> tuple[pd.DataFrame, int, int]:
    source_frames: list[pd.DataFrame] = []
    source_rows = 0
    tables_with_data = 0

    for month_table in source_tables:
        try:
            source_df = read_month_source_for_ext(engine, month_table, ext_value)
        except Exception as exc:
            logger.warning("No fue posible leer %s para EXT=%s detalle=%s", month_table, ext_value, exc)
            continue

        if source_df.empty:
            continue

        source_frames.append(source_df)
        source_rows += len(source_df)
        tables_with_data += 1

    if not source_frames:
        return empty_promedios_frame(), 0, 0

    merged_source_df = pd.concat(source_frames, ignore_index=True)
    ext_temp_frame = get_monthly_promedios(merged_source_df)
    return ext_temp_frame, source_rows, tables_with_data


def replace_ext_in_temp(engine, table_name: str, ext_value: str, ext_frame: pd.DataFrame) -> None:
    if not table_exists(engine, table_name):
        initialize_temp_table(engine, table_name)

    with engine.begin() as connection:
        connection.execute(text(f"DELETE FROM {table_name} WHERE EXT = :ext"), {"ext": ext_value})

    write_table(engine, ext_frame, table_name, if_exists="append")


def rebuild_final_table(engine, temp_table: str, final_table: str) -> pd.DataFrame:
    if not table_exists(engine, temp_table):
        raise RuntimeError(f"La tabla temporal {temp_table} no existe")

    det_sema_temp = pd.read_sql(text(f"SELECT * FROM {temp_table}"), engine)
    det_sema_temp = normalize_source_frame(det_sema_temp)

    if det_sema_temp.empty:
        write_table(engine, empty_promedios_frame(), final_table, if_exists="replace")
        return empty_promedios_frame()

    system_source = det_sema_temp[
        (det_sema_temp["Nombre"] == "PROMEDIO")
        & (det_sema_temp["Acceso"] == "PROMEDIO_EXT")
        & (det_sema_temp["Tipo_sensor"] == "PROMEDIO")
        & (det_sema_temp["Sensor"] == "PROMEDIO")
    ].copy()

    final_frames = [det_sema_temp[PROMEDIOS_COLUMNS]]

    if not system_source.empty:
        system_level = round_grouped_frame(
            system_source,
            LEVEL_AGGREGATIONS["system"]["group_by"],
            LEVEL_AGGREGATIONS["system"]["metrics"],
        )
        system_level["Nombre"] = "PROMEDIO"
        system_level["EXT"] = SYSTEM_EXT_LABEL
        system_level["Acceso"] = "PROMEDIO_EXT"
        system_level["Tipo_sensor"] = "PROMEDIO"
        system_level["Sensor"] = "PROMEDIO"
        system_level = system_level[PROMEDIOS_COLUMNS]
        final_frames.append(system_level)

    det_sema_tot = pd.concat(final_frames, ignore_index=True)
    write_table(engine, det_sema_tot, final_table, if_exists="replace")
    return det_sema_tot


def main() -> None:
    load_environment()
    setup_logging()

    engine = build_oracle_engine()
    source_prefix = os.getenv("PROMEDIOS_SEMA_SOURCE_PREFIX", "DET_15M_SEMA_").strip() or "DET_15M_SEMA_"
    temp_table = os.getenv("PROMEDIOS_SEMA_TEMP_TABLE", "prom_det_sema_temp").strip() or "prom_det_sema_temp"
    final_table = os.getenv("PROMEDIOS_SEMA_FINAL_TABLE", "prom_det_sema_tot").strip() or "prom_det_sema_tot"
    registry_table = os.getenv("PROMEDIOS_SEMA_REG_TABLE", "prom_reg_ext").strip() or "prom_reg_ext"
    months_back = get_env_int("PROMEDIOS_SEMA_MONTHS_BACK", 12)
    progress_every = max(1, get_env_int("PROMEDIOS_SEMA_PROGRESS_EVERY", 1))

    target_month = get_previous_closed_month()
    target_period = target_month.strftime("%Y-%m")
    source_tables = iter_source_month_tables(source_prefix, months_back=months_back)

    ensure_registry_table(engine, registry_table)

    print(
        f"Inicio {PIPELINE_ID} | periodo_objetivo={target_period} | meses={months_back} | tablas_fuente={len(source_tables)}"
    )
    print("Tablas fuente:", ", ".join(source_tables))

    all_exts, source_tables_ok, source_tables_skipped = collect_source_exts(engine, source_tables)
    if not all_exts:
        raise RuntimeError("No se encontraron externos en las tablas fuente para calcular promedios")

    print(
        f"Externos confirmados={len(all_exts)} | tablas_con_ext={source_tables_ok} | tablas_omitidas={source_tables_skipped}"
    )

    processed_exts = get_processed_exts(engine, registry_table, target_period)
    temp_exists = table_exists(engine, temp_table)

    if processed_exts and not temp_exists:
        print(
            f"Advertencia: hay {len(processed_exts)} externos marcados en {registry_table} para {target_period}, "
            f"pero no existe {temp_table}. Se reinicia el periodo."
        )
        clear_registry_period(engine, registry_table, target_period)
        processed_exts = set()

    if not processed_exts:
        initialize_temp_table(engine, temp_table)
        print(f"Reinicio controlado de {temp_table} para el periodo {target_period}")

    pending_exts = [ext_value for ext_value in all_exts if ext_value not in processed_exts]
    print(
        f"Estado inicial | procesados={len(processed_exts)} | pendientes={len(pending_exts)} | total={len(all_exts)}"
    )

    if not pending_exts:
        if not table_exists(engine, final_table):
            print(f"{final_table} no existe. Se regenera desde {temp_table}.")
            det_sema_tot = rebuild_final_table(engine, temp_table, final_table)
            print(f"Cargue final regenerado | filas_final={len(det_sema_tot)}")
        print(f"Promedios actualizados para el periodo {target_period}. No hay externos pendientes.")
        return

    total_exts = len(all_exts)
    initial_processed_count = len(processed_exts)

    successful_exts_in_run = 0

    for idx, ext_value in enumerate(pending_exts, start=1):
        print(f"Procesando EXT={ext_value} | pendiente={idx}/{len(pending_exts)}")
        ext_temp_frame, source_rows, source_tables_with_data = build_ext_temp_rows(engine, source_tables, ext_value)

        if ext_temp_frame.empty:
            print(f"EXT={ext_value} sin datos utilizables en el horizonte de {months_back} meses. Se omite.")
            continue

        replace_ext_in_temp(engine, temp_table, ext_value, ext_temp_frame)
        register_processed_ext(engine, registry_table, ext_value, target_period)
        successful_exts_in_run += 1

        completed_count = initial_processed_count + successful_exts_in_run
        progress_pct = (completed_count / total_exts) * 100
        if idx == 1 or idx % progress_every == 0 or idx == len(pending_exts):
            print(
                f"Temporal actualizada | EXT={ext_value} | filas_ext={len(ext_temp_frame)} | "
                f"filas_fuente={source_rows} | tablas_con_datos={source_tables_with_data}"
            )
            print(
                f"Registro actualizado en {registry_table} | EXT={ext_value} | procesado={target_period} | "
                f"avance={completed_count}/{total_exts} ({progress_pct:.1f}%)"
            )

    processed_after_run = get_processed_exts(engine, registry_table, target_period)
    pending_after_run = [ext_value for ext_value in all_exts if ext_value not in processed_after_run]
    print(
        f"Cargue temporal finalizado | procesados={len(processed_after_run)} | pendientes={len(pending_after_run)}"
    )

    if pending_after_run:
        print(
            f"Quedaron externos pendientes para {target_period}. Se continuara en la siguiente ventana: "
            + ", ".join(pending_after_run[:10])
            + (" ..." if len(pending_after_run) > 10 else "")
        )
        return

    det_sema_tot = rebuild_final_table(engine, temp_table, final_table)
    print(
        f"Cargue final completado | periodo={target_period} | tabla_temp={temp_table} | "
        f"tabla_final={final_table} | filas_final={len(det_sema_tot)}"
    )
    logger.info(
        "Se actualizaron promedios periodo=%s temp=%s final=%s externos=%s filas_final=%s",
        target_period,
        temp_table,
        final_table,
        len(processed_after_run),
        len(det_sema_tot),
    )


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
