from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import numpy as np

pd.options.mode.chained_assignment = None  # default='warn'
import gspread
from dotenv import load_dotenv
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import requests
import gspread_dataframe as gd
import io
from scipy.spatial.distance import pdist, squareform
from sqlalchemy.engine import create_engine
from sqlalchemy.dialects.oracle import (FLOAT,NUMBER,VARCHAR2,TIMESTAMP,DATE,VARCHAR)
from sqlalchemy import text

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.colombia_holidays import build_special_dates
from pipelines.detecciones_file_rules import classify_drive_files
from pipelines.fin_sem_file_rules import (
    REQUIRED_COLUMNS,
    build_fin_sem_pending_preview,
    classify_fin_sem_drive_files,
    ensure_required_columns,
    list_drive_files,
    mark_registry_processed,
    sort_registry_for_output,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/bigquery",
]
DETECTOR_EXPORT_NAME_PATTERN = (
    r"(?i)^(?:Detektor-Messquerschnitt|Detector-Measurement Point-Traffic Data - Processed|"
    r"Detector-Punto de medici[oó]n-Datos de tr[aá]fico - procesados).+\.csv$"
)
DETECTOR_EXPORT_HISTORICAL_PATTERN = (
    r"(?i)^(?:Detektor-Messquerschnitt|Detector-Measurement Point-Traffic Data - Processed|"
    r"Detector-Punto de medici[oó]n-Datos de tr[aá]fico - procesados)-(\d{6})_\1\.csv$"
)


class ConfigurationError(RuntimeError):
    """Error funcional para variables de entorno faltantes o invalidas."""


def load_environment() -> None:
    load_dotenv(ROOT_DIR / "config" / ".env")
    load_dotenv(ROOT_DIR / ".env")


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Variable de entorno requerida no definida: {name}")
    return value


def require_any_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    raise ConfigurationError(
        f"No se encontro ninguna variable de entorno valida en: {', '.join(names)}"
    )


def parse_scopes(raw_scopes: str | None) -> list[str]:
    if not raw_scopes:
        return DEFAULT_SCOPES
    return [scope.strip() for scope in raw_scopes.split(",") if scope.strip()]


def parse_csv_env(name: str) -> list[str]:
    return [item.strip() for item in require_env(name).split(",") if item.strip()]


def parse_int_csv_env(name: str) -> list[int]:
    return [int(item) for item in parse_csv_env(name)]


def parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "si", "on"}


def is_detector_export_name(series: pd.Series) -> pd.Series:
    return series.astype(str).str.match(DETECTOR_EXPORT_NAME_PATTERN, na=False)


def extract_historical_export_date(series: pd.Series) -> pd.Series:
    return series.astype(str).str.extract(DETECTOR_EXPORT_HISTORICAL_PATTERN, expand=True).iloc[:, 0]


def build_google_clients(scopes: list[str]):
    credentials_path = require_env("GOOGLE_APPLICATION_CREDENTIALS")
    credentials_file = (ROOT_DIR / credentials_path).resolve()
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_file),
        scopes=scopes,
    )
    credentials.refresh(Request())
    gspread_client = gspread.service_account(filename=str(credentials_file))
    drive_service = build("drive", "v3", credentials=credentials)
    return gspread_client, drive_service, credentials.token


def build_oracle_engine():
    user = require_env("ORACLE_USER")
    password = require_env("ORACLE_PASSWORD")
    dsn = require_env("ORACLE_DSN")
    safe_password = quote_plus(password)
    return create_engine(f"oracle+oracledb://{user}:{safe_password}@{dsn}", thick_mode={})


def load_fin_sem_settings() -> dict[str, object]:
    return {
        "years": parse_int_csv_env("FIN_SEM_SEMA_YEARS"),
        "include_friday": parse_bool_env("FIN_SEM_SEMA_INCLUDE_FRIDAY", default=True),
        "mapping_path": require_env("FIN_SEM_SEMA_REGLAS_PATH"),
        "registro_sheet_url": require_any_env(
            "FIN_SEM_SEMA_REGISTRO_SHEET_URL",
            "GOOGLE_DETECCIONES_SHEET_URL",
        ),
        "registro_worksheet": require_env("FIN_SEM_SEMA_REGISTRO_WORKSHEET"),
        "historico_folder_id": require_any_env(
            "GOOGLE_DETECCIONES_DRIVE_FOLDER_ID",
            "FIN_SEM_SEMA_HISTORICO_DRIVE_FOLDER_ID",
        ),
        "hoy_folder_id": require_env("FIN_SEM_SEMA_HOY_DRIVE_FOLDER_ID"),
        "min_size_mb": float(os.getenv("DETECCIONES_SEMA_MIN_SIZE_MB", "50")),
        "max_size_mb": float(os.getenv("DETECCIONES_SEMA_MAX_SIZE_MB", "100")),
        "target_size_mb": float(os.getenv("DETECCIONES_SEMA_TARGET_SIZE_MB", "67")),
    }


def safe_to_float(series):
    return (
        series
        .astype(str)
        .str.strip()
        .str.replace(",", ".", regex=False)
        .where(lambda x: x.str.match(r"^-?\d+(\.\d+)?$"))
        .astype(float)
    )


def build_fecha_ini(
    dates_list: list[date],
    mapping_df: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    base_pairs = (
        mapping_df[["Analisis", "Corredor"]]
        .drop_duplicates()
        .sort_values(["Analisis", "Corredor"])
        .itertuples(index=False)
    )
    for pair in base_pairs:
        for current_date in dates_list:
            rows.append([pair.Analisis, pair.Corredor, current_date])
    fecha_ini = pd.DataFrame(rows, columns=["Analisis", "Corredor", "Tiempo"])
    fecha_ini["Tiempo"] = pd.to_datetime(fecha_ini["Tiempo"]).dt.date
    return fecha_ini


def load_analysis_mapping(config_path: str) -> tuple[pd.DataFrame, list[str]]:
    mapping_file = (ROOT_DIR / config_path).resolve()
    if not mapping_file.exists():
        raise ConfigurationError(f"No existe el archivo de reglas: {mapping_file}")
    mapping_df = pd.read_csv(mapping_file, dtype=str).fillna("")
    mapping_df.columns = [column.strip() for column in mapping_df.columns]
    required_columns = {"Externo", "Corredor", "Sensor", "Analisis"}
    missing_columns = required_columns - set(mapping_df.columns)
    if missing_columns:
        raise ConfigurationError(
            f"Faltan columnas requeridas en reglas FIN SEM SEMA: {', '.join(sorted(missing_columns))}"
        )
    for column in required_columns:
        mapping_df[column] = mapping_df[column].astype(str).str.strip()
    mapping_df = mapping_df[
        (mapping_df["Externo"] != "")
        & (mapping_df["Corredor"] != "")
        & (mapping_df["Sensor"] != "")
        & (mapping_df["Analisis"] != "")
    ].copy()
    duplicates = mapping_df.duplicated(subset=["Externo", "Sensor"], keep=False)
    if duplicates.any():
        duplicated_rows = mapping_df.loc[duplicates, ["Externo", "Sensor"]].drop_duplicates()
        raise ConfigurationError(
            "La tabla de reglas FIN SEM SEMA tiene combinaciones repetidas de Externo/Sensor: "
            + "; ".join(
                f"{row.Externo}/{row.Sensor}" for row in duplicated_rows.itertuples(index=False)
            )
        )
    externos = sorted(mapping_df["Externo"].unique().tolist())
    return mapping_df, externos


def assign_corredor_analisis(df_fines: pd.DataFrame, mapping_df: pd.DataFrame) -> pd.DataFrame:
    mapped_df = df_fines.merge(
        mapping_df,
        left_on=["EXT", "Sensor"],
        right_on=["Externo", "Sensor"],
        how="inner",
    )
    if mapped_df.empty:
        return mapped_df
    return mapped_df.drop(columns=["Externo"])


def enrich_hourly_output(df_hora: pd.DataFrame) -> pd.DataFrame:
    enriched = df_hora.copy()
    enriched["Dia_sem"] = enriched["Tiempo"].dt.day_name()
    enriched["Hora"] = enriched["Tiempo"].dt.hour
    enriched["Semana"] = (
        (
            enriched["Tiempo"]
            - pd.to_timedelta(enriched["Tiempo"].dt.dayofweek, unit="D")
            + timedelta(days=4)
        ).dt.date.astype(str)
        + " to "
        + (
            (
                enriched["Tiempo"]
                - pd.to_timedelta(enriched["Tiempo"].dt.dayofweek, unit="D")
            ).dt.date
            + timedelta(days=4)
            + timedelta(days=3)
        ).astype(str)
    )
    enriched["Fecha"] = enriched["Tiempo"].dt.strftime("%Y/%m/%d")
    enriched["period"] = enriched[["Analisis", "Corredor", "Fecha"]].agg("-".join, axis=1)
    enriched["Fecha_2"] = enriched["Tiempo"].dt.floor("D")
    return enriched[
        [
            "Tiempo",
            "EXT",
            "Acceso",
            "Sensor",
            "Corredor",
            "Analisis",
            "Deteccion",
            "Dia_sem",
            "Hora",
            "Semana",
            "Fecha",
            "Fecha_2",
            "period",
        ]
    ]


def build_pivot_base(df_hora: pd.DataFrame, dias: dict[str, int]) -> pd.DataFrame:
    pivot_base = df_hora.copy()
    pivot_base["Tiempo"] = pd.to_datetime(pivot_base["Tiempo"], errors="coerce")
    pivot_base["dia_n"] = pivot_base["Dia_sem"].map(dias)
    pivot_base["label"] = (
        pivot_base["dia_n"].astype(str)
        + "."
        + pivot_base["Dia_sem"].str[:3]
        + " "
        + pivot_base["Hora"].astype(str).str.zfill(2)
        + "H"
    )
    pivot_base["Deteccion"] = pivot_base["Deteccion"].replace(0, np.nan)
    pivot_base["Fecha"] = pivot_base["Tiempo"].dt.strftime("%Y/%m/%d")
    return pivot_base[
        [
            "Tiempo",
            "EXT",
            "Acceso",
            "Sensor",
            "Corredor",
            "Analisis",
            "Dia_sem",
            "Hora",
            "label",
            "Deteccion",
            "Fecha",
            "Semana",
        ]
    ]


def load_sheet_records(
    gspread_client: gspread.Client,
    sheet_url: str,
    worksheet_name: str,
) -> pd.DataFrame:
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
    data = worksheet.get_all_values()
    if not data:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    registro = pd.DataFrame.from_records(data)
    registro.columns = registro.iloc[0]
    registro = registro.drop(registro.index[0]).reset_index(drop=True)
    registro = ensure_required_columns(registro)
    registro["num"] = registro["num"].replace("", "0", regex=True)
    registro["size_in_MB"] = registro["size_in_MB"].replace("", "0", regex=True)
    registro["size_in_MB"] = registro["size_in_MB"].replace(",", ".", regex=True)
    registro["mes"] = registro["mes"].replace("", "0", regex=True)
    registro["size_in_MB"] = pd.to_numeric(registro["size_in_MB"], errors="coerce").fillna(0.0)
    registro["mes"] = pd.to_numeric(registro["mes"], errors="coerce").fillna(0).astype(int)
    registro["num"] = pd.to_numeric(registro["num"], errors="coerce").fillna(0).astype(int)
    registro["date"] = pd.to_datetime(registro["date"], errors="coerce")
    return sort_registry_for_output(registro)


def replace_sheet_records(
    gspread_client: gspread.Client,
    sheet_url: str,
    worksheet_name: str,
    dataframe: pd.DataFrame,
) -> None:
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
    data = worksheet.get_all_values()
    worksheet.delete_rows(1, len(data) - 1)
    worksheet = gspread_client.open_by_url(sheet_url).worksheet(worksheet_name)
    worksheet.add_rows(dataframe.shape[0])
    gd.set_with_dataframe(worksheet=worksheet, dataframe=dataframe)


def sync_fin_sem_registry(
    registro_df: pd.DataFrame,
    drive_df: pd.DataFrame,
    special_dates: list[date],
    min_size_mb: float,
    max_size_mb: float,
    target_size_mb: float,
) -> pd.DataFrame:
    registro = classify_fin_sem_drive_files(
        drive_df=drive_df,
        existing_registry=registro_df,
        special_dates=special_dates,
        min_size_mb=min_size_mb,
        max_size_mb=max_size_mb,
        target_size_mb=target_size_mb,
    )
    registro["num"] = pd.to_numeric(registro["num"], errors="coerce").fillna(0).astype(int)
    return sort_registry_for_output(registro)

def main():
    
    load_environment()
    scopes = parse_scopes(os.getenv("GOOGLE_SCOPES"))
    gc, service, access_token = build_google_clients(scopes)
    engine = build_oracle_engine()
    settings = load_fin_sem_settings()
    mapping_df, Externos = load_analysis_mapping(str(settings["mapping_path"]))
    dates_list = build_special_dates(
        list(settings["years"]),
        bool(settings["include_friday"]),
    )
    Fecha_ini = build_fecha_ini(dates_list, mapping_df)


    #02 FUNCIONES DE UTILIDAD 

    # Se tienen las funciones necesarias para el funcionamiento

    ## función de Cambio.
    def perc_change(u, v):
        return (v - u) / u

    # se van a seleccionar las fechas de 2024 y 2025 hacia el final de año incluir 2026
    #importante inlcuir los festivos en la lista a continuacion



    #Es necesario marcar los externos que van a ser utilizados en el análisis
    dias = {'Friday':1, 'Saturday':2,'Sunday':3, 'Monday':4, 'Tuesday':5, 'Wednesday':6, 'Thursday':7}

    # 03 CONSULTA A LAS BASES DE REGISTRO - QUE HAY YA

    # Se va aconsultar la base donde estan todas las detecciones
    registro_sheet_url = str(settings["registro_sheet_url"])
    registro_worksheet = str(settings["registro_worksheet"])
    Registro = load_sheet_records(gc, registro_sheet_url, registro_worksheet)

    ## Conexión con la carpeta donde se localiza los archivos de volúmenes, esta debe ser actualizada antes de correr el código.
    historico_df = list_drive_files(service, str(settings["historico_folder_id"]))


    Registro = sync_fin_sem_registry(
        registro_df=Registro,
        drive_df=historico_df,
        special_dates=dates_list,
        min_size_mb=float(settings["min_size_mb"]),
        max_size_mb=float(settings["max_size_mb"]),
        target_size_mb=float(settings["target_size_mb"]),
    )

    select_data, max_date = build_fin_sem_pending_preview(Registro, dates_list)
    select_data = select_data.copy()
    if not select_data.empty:
        select_data["date"] = pd.to_datetime(select_data["date"], errors="coerce").dt.date
    ids = select_data['id'].tolist()

    if not ids:  #En caso de que la lista este vacia
        dt_min =datetime.today().date().strftime('%d/%m/%y')

    else:
        select_data['date']= pd.to_datetime(select_data['date'])
        dt_min =select_data['date'].min().strftime('%d/%m/%y')
        
    data=[]
    registros_count = []
    
    # 031 BORRAR REGISTROS TRANSACCIONES (RESIDUOS DEL DIA INCOMPLETOS) PARA FECHAS FALTANTES
    engine = build_oracle_engine()

    stmt1P = text(f'''DELETE FROM FIN_DIA_SEMA WHERE "Tiempo" >= TO_DATE('{dt_min}','DD/MM/RR')''')
    stmt2P = text(f'''DELETE FROM FIN_HORA_SEMA WHERE "Tiempo" >= TO_DATE('{dt_min}','DD/MM/RR')''')
    stmt3P = text(f'''DELETE FROM FIN_PIVOT_SEMA WHERE "Tiempo" >= TO_DATE('{dt_min}','DD/MM/RR') AND "Semana" <> 'Promedio' ''')
    stmt4P = text(f'''DELETE FROM FIN_SEG_SEMA WHERE "Tiempo" >= TO_DATE('{dt_min}','DD/MM/RR')''')

    with engine.connect() as connection:
        connection.execute(stmt1P)
        connection.execute(stmt2P)
        connection.execute(stmt3P)
        connection.execute(stmt4P)
        connection.commit()
        
        print(f"Se borran los registros por encima a la seleccion a la fecha {dt_min}")
        
    #032 SE PROCESAN LAS DETECCIONES DE FECHAS DIFERENTES A EL DIA DE HOY
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

    if not ids:  #En caso de que la lista este vacia
        print("No hay registros nuevos")
    else:
        for file_id in ids:
            url = "https://www.googleapis.com/drive/v3/files/" + file_id + "?alt=media"
            res = requests.get(url, headers={"Authorization": "Bearer " + access_token})
            df = pd.read_csv(io.StringIO(res.text),sep=';',encoding='latin1')

            for col in numeric_cols:
                if col in df.columns:
                    df[col] = safe_to_float(df[col])
            df[numeric_cols] = df[numeric_cols].fillna(0)

            df=df.iloc[:,:8].drop(['UTC'], axis=1)
            df=df.set_axis(['Nombre', 'Tiempo', 'Sensor','Deteccion','Estado','Num_deteccion','Ocupacion'], axis='columns')
            df = df.query('(Num_deteccion > 8) & ~(Ocupacion <= 0.01 & Deteccion > 1)')
            df['Tiempo']= pd.to_datetime(df['Tiempo'], format='%d.%m.%Y %H:%M:%S')- timedelta(hours=0, minutes=1)
            # df.loc[:,'IG']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 1].str.extract('(\\d+)').astype(str)
            df.loc[:,'EXT']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 2].str.extract('(\\d+)').astype(str)
            # df.loc[:,'Movimiento']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 3]
            df.loc[:,'Acceso']=df['Sensor'].str.extract('(\\d+(?=.))').iloc[:, 0].str.extract(r'([0-9]+)').infer_objects().fillna(0).astype(str)
            df.loc[df['Acceso']== '0', 'Acceso'] = df['Sensor'].str.extract('(\\d+)').iloc[:, 0]
            df.loc[:,'Tipo_sensor']=df['Sensor'].str.extract('(^[A-Z])').iloc[:, 0]
            df=df.drop(['Estado'], axis=1)
            df.loc[:,'Deteccion']=np.round((df['Deteccion']/4), 0).astype(int)
            # df.loc[:,'Brecha'] =np.where((df['Deteccion'] == 0)|(df['Ocupacion'] == 0),0, np.round((900/df['Deteccion'])-((900*(df['Ocupacion']/100))/df['Deteccion']),3))
            # df.loc[:,'Velocidad'] =np.where((df['Deteccion'] == 0)|(df['Ocupacion'] == 0),0, np.round(6/((900*(df['Ocupacion']/100))/df['Deteccion'])*3.6,3))
            # df.loc[:,'Ocupacion'] =np.round(df['Ocupacion'],4)

            df= df[df['EXT'].isin(Externos)]
            # df = df[['Nombre','Tiempo','IG','EXT','Movimiento','Acceso','Tipo_sensor','Sensor','Num_deteccion','Deteccion','Ocupacion','Brecha','Velocidad']]
            # df=df[['Tiempo','EXT','Acceso','Sensor','Deteccion','Ocupacion','Brecha','Velocidad']]
            df=df[['Tiempo','EXT','Acceso','Sensor','Deteccion']]
            data.append(df)
            df_fines = pd.concat(data, axis=0)

            num = len(df['EXT'])
            datas = {'id': [file_id],
                    'num': [num]}
            
            registros_counta = pd.DataFrame(datas)
            registros_count.append(registros_counta)
            num_reg=pd.concat(registros_count, axis=0)

            print(f"{file_id}")
            
        df_fines = assign_corredor_analisis(df_fines, mapping_df)
        
        # BASE HORARIA
        
        df_hora=df_fines.set_index(df_fines['Tiempo']).drop(['Tiempo'], axis=1)
        df_hora=np.round(df_hora.groupby(['EXT','Acceso','Sensor','Corredor','Analisis']).resample('h', include_groups=False).agg(Deteccion= ('Deteccion','sum')),3)
        df_hora=df_hora.reset_index()
        df_hora=df_hora.dropna(subset=['Deteccion'])
        df_hora = enrich_hourly_output(df_hora)
        
        # # BASE PARA DIAS
        df_dia =df_hora.copy()
        df_dia=df_dia.set_index(df_dia['Tiempo']).drop(['Tiempo','Fecha','Fecha_2'], axis=1)
        df_dia = np.round(df_dia.groupby(['Corredor','Analisis','Dia_sem']).resample('D', include_groups=False).agg(Deteccion= ('Deteccion','sum')),3).dropna(subset=['Deteccion']).sort_values(by=['Tiempo'], ascending=True)
        df_dia=df_dia[(df_dia['Deteccion'] != 0)]
        df_dia=df_dia.reset_index()

        # # BASE PARA ANALISIS PROMEDIOS
        prueba = build_pivot_base(df_hora, dias)
        
        # # BASE PARA LOS SEGUIMIENTOS
        df_seguimiento=df_hora.copy()
        df_seguimiento['Tiempo']=pd.to_datetime(df_seguimiento['Tiempo']).dt.floor('D')
        pivot_table = df_seguimiento.pivot_table(
            index=['Analisis','Corredor','Tiempo'],
            columns='Hora',
            values=['Deteccion'],
            aggfunc='mean').round(0)
        pivot_table.columns = pivot_table.columns.droplevel(0)
        pivot_table.columns.name = None
        pivot_table = pivot_table.reset_index()
        Fecha_ini['Tiempo']=pd.to_datetime(Fecha_ini['Tiempo']).dt.floor('D')
        df_seguimiento = Fecha_ini.merge(pivot_table, on=['Analisis','Corredor','Tiempo'],  how ='left').fillna(0)
        df_seguimiento = df_seguimiento.melt(['Tiempo','Analisis','Corredor'],var_name='Hora', value_name='Deteccion')
        df_seguimiento['Hora']=df_seguimiento['Hora'].astype(str).astype(int)
        
        ### 033 - SE CARGAN LA BASE DE ORACLE LOS DATOS DE LAS FECHAS ANTERIORES
                
        engine = build_oracle_engine()

        # BASE PARA FINES HORARIA
        dtype_hora = {"Tiempo" : TIMESTAMP, "EXT" : VARCHAR2(50),"Acceso": VARCHAR2(50),"Sensor": VARCHAR2(20), "Corredor" : VARCHAR2(50),"Analisis": VARCHAR2(50),
                    "Deteccion" : NUMBER, "Dia_sem": VARCHAR2(50), "Hora" : NUMBER, "Semana": VARCHAR2(100), "Fecha": VARCHAR2(50), "Fecha_2" : TIMESTAMP, "period": VARCHAR2(100)
            }
        df_hora .to_sql(
            name=f"fin_hora_sema",
            con=engine,
            if_exists="append", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_hora
            )
        
        # BASE PARA FINES DIARIA
        dtype_dia = {"Tiempo" : TIMESTAMP,  "Corredor" : VARCHAR2(50),"Analisis": VARCHAR2(50), "Dia_sem": VARCHAR2(50),"Deteccion" : NUMBER
                    }
        df_dia .to_sql(
            name=f"fin_dia_sema",
            con=engine,
            if_exists="append", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_dia
            )
        
        # BASE PARA FINES PIVOT
        dtype_pivot = {"Tiempo" : TIMESTAMP, "EXT" : VARCHAR2(50),"Acceso": VARCHAR2(50),"Sensor": VARCHAR2(20), "Corredor" : VARCHAR2(50),"Analisis": VARCHAR2(50), "Dia_sem": VARCHAR2(50), "Hora" : NUMBER,
                    "label": VARCHAR2(50),"Deteccion" : FLOAT, "Fecha": VARCHAR2(50),"Semana": VARCHAR2(100)
            }
        prueba.to_sql(
            name=f"fin_pivot_sema",
            con=engine,
            if_exists="append", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_pivot
            )
        # BASE PARA FINES DE SEGUIMIENTO
        dtype_Seg = {"Tiempo" : TIMESTAMP, "Analisis": VARCHAR2(50),"Corredor" : VARCHAR2(50), "Hora" : VARCHAR2(20), "Deteccion" : NUMBER
            }

        df_seguimiento.to_sql(
            name=f"fin_seg_sema",
            con=engine,
            if_exists="append", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_Seg
            )

        del df
        del df_fines
        del data
        del df_hora
        del prueba
        del df_seguimiento
        data = []
        
        #034 SE ACTUALIZA LA BASE DE REGISTRO CON EL PROCESAMIENTO DE FECHAS ANTERIORES

        registro_n = mark_registry_processed(Registro, num_reg)
        registro_n['date'] = pd.to_datetime(registro_n['date'], errors='coerce')
        registro_n['size_in_MB'] = pd.to_numeric(registro_n['size_in_MB'], errors='coerce').fillna(0.0)
        registro_n['num'] = pd.to_numeric(registro_n['num'], errors='coerce').fillna(0).astype(int)

        # Se actualiza la base de datos de los registros procesados.
        replace_sheet_records(gc, registro_sheet_url, registro_worksheet, registro_n)

        # Actualización base registros
        dtype_Reg = {"size_in_MB" : FLOAT, "id" : VARCHAR2(50), "name": VARCHAR2(200),"creation" : VARCHAR2(100), "last_modification" : VARCHAR2(100), "type_of_file": VARCHAR2(50),"date": TIMESTAMP,
                        "mes" : NUMBER, "Estado" : VARCHAR2(50), "num": NUMBER, 
                    }
        registro_n.to_sql(
            name="fin_reg_sema",
            con=engine,
            if_exists="replace", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_Reg
            )
        print(f"Se actualizo desde {select_data['date'].min()} hasta {select_data['date'].max()} en registros de Detecciones - Sema")
        
    # 04 - SE REALIZA EL PROCESO PARA LOS REGISTROS DEL DIA DE HOY

    # BORRAR REGISTROS TRANSACCIONALES ANTIGUOS (AUNQUE YA SE HIZO EN 030)

    engine = build_oracle_engine()

    stmt1 = text('''DELETE FROM FIN_DIA_SEMA WHERE "Tiempo" >= (TRUNC(CURRENT_DATE))''')
    stmt2 = text('''DELETE FROM FIN_HORA_SEMA WHERE "Tiempo" >= (TRUNC(CURRENT_DATE))''')
    stmt3a = text('''DELETE FROM FIN_PIVOT_SEMA WHERE "Tiempo" >= (TRUNC(CURRENT_DATE)) AND "Semana" <> 'Promedio' ''')
    stmt3b = text('''DELETE FROM FIN_PIVOT_SEMA WHERE ("Semana" = 'Promedio')''')  # ESTE SE DEBE ACTIVAR PARA ACTUALIZAR LOS PROMEDIOS
    stmt4 = text('''DELETE FROM FIN_SEG_SEMA WHERE "Tiempo" >= (TRUNC(CURRENT_DATE))''')

    with engine.connect() as connection:
        connection.execute(stmt1)
        connection.execute(stmt2)
        connection.execute(stmt3a)
        connection.execute(stmt3b)    # ESTE SE DEBE ACTIVAR PARA ACTUALIZAR LOS PROMEDIOS
        connection.execute(stmt4)
        connection.commit()
        
        print(f"Se borran los registros del mismo dia para que se actualicen")
        
    ## 041 SE BUSCAN LOS ARCHVIOS DEL DIA DE HOY.

    # Se marca el dia de hoy para los registros del mismo día
    hoy = datetime.today().date()

    cleared_df = list_drive_files(service, str(settings["hoy_folder_id"]))
    cleared_dfa = cleared_df
    cleared_df['creation']= pd.to_datetime(cleared_df['creation'])- timedelta(hours=5)
    cleared_df['date'] = pd.to_datetime(cleared_df['creation'], format='%y/%m/%d').dt.floor('D')
    cleared_df['date'] = pd.to_datetime(cleared_df.date).dt.tz_localize(None)
    #cleared_df = classify_drive_files(
    #    ensure_required_columns(cleared_df),
    #    min_size_mb=float(settings["min_size_mb"]),
    #    max_size_mb=float(settings["max_size_mb"]),
    #    target_size_mb=float(settings["target_size_mb"]),
    #    existing_registry=pd.DataFrame(columns=REQUIRED_COLUMNS),
    #)

    cleared_df['mes'] = cleared_df['date'].dt.strftime("%y%m")

    select_data = cleared_df[
        cleared_df['date'].dt.date == hoy
    ].copy()
    # Ordenar por más reciente
    select_data = select_data.sort_values(by='creation', ascending=False)

    # Tomar solo los 2 últimos
    select_data = select_data.head(2)


    # 4) Si select_data no está vacío, convertir date y construir MESES/ids2
    if not select_data.empty:
        # Asegurar que la columna 'date' sea datetime antes de .dt.date
        select_data['date'] = pd.to_datetime(select_data['date'], errors='coerce').dt.date
        MESES = select_data['mes'].unique().tolist()
        ids2 = select_data['id'].tolist()
    else:
        # Caso vacío: continuar la ejecución con listas vacías
        MESES = []
        ids2 = []

    #042 - SE COMPILAN LOS REGISTROS QUE CORRESPONDEN AL MISMO DIA


    data=[]
    registros_count = []

    numeric_cols = [
        "Processed Data.Count Vehicle[Fzg/h]",
        "Processed Data.Speed Vehicle[km/h]",
        "Processed Data.Occupancy Vehicle[%]",
        "Original Data.Count Vehicle[Fzg/h]",
        "Original Data.Speed Vehicle[km/h]",
        "Original Data.Occupancy Vehicle[%]"
        ]


    if not ids2:  #En caso de que la lista este vacia
        print("No hay registros creados el dia de hoy")
    else:
        for file_id in ids2:
            url = "https://www.googleapis.com/drive/v3/files/" + file_id + "?alt=media"
            res = requests.get(url, headers={"Authorization": "Bearer " + access_token})
            df = pd.read_csv(io.StringIO(res.text),sep=';',encoding='latin1')

            
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = safe_to_float(df[col])
            df[numeric_cols] = df[numeric_cols].fillna(0)
                
            df=df.iloc[:,[0,1,2,6]]

            


            df['Timestamp']= pd.to_datetime(df['Timestamp'], format='%d.%m.%Y %H:%M:%S')
            df=df.set_axis(['Tiempo', 'Nombre', 'Deteccion', 'Ocupacion'], axis='columns')
            df=df.loc[~((df['Deteccion'] == '-'))]
            for col in ["Deteccion", "Ocupacion"]:
                if df[col].apply(lambda x: isinstance(x, str)).any():
                    df[col] = (
                        df[col]
                        .astype(str)
                        .str.replace('.', '', regex=False)
                        .str.replace(',', '.', regex=False)
                        .astype(float)
                    )
            df.loc[:,'EXT']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 2].str.extract('(\\d+)').astype(str)
            df.loc[:,'Sensor']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 4].astype(str)
            df.loc[:,'Acceso']=df['Sensor'].str.extract('(\\d+(?=.))').iloc[:, 0].str.extract(r'([0-9]+)').infer_objects().fillna(0).astype(str)
            df.loc[df['Acceso']== '0', 'Acceso'] = df['Sensor'].str.extract('(\\d+)').iloc[:, 0]
            df.loc[:,'Tipo_sensor']=df['Sensor'].str.extract('(^[A-Z])').iloc[:, 0]
            df.loc[:,'Deteccion']=np.round((df['Deteccion']/4), 0).astype(int)
            df['Fecha'] = pd.to_datetime(df['Tiempo'], format="%Y/%m/%d").dt.floor('D')
            

            df = df[df['Fecha'].dt.date == hoy]
            #df = df.loc[df['Fecha'] == hoy]
            

            df= df[df['EXT'].isin(Externos)]
            # df = df[['Nombre','Tiempo','IG','EXT','Movimiento','Acceso','Tipo_sensor','Sensor','Num_deteccion','Deteccion','Ocupacion','Brecha','Velocidad']]
            # df=df[['Tiempo','EXT','Acceso','Sensor','Deteccion','Ocupacion','Brecha','Velocidad']]
            df=df[['Tiempo','EXT','Acceso','Sensor','Deteccion']]

            data.append(df)
            df_fines = pd.concat(data, axis=0)

            num = len(df['EXT'])
            datas = {'id': [file_id],
                    'num': [num]}
            
            registros_counta = pd.DataFrame(datas)
            registros_count.append(registros_counta)
            num_reg=pd.concat(registros_count, axis=0)
            
        df_fines = assign_corredor_analisis(df_fines, mapping_df)
        df_fines= df_fines.sort_values(['Tiempo', 'Deteccion'], ascending=[False, False])
        df_fines = df_fines.drop_duplicates(subset=['Tiempo','EXT','Sensor','Corredor','Analisis'], keep='first')


        
        # BASE HORARIA
        
        df_hora=df_fines.set_index(df_fines['Tiempo']).drop(['Tiempo'], axis=1)
        df_hora=np.round(df_hora.groupby(['EXT','Acceso','Sensor','Corredor','Analisis']).resample('h', include_groups=False).agg(Deteccion= ('Deteccion','sum')),3)
        df_hora=df_hora.reset_index()
        df_hora=df_hora.dropna(subset=['Deteccion'])
        df_hora = enrich_hourly_output(df_hora)

        
        
        # # BASE PARA DIAS
        df_dia =df_hora.copy()
        df_dia=df_dia.set_index(df_dia['Tiempo']).drop(['Tiempo','Fecha','Fecha_2'], axis=1)
        df_dia = np.round(df_dia.groupby(['Corredor','Analisis','Dia_sem']).resample('D', include_groups=False).agg(Deteccion= ('Deteccion','sum')),3).dropna(subset=['Deteccion']).sort_values(by=['Tiempo'], ascending=True)
        df_dia=df_dia[(df_dia['Deteccion'] != 0)]
        df_dia=df_dia.reset_index()

        # # BASE PARA ANALISIS PROMEDIOS
        prueba = build_pivot_base(df_hora, dias)

        
        # # BASE PARA LOS SEGUIMIENTOS
        df_seguimiento=df_hora.copy()
        df_seguimiento['Tiempo']=pd.to_datetime(df_seguimiento['Tiempo']).dt.floor('D')
        pivot_table = df_seguimiento.pivot_table(
            index=['Analisis','Corredor','Tiempo'],
            columns='Hora',
            values=['Deteccion'],
            aggfunc='mean').round(0)
        pivot_table.columns = pivot_table.columns.droplevel(0)
        pivot_table.columns.name = None
        pivot_table = pivot_table.reset_index()
        Fecha_ini['Tiempo']=pd.to_datetime(Fecha_ini['Tiempo']).dt.floor('D')
        df_seguimiento = Fecha_ini.merge(pivot_table, on=['Analisis','Corredor','Tiempo'],  how ='left').fillna(0)
        df_seguimiento = df_seguimiento.melt(['Tiempo','Analisis','Corredor'],var_name='Hora', value_name='Deteccion')
        df_seguimiento['Hora']=df_seguimiento['Hora'].astype(str).astype(int)
        
        ### 043- SE ACTUALIZAN LOS REGISTROS DEL DIA DE HOY PARA EL MISMO DIA
                
        engine = build_oracle_engine()

        # BASE PARA FINES HORARIA
        dtype_hora = {"Tiempo" : TIMESTAMP, "EXT" : VARCHAR2(50),"Acceso": VARCHAR2(50),"Sensor": VARCHAR2(20), "Corredor" : VARCHAR2(50),"Analisis": VARCHAR2(50),
                    "Deteccion" : NUMBER, "Dia_sem": VARCHAR2(50), "Hora" : NUMBER, "Semana": VARCHAR2(100), "Fecha": VARCHAR2(50), "Fecha_2" : TIMESTAMP, "period": VARCHAR2(100)
            }
        df_hora .to_sql(
            name=f"fin_hora_sema",
            con=engine,
            if_exists="append", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_hora
            )
        
        # BASE PARA FINES DIARIA
        dtype_dia = {"Tiempo" : TIMESTAMP,  "Corredor" : VARCHAR2(50),"Analisis": VARCHAR2(50), "Dia_sem": VARCHAR2(50),"Deteccion" : NUMBER
                    }
        df_dia .to_sql(
            name=f"fin_dia_sema",
            con=engine,
            if_exists="append", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_dia
            )
        
        # BASE PARA FINES PIVOT
        dtype_pivot = {"Tiempo" : TIMESTAMP, "EXT" : VARCHAR2(50),"Acceso": VARCHAR2(50),"Sensor": VARCHAR2(20), "Corredor" : VARCHAR2(50),"Analisis": VARCHAR2(50), "Dia_sem": VARCHAR2(50), "Hora" : NUMBER,
                    "label": VARCHAR2(50),"Deteccion" : FLOAT, "Fecha": VARCHAR2(50),"Semana": VARCHAR2(100)
            }
        prueba.to_sql(
            name=f"fin_pivot_sema",
            con=engine,
            if_exists="append", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_pivot
            )
        # BASE PARA FINES DE SEGUIMIENTO
        dtype_Seg = {"Tiempo" : TIMESTAMP, "Analisis": VARCHAR2(50),"Corredor" : VARCHAR2(50), "Hora" : VARCHAR2(20), "Deteccion" : NUMBER
            }

        df_seguimiento.to_sql(
            name=f"fin_seg_sema",
            con=engine,
            if_exists="append", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_Seg
            )
        
        del df
        del df_fines
        del data
        del df_hora
        del prueba
        del df_seguimiento
        data = []
        print ('Se actualizan los registros para el mismo dia')
        
    # 05 ACTUALIZACION DE BASE DE DATOS DE CAMBIO

    select_template = '''SELECT * FROM FIN_DIA_SEMA WHERE "Tiempo" >= TRUNC(ADD_MONTHS(SYSDATE, -12))'''
    frames_dict = {}
    appended_data = []
    frames_dict = pd.read_sql(select_template, engine)
    appended_data.append(frames_dict)
    df_resum = pd.concat(appended_data, axis=0)
    df_resum=df_resum.dropna(subset = ['Deteccion'])

    # ---------------------------
    # 1) Preparar df_resum y perc_change
    # ---------------------------
    df_resum = df_resum.copy()
    df_resum['Tiempo'] = pd.to_datetime(df_resum['Tiempo'], errors='coerce')
    df_resum['Deteccion'] = pd.to_numeric(df_resum['Deteccion'], errors='coerce')
    df_resum = df_resum.dropna(subset=['Deteccion']).reset_index(drop=True)

    def perc_change(a, b):
        return ((a - b) / b * 100) if b != 0 else np.nan

    # ---------------------------
    # 2) Calcular mat (tal como lo tenías)
    # ---------------------------
    arr = df_resum[['Deteccion']].to_numpy(float)
    mat1 = squareform(pdist(arr, lambda u, v: perc_change(v[0], u[0])))
    mat2 = squareform(pdist(arr, lambda u, v: perc_change(u[0], v[0])))
    mat  = np.tril(mat1) + np.triu(mat2)
    np.fill_diagonal(mat, 0.0)

    # ---------------------------
    # 3) Construir MultiIndex para filas y columnas
    # ---------------------------
    idx = pd.MultiIndex.from_arrays(
        [df_resum['Tiempo'], df_resum['Corredor'], df_resum['Analisis'], df_resum['Dia_sem']],
        names=['Tiempo_base', 'Correrdor_base', 'Analisis_Base', 'Dia_sem_base']
    )
    cols = pd.MultiIndex.from_arrays(
        [df_resum['Tiempo'], df_resum['Corredor'], df_resum['Analisis'], df_resum['Dia_sem']],
        names=['Tiempo', 'Corredor', 'Analisis', 'Dia_sem']
    )

    # ---------------------------
    # 4) Crear DataFrame ancho y reset_index (no aplanamos aún)
    # ---------------------------
    df_wide = pd.DataFrame(mat, index=idx, columns=cols)
    df_wide_reset = df_wide.reset_index()   # las primeras columnas son las 4 del índice

    # ---------------------------
    # 5) Aplanar solo las columnas "de comparación" (las que vienen del MultiIndex de columnas)
    # ---------------------------
    SEP = '___'  # separador seguro (triple underscore para evitar colisiones)
    # Crear nombres aplanados para las columnas anchas (las columnas originales del DataFrame df_wide)
    flat_comp_cols = [SEP.join(map(str, col)) for col in df_wide.columns]  # length = n (no usar list(...) explícito)

    # Asignar nombres completos a df_wide_reset: primeras 4 (index) + las columnas aplanadas
    base_names = ['Tiempo_base', 'Correrdor_base', 'Analisis_Base', 'Dia_sem_base']
    df_wide_reset.columns = base_names + flat_comp_cols

    # ---------------------------
    # 6) Melt: pasar las columnas a filas
    # ---------------------------
    cambio_melt = df_wide_reset.melt(
        id_vars=base_names,
        value_vars=flat_comp_cols,
        var_name='variable',
        value_name='Valores'
    )

    # ---------------------------
    # 7) Separar la columna compuesta en 4 columnas y borrar 'variable'
    # ---------------------------
    splits = cambio_melt['variable'].str.split(SEP, n=3, expand=True)
    splits.columns = ['Tiempo', 'Corredor', 'Analisis', 'Dia_sem']
    cambio_melt = pd.concat([cambio_melt.drop(columns='variable'), splits], axis=1)

    # ---------------------------
    # 8) Convertir las fechas y filtrar coincidencias
    # ---------------------------
    cambio_melt['Tiempo_base'] = pd.to_datetime(cambio_melt['Tiempo_base'], errors='coerce').dt.normalize()
    cambio_melt['Tiempo']      = pd.to_datetime(cambio_melt['Tiempo'], errors='coerce').dt.normalize()

    # Filtrar solo filas donde coinciden corredor y análisis
    cambio_melt = cambio_melt[
        (cambio_melt['Correrdor_base'] == cambio_melt['Corredor']) &
        (cambio_melt['Analisis_Base']  == cambio_melt['Analisis'])
    ]

    # ---------------------------
    # 9) Reordenar columnas tal como pediste
    # ---------------------------
    cambio_melt = cambio_melt[
        ['Tiempo_base', 'Correrdor_base', 'Analisis_Base', 'Dia_sem_base',
        'Tiempo', 'Corredor', 'Analisis', 'Dia_sem', 'Valores']
    ].reset_index(drop=True)

    cambio_melt= cambio_melt.reset_index().drop(['index'], axis=1)
    cambio_melt['Fecha'] = cambio_melt['Tiempo'].dt.strftime('%Y/%m/%d')
    cambio_melt['period'] = cambio_melt[['Analisis','Corredor','Fecha']].agg('-'.join, axis=1)
    cambio_melt= cambio_melt.merge(df_resum, how='inner', on=['Tiempo', 'Corredor','Analisis','Dia_sem'])
    
    # 052 - SE SELECCIONA Y ACTUALIZAN LAS BASES HORARIAS
    dtype_cambio = {"Tiempo_base":TIMESTAMP, "Correrdor_base": VARCHAR2(50), "Analisis_Base":VARCHAR2(50), "Dia_sem_base": VARCHAR2(50), "Tiempo" : TIMESTAMP, "Corredor":VARCHAR2(50), "Analisis":VARCHAR2(50), "Dia_sem": VARCHAR2(50),
                    "Valores" : FLOAT, "Fecha": VARCHAR2(50), "period": VARCHAR2(100), "Deteccion" : FLOAT
        }
    cambio_melt .to_sql(
        name=f"fin_camb_sema",
        con=engine,
        if_exists="replace", # Append to existing table
        index=False,        # Do not write row index
        dtype=dtype_cambio
        )
    del df_resum
    del cambio_melt
    
    # # 06 ACTUALIZACION DE BASE DE DATOS DE PIVOTE (ACTUALIZAR SOLO PÁRA ACTUALIZAR PROMEDIOS)

    d_prom= datetime(1900, 1, 1).date().strftime('%Y-%m-%d')

    select_template = ''' SELECT * FROM FIN_PIVOT_SEMA '''
    frames_dict = {}
    appended_data = []
    frames_dict = pd.read_sql(select_template, engine)
    appended_data.append(frames_dict)
    df_medio = pd.concat(appended_data, axis=0)
    df_medio=df_medio.dropna(subset = ['Deteccion'])
    df_medio = df_medio.rename(columns={'ext': 'EXT'})
    df_medio = df_medio[df_medio['Semana'] != 'Promedio']


    df_avg = df_medio.groupby(['EXT','Acceso','Sensor','Corredor','Analisis','Dia_sem','Hora','label'],as_index=False)['Deteccion'].mean()
    df_avg = df_avg[df_avg['Deteccion'].notna()]
    df_avg['Tiempo'] = pd.to_datetime(d_prom)
    df_avg['Fecha']= df_avg['Dia_sem'].astype(str) + '_prom'
    df_avg['Semana'] = 'Promedio'
    df_result = pd.concat([df_medio, df_avg])
    df_result['Deteccion']=df_result['Deteccion'].replace(0, np.nan)
    
    # 061 - SE ACTUALIZA LA BASE COJUNTA DE PIVOTE (GENERAL + PROMEDIO)
    dtype_pivot = {"Tiempo":TIMESTAMP, "EXT" : VARCHAR2(50),"Acceso": VARCHAR2(50),"Sensor": VARCHAR2(20), "Corredor" : VARCHAR2(50),"Analisis": VARCHAR2(50), "Dia_sem": VARCHAR2(50), "Hora" : NUMBER,
                    "label": VARCHAR2(50),"Deteccion" : FLOAT, "Fecha": VARCHAR2(50),"Semana": VARCHAR2(100)
        }
    df_result.to_sql(
        name=f"fin_pivot_sema",
        con=engine,
        if_exists="replace", # Append to existing table
        index=False,        # Do not write row index
        dtype=dtype_pivot
        )

    del df_result
    del df_medio
    del df_avg
    
    print ("finaliza_codigo fin de semana")

if __name__ == "__main__":
    main()
