from __future__ import annotations

import logging
import os
import re
import sys
import unicodedata
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


def normalizar_columna(col: str) -> str:
    col = "".join(
        c for c in unicodedata.normalize("NFD", str(col))
        if unicodedata.category(c) != "Mn"
    )
    col = col.lower()
    col = re.sub(r"[^a-z0-9]+", "_", col)
    return col.strip("_")


def normalize_union_columns(union: pd.DataFrame) -> pd.DataFrame:
    df = union.copy()
    rename_map = {}
    for col in df.columns:
        normalized = normalizar_columna(col)
        if normalized == "cod_id":
            rename_map[col] = "COD_ID"
        elif normalized == "externo":
            rename_map[col] = "EXTERNO"
        elif normalized == "direccion":
            rename_map[col] = "DIRECCION"
        elif normalized == "zona_auto":
            rename_map[col] = "ZONA_AUTO"
        elif normalized == "localidad":
            rename_map[col] = "LOCALIDAD"
        elif normalized == "equipo":
            rename_map[col] = "EQUIPO"
        elif normalized == "interseccion":
            rename_map[col] = "INTERSECCION"
        elif normalized == "operacion":
            rename_map[col] = "OPERACION"
        elif normalized == "num_wide":
            rename_map[col] = "NUM_WIDE"
        elif normalized == "num_narrow":
            rename_map[col] = "NUM_NARROW"
        elif normalized == "shutdown":
            rename_map[col] = "SHUTDOWN"
        elif normalized == "longitud":
            rename_map[col] = "LONGITUD"
        elif normalized == "latitud":
            rename_map[col] = "LATITUD"
        elif normalized == "estado":
            rename_map[col] = "ESTADO"
        elif normalized == "fecha":
            rename_map[col] = "FECHA"
        elif normalized == "causa":
            rename_map[col] = "causa"
        elif normalized == "id_de_solicitud":
            rename_map[col] = "id_de_solicitud"
        elif normalized == "estado_de_la_interseccion":
            rename_map[col] = "estado_de_la_interseccion"
        elif normalized == "tiempo_transcurrido":
            rename_map[col] = "tiempo_transcurrido"
    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def drop_novedad_columns(df: pd.DataFrame) -> pd.DataFrame:
    cols_to_drop = [
        col
        for col in df.columns
        if normalizar_columna(col)
        in {
            "causa",
            "id_de_solicitud",
            "estado_de_la_interseccion",
            "tiempo_transcurrido",
            "nei",
            "causa_x",
            "causa_y",
            "id_de_solicitud_x",
            "id_de_solicitud_y",
            "estado_de_la_interseccion_x",
            "estado_de_la_interseccion_y",
            "tiempo_transcurrido_x",
            "tiempo_transcurrido_y",
        }
        or col.upper().endswith(("_X", "_Y"))
    ]
    if cols_to_drop:
        return df.drop(columns=cols_to_drop, errors="ignore")
    return df


def normalize_registro_columns(registro: pd.DataFrame) -> pd.DataFrame:
    df = registro.copy()
    rename_map = {}
    for col in df.columns:
        normalized = normalizar_columna(col)
        if normalized == "size_in_mb":
            rename_map[col] = "size_in_MB"
        elif normalized == "id":
            rename_map[col] = "id"
        elif normalized == "name":
            rename_map[col] = "name"
        elif normalized == "creation":
            rename_map[col] = "creation"
        elif normalized == "last_modification":
            rename_map[col] = "last_modification"
        elif normalized == "type_of_file":
            rename_map[col] = "type_of_file"
        elif normalized == "date":
            rename_map[col] = "date"
        elif normalized == "guia":
            rename_map[col] = "guia"
        elif normalized == "estado":
            rename_map[col] = "Estado"
        elif normalized == "num":
            rename_map[col] = "num"
    if rename_map:
        df = df.rename(columns=rename_map)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    if "guia" in df.columns:
        df["guia"] = pd.to_numeric(df["guia"], errors="coerce").astype("Int64")
    if "num" in df.columns:
        df["num"] = pd.to_numeric(df["num"], errors="coerce").astype("Int64")
    return df


def load_novedad_sheet(
    gspread_client: gspread.Client,
    sheet_url: str,
    worksheet_name: str,
    data_range: str,
) -> pd.DataFrame:
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
    raw = worksheet.batch_get((data_range,))[0]
    if not raw or len(raw) < 2:
        return pd.DataFrame()
    novedad_sema = pd.DataFrame.from_records(raw[1:], columns=raw[0])
    novedad_sema.columns = [normalizar_columna(col) for col in novedad_sema.columns]
    return novedad_sema


def enrich_union_with_novedades(union: pd.DataFrame, novedad_sema: pd.DataFrame) -> pd.DataFrame:
    if union.empty:
        return union
    union = drop_novedad_columns(normalize_union_columns(union))
    externo_col = "EXTERNO" if "EXTERNO" in union.columns else "externo" if "externo" in union.columns else None
    if externo_col is None:
        logger.warning("La union no contiene columna EXTERNO para cruzar novedades")
        return union
    if novedad_sema.empty or "nei" not in novedad_sema.columns:
        for col in ["causa", "id_de_solicitud", "estado_de_la_interseccion", "tiempo_transcurrido"]:
            if col not in union.columns:
                union[col] = ""
        return union

    novedad = novedad_sema.copy()
    if "estado_de_la_interseccion" in novedad.columns:
        novedad = novedad[novedad["estado_de_la_interseccion"] != "EN SERVICIO"]

    cols_base = [col for col in ["id", "prioridad", "nei"] if col in novedad.columns]
    if cols_base:
        novedad[cols_base] = novedad[cols_base].replace(r"^\s*$", pd.NA, regex=True)
        novedad = novedad.dropna(subset=cols_base, how="all")

    if "tiempo_transcurrido" in novedad.columns:
        novedad["tiempo_transcurrido"] = pd.to_timedelta(
            novedad["tiempo_transcurrido"],
            errors="coerce",
        )
        novedad["tiempo_transcurrido"] = novedad["tiempo_transcurrido"].apply(
            lambda x: (
                f"{int(x.total_seconds() // 3600):02}:"
                f"{int((x.total_seconds() % 3600) // 60):02}:"
                f"{int(x.total_seconds() % 60):02}"
            )
            if pd.notna(x)
            else None
        )

    cols_novedad = [col for col in ["nei", "causa", "id_de_solicitud", "estado_de_la_interseccion", "tiempo_transcurrido"] if col in novedad.columns]
    if not cols_novedad:
        return union

    novedad_subset = novedad[cols_novedad].drop_duplicates(subset=["nei"], keep="last").copy()
    novedad_subset = novedad_subset.rename(
        columns={
            "causa": "CAUSA",
            "id_de_solicitud": "ID_DE_SOLICITUD",
            "estado_de_la_interseccion": "ESTADO_DE_LA_INTERSECCION",
            "tiempo_transcurrido": "TIEMPO_TRANSCURRIDO",
        }
    )
    novedad_subset["NEI_KEY"] = novedad_subset["nei"].astype(str)

    merged = union.copy()
    merged["NEI_KEY"] = merged[externo_col].astype(str)
    merged = merged.merge(novedad_subset, how="left", on="NEI_KEY")
    if "NEI_KEY" in merged.columns:
        merged = merged.drop(columns=["NEI_KEY", "nei"], errors="ignore")

    default_values = {
        "CAUSA": "",
        "ID_DE_SOLICITUD": "",
        "ESTADO_DE_LA_INTERSECCION": "EN SERVICIO",
        "TIEMPO_TRANSCURRIDO": "",
    }
    for column, default_value in default_values.items():
        if column not in merged.columns:
            merged[column] = default_value
        else:
            merged[column] = merged[column].fillna(default_value)
    return merged


def load_registry_sheet(gspread_client: gspread.Client, sheet_url: str, worksheet_name: str) -> pd.DataFrame:
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
    raw = worksheet.get_all_values()
    if not raw:
        return pd.DataFrame(columns=REQUIRED_REGISTRY_COLUMNS)
    registro_df = pd.DataFrame.from_records(raw)
    registro_df.columns = registro_df.iloc[0]
    registro_df = registro_df.drop(registro_df.index[0]).reset_index(drop=True)
    return ensure_registry_columns(registro_df)


def load_registry_backup_from_oracle(engine, table_name: str) -> pd.DataFrame:
    logger.warning(
        "Registro Sheet vacio o invalido; se intentara recuperar respaldo Oracle desde %s",
        table_name,
    )
    registro_df = pd.read_sql(f"SELECT * FROM {table_name}", engine)
    if registro_df.empty:
        raise DataAvailabilityError(
            f"El respaldo Oracle {table_name} esta vacio y no permite reconstruir el registro"
        )
    return ensure_registry_columns(registro_df)


def load_registry_with_oracle_fallback(
    gspread_client: gspread.Client,
    sheet_url: str,
    worksheet_name: str,
    engine,
    oracle_table_name: str,
) -> pd.DataFrame:
    try:
        worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
        raw = worksheet.get_all_values()
        if not raw:
            return load_registry_backup_from_oracle(engine, oracle_table_name)

        registro_df = pd.DataFrame.from_records(raw)
        if registro_df.empty or registro_df.shape[1] == 0:
            return load_registry_backup_from_oracle(engine, oracle_table_name)

        header = registro_df.iloc[0].astype(str).str.strip()
        if not header.any():
            return load_registry_backup_from_oracle(engine, oracle_table_name)

        registro_df.columns = registro_df.iloc[0]
        registro_df = registro_df.drop(registro_df.index[0]).reset_index(drop=True)
        return ensure_registry_columns(registro_df)
    except (IndexError, ValueError) as exc:
        logger.warning(
            "No fue posible leer la estructura del Registro Sheet; se usara respaldo Oracle. detalle=%s",
            exc,
        )
        return load_registry_backup_from_oracle(engine, oracle_table_name)


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
    novedades_sheet_url = os.getenv("NOVEDADES_SEMA_SHEET_URL", sheet_url).strip() or sheet_url
    novedades_tab = os.getenv("NOVEDADES_SEMA_WORKSHEET", "UNIDADES DE TRANSITO").strip() or "UNIDADES DE TRANSITO"
    novedades_range = os.getenv("NOVEDADES_SEMA_DATA_RANGE", "A3:N150000").strip() or "A3:N150000"
    novedades_table = os.getenv("NOVEDADES_SEMA_ORACLE_TABLE", "noved_central_sema").strip() or "noved_central_sema"

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
    select_template = """SELECT * FROM EST_ACT_SEMA"""
    actual = pd.read_sql(select_template, engine)
    actual = normalize_union_columns(actual)
    union = actual.copy() if not actual.empty else base.copy()
    union = drop_novedad_columns(normalize_union_columns(union))
    for col in ["CAUSA", "ID_DE_SOLICITUD", "ESTADO_DE_LA_INTERSECCION", "TIEMPO_TRANSCURRIDO"]:
        if col not in union.columns:
            union[col] = ""

    registro_df = load_registry_with_oracle_fallback(
        gspread_client,
        sheet_url,
        registro_tab,
        engine,
        "est_sua_reg_sema",
    )
    registro_df = normalize_registro_columns(registro_df)
    result_df = pd.DataFrame(columns=["ESTADO", "EXTERNO", "FECHA"])
    registro_n = registro_df.copy()
    drive_df = list_drive_files(drive_service, drive_folder_id)
    if drive_df.empty:
        logger.info("No se encontraron archivos CSV en la carpeta origen")
    else:
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

        if ids:
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

            if parsed_data:
                result_df = pd.concat(parsed_data, axis=0).drop_duplicates()
                result_df = result_df[result_df["EXTERNO"] != "Off"]
                result_df["EXTERNO"] = result_df["EXTERNO"].astype(str)
                num_reg = pd.concat(count_data, axis=0)

                last_time = pending["date"].max()
                ultimo = result_df[result_df["FECHA"] == last_time].copy()
                union = pd.merge(base, ultimo, on="EXTERNO", how="left").fillna(
                    {"ESTADO": "Operando", "FECHA": last_time}
                )
                union = drop_novedad_columns(normalize_union_columns(union))
                for col in ["CAUSA", "ID_DE_SOLICITUD", "ESTADO_DE_LA_INTERSECCION", "TIEMPO_TRANSCURRIDO"]:
                    if col not in union.columns:
                        union[col] = ""

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
                registro_n = normalize_registro_columns(registro_n)
                registro_n = registro_n.sort_values(by="date", ascending=False)
            else:
                logger.info("No se lograron parsear estados desde los archivos pendientes")
        else:
            logger.info("No hay registros de estados nuevos - SEMA")

    # Siempre refresca novedades, haya o no correos nuevos.
    novedad_sema = load_novedad_sheet(
        gspread_client,
        novedades_sheet_url,
        novedades_tab,
        novedades_range,
    )
    union = enrich_union_with_novedades(union, novedad_sema)

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
    registro_n = normalize_registro_columns(registro_n)
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
        "CAUSA": VARCHAR2(300),
        "ID_DE_SOLICITUD": VARCHAR2(80),
        "ESTADO_DE_LA_INTERSECCION": VARCHAR2(80),
        "TIEMPO_TRANSCURRIDO": VARCHAR2(20),
    }
    union.to_sql(
        name="est_act_sema",
        con=engine,
        if_exists="replace",
        index=False,
        dtype=dtype_act,
    )

    union.to_sql(
        name="est_sua_act_sema",
        con=engine,
        if_exists="replace",
        index=False,
        dtype=dtype_act,
    )

    if not result_df.empty:
        dtype_hist = {"ESTADO": VARCHAR2(80), "EXTERNO": VARCHAR2(50), "FECHA": TIMESTAMP}
        result_df.to_sql(
            name="est_sua_hist_sema",
            con=engine,
            if_exists="append",
            index=False,
            dtype=dtype_hist,
        )
        logger.info("Se actualizan %s registro(s) de estados - SEMA", len(ids))
    else:
        logger.info("Se actualiza EST_ACT_SEMA desde Oracle + novedades de sheet")

    write_dataframe_to_sheet(gspread_client, sheet_url, registro_tab, registro_n)
    write_dataframe_to_sheet(gspread_client, sheet_url, estados_tab, union)


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
