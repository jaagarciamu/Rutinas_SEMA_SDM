## LIBRERIAS PARA EL CODIGO

from __future__ import annotations

import io
import logging
import os
import smtplib
import traceback
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from pipelines.alerting import record_pipeline_failure, record_pipeline_success
from sqlalchemy.dialects.oracle import FLOAT, NUMBER, TIMESTAMP, VARCHAR2
from sqlalchemy.engine import create_engine

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/bigquery",
]

logger = logging.getLogger("detecciones_30min")
ORACLE_FLOAT = FLOAT(binary_precision=126)


class ConfigurationError(RuntimeError):
    """Error funcional para variables de entorno faltantes o inválidas."""


class DataAvailabilityError(RuntimeError):
    """Error de negocio cuando no hay data minima requerida para ejecutar."""


def setup_logging() -> None:
    if logger.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)


def load_environment() -> None:
    """Carga variables desde config/.env y luego .env en raíz (si existe)."""
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
    drive_service = build("drive", "v3", credentials=credentials)
    return drive_service, access_token


def send_error_email(subject: str, body: str) -> None:
    """Envio opcional de alerta por correo en caso de error."""
    if os.getenv("ALERT_EMAIL_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "si"}:
        return

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "587").strip())
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes", "si"}
    smtp_use_auth = os.getenv("SMTP_USE_AUTH", "true").strip().lower() in {"1", "true", "yes", "si"}
    from_email = os.getenv("ALERT_EMAIL_FROM", "").strip()
    to_emails = [x.strip() for x in os.getenv("ALERT_EMAIL_TO", "").split(",") if x.strip()]

    if not all([smtp_host, from_email]) or not to_emails:
        logger.warning("Alerta por correo habilitada pero faltan SMTP_HOST/ALERT_EMAIL_FROM/ALERT_EMAIL_TO")
        return

    if smtp_use_auth and not all([smtp_user, smtp_password]):
        logger.warning("SMTP_USE_AUTH=true pero faltan SMTP_USER/SMTP_PASSWORD")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = ", ".join(to_emails)
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
        if smtp_use_tls:
            smtp.starttls()
        if smtp_use_auth:
            smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)


def get_folder_id_by_name(folder_df: pd.DataFrame, target_name: int, label: str) -> str:
    if folder_df.empty:
        raise DataAvailabilityError(f"No se encontraron carpetas disponibles para {label}")
    condition = folder_df.loc[folder_df["name"] == target_name]
    if condition.empty:
        raise DataAvailabilityError(
            f"No se encontró carpeta para {label}={target_name} en estructura de 30min"
        )
    return str(condition.iloc[0, condition.columns.get_loc("id")])

def safe_to_float(series):
    return (
        series
        .astype(str)
        .str.strip()
        .str.replace(",", ".", regex=False)
        .where(lambda x: x.str.match(r"^-?\d+(\.\d+)?$"))
        .astype(float)
    )    


def main():
    load_environment()
    setup_logging()

    # Fechas del dia actual
    now = datetime.now()
    mes = now.date().month
    dia= now.date().day
    dias_antes = 2 # antes de  ayer
    max_delay_minutes = int(os.getenv("DETECCIONES_30MIN_MAX_DELAY_MINUTES", "120"))
    freshness_alert_start_hour = int(os.getenv("DETECCIONES_30MIN_FRESHNESS_ALERT_START_HOUR", "8"))
    freshness_alert_end_hour = int(os.getenv("DETECCIONES_30MIN_FRESHNESS_ALERT_END_HOUR", "20"))

    # Fecha de ayer (sin hora)
    ayer = now - timedelta(days=1)
    ayer_truncado = pd.Timestamp(ayer.date())
    scopes = parse_scopes(os.getenv("GOOGLE_SCOPES"))
    service, access_token = build_google_clients(scopes)
    folder_root_30min = require_env("GOOGLE_DETECCIONES_30MIN_DRIVE_ROOT_FOLDER_ID")
    folder_historico = require_env("GOOGLE_DETECCIONES_DRIVE_FOLDER_ID")

    ## SE REVISAN LAS CARPETAS PARA VER SI HAY ARCHIVOS NUEVOS

    #1) Se busca en la carpta general el mes
    results = service.files().list(q = f"mimeType = 'application/vnd.google-apps.folder' and parents='{folder_root_30min}'",pageSize=1000, fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, createdTime)").execute()
    items = results.get('files', [])

    data = []
    for row in items:
        if row["mimeType"] == "application/vnd.google-apps.folder":
            row_data = []
            try:
                row_data.append(round(int(row["size"])/100000000, 2))
            except KeyError:
                row_data.append(0.00)
            row_data.append(row["id"])
            row_data.append(row["name"])
            row_data.append(row["createdTime"])
            row_data.append(row["modifiedTime"])
            row_data.append(row["mimeType"])
            data.append(row_data)

    folder_df = pd.DataFrame(data, columns = ['size_in_MB','id', 'name', 'creation','last_modification', 'type_of_file'])
    folder_df['name']=folder_df['name'].astype(str).astype(int)

    folder_ID = get_folder_id_by_name(folder_df, mes, "mes")

    # Se busca en la carpeta del del mes el diad e hoy
    results = service.files().list(q = f"mimeType = 'application/vnd.google-apps.folder' and parents='{folder_ID}'",pageSize=1000, fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, createdTime)").execute()
    items = results.get('files', [])

    data = []
    for row in items:
        if row["mimeType"] == "application/vnd.google-apps.folder":
            row_data = []
            try:
                row_data.append(round(int(row["size"])/100000000, 2))
            except KeyError:
                row_data.append(0.00)
            row_data.append(row["id"])
            row_data.append(row["name"])
            row_data.append(row["createdTime"])
            row_data.append(row["modifiedTime"])
            row_data.append(row["mimeType"])
            data.append(row_data)

    dia_df = pd.DataFrame(data, columns = ['size_in_MB','id', 'name', 'creation','last_modification', 'type_of_file'])
    dia_df['name']=dia_df['name'].astype(str).astype(int)
    dia_ID = get_folder_id_by_name(dia_df, dia, "dia")

    if not dia_ID:
        raise DataAvailabilityError("No se encontró carpeta del día actual para 30min")
    else:   ## SE ENTRA EN EL CODIGO PRINCIPAL
        engine = build_oracle_engine()

        # Se identifican las carpetas del mismo dia y se dejan 10 si aumentan los detectores, estos deben aumentar
        results = service.files().list(q = f"mimeType = 'application/vnd.google-apps.folder' and parents='{dia_ID}'",pageSize=1000, fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, createdTime)").execute()
        items = results.get('files', [])

        data = []
        ids=[]
        for row in items:
            if row["mimeType"] == "application/vnd.google-apps.folder":
                row_data = []
                try:
                    row_data.append(round(int(row["size"])/100000000, 2))
                except KeyError:
                    row_data.append(0.00)
                row_data.append(row["id"])
                row_data.append(row["name"])
                row_data.append(row["createdTime"])
                row_data.append(row["modifiedTime"])
                row_data.append(row["mimeType"])
                ids.append(row["id"])
                data.append(row_data)

        transcurso_df = pd.DataFrame(data, columns = ['size_in_MB','id', 'name', 'creation','last_modification', 'type_of_file'])
        ids_detc = transcurso_df['id'].tolist()[:10]
        if not ids_detc:
            raise DataAvailabilityError("No hay subcarpetas de detectores para el día actual")

        # Se extraen los Id de los ultimos 10 archivos
        data2=[]
        registros_count = []

        for decte in ids_detc:
            ## Conexión con la carpeta donde se localiza los archivos de volúmenes, esta debe ser actualizada antes de correr el código.
            results = service.files().list(q = f"parents='{decte}'",pageSize=1000, fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, createdTime)").execute()
            elemts = results.get('files', [])

            
            data = []
            ids=[]
            for row in elemts:
                if row["mimeType"] != "application/vnd.google-apps.folder":
                    row_data = []
                    try:
                        row_data.append(round(int(row["size"])/100000000, 2))
                    except KeyError:
                        row_data.append(0.00)
                    row_data.append(row["id"])
                    row_data.append(row["name"])
                    row_data.append(row["createdTime"])
                    row_data.append(row["modifiedTime"])
                    row_data.append(row["mimeType"])
                    ids.append(row["id"])
                    data.append(row_data)

            cleared_df = pd.DataFrame(data, columns = ['size_in_MB', 'id', 'name', 'creation','last_modification', 'type_of_file'])
            cleared_df['creation'] = pd.to_datetime(cleared_df['creation'])- timedelta(hours=5)
            cleared_df['last_modification'] = pd.to_datetime(cleared_df['last_modification'])- timedelta(hours=5)
            cleared_df['date']= pd.to_datetime(cleared_df['last_modification'], format='%y%m%d')
            cleared_df['mes'] = cleared_df['date'].dt.strftime("%y%m")
            cleared_df = cleared_df.sort_values(by=['last_modification'], ascending=False)
            cleared_df = cleared_df[cleared_df['type_of_file'] == 'text/csv']
            cleared_df = cleared_df[cleared_df['name'].str.contains("report")]
            data2.append(cleared_df)

        cleared_df_rev  = pd.concat(data2, axis=0)
        cleared_df_rev = cleared_df_rev.sort_values(by=['last_modification'], ascending=True)
        ids =cleared_df_rev['id'].tolist()

        ## se procesan los registros del mismo dia
        ids2=[]
        data=[]
        registros_count = []

        numeric_cols = [
            "Processed Data.Count Vehicle[Veh/Interval]",
            "Processed Data.Speed Vehicle[km/h]",
            "Processed Data.Occupancy Vehicle[%]",
            "Original Data.Count Vehicle[Veh/Interval]",
            "Original Data.Speed Vehicle[km/h]",
            "Original Data.Occupancy Vehicle[%]"
            ]

        for file_id in ids:
            url = "https://www.googleapis.com/drive/v3/files/" + file_id + "?alt=media"
            res = requests.get(url, headers={"Authorization": "Bearer " + access_token}, timeout=120)
            res.raise_for_status()
            df = pd.read_csv(io.StringIO(res.text),sep=';',encoding='latin1')

            for col in numeric_cols:
                if col in df.columns:
                    df[col] = safe_to_float(df[col])
            df[numeric_cols] = df[numeric_cols].fillna(0)
            
            df=df.iloc[:,[0,1,2,3,6]]
            df['Timestamp']= pd.to_datetime(df['Timestamp'], format='%d.%m.%Y %H:%M:%S')- timedelta(hours=0, minutes=1)
            df=df.set_axis(['Tiempo', 'Nombre', 'Deteccion','Reg_ok','Ocupacion'], axis='columns')
            df = df[df['Reg_ok'] != 'nOk']
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
            # df.loc[:,'IG']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 1].str.extract('(\\d+)').astype(str)
            df.loc[:,'EXT']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 2].str.extract('(\\d+)').astype(str)
            # df.loc[:,'Movimiento']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 3]
            df.loc[:,'Sensor']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 4].astype(str)
            df.loc[:,'Acceso']=df['Sensor'].str.extract('(\\d+(?=.))').iloc[:, 0].str.extract(r'([0-9]+)').infer_objects().fillna(0).astype(str)
            df.loc[df['Acceso']== '0', 'Acceso'] = df['Sensor'].str.extract('(\\d+)').iloc[:, 0]
            df.loc[:,'Tipo_sensor']=df['Sensor'].str.extract('(^[A-Z])').iloc[:, 0]
            df.loc[:,'Num_deteccion'] = 16
            df.loc[:,'Deteccion']=np.round((df['Deteccion']), 0).astype(int)
            df.loc[:,'Ocupacion'] =np.round(df['Ocupacion'],4)
            df.loc[:,'Brecha'] =np.where((df['Deteccion'] == 0)|(df['Ocupacion'] == 0),0, np.round((900/df['Deteccion'])-((900*(df['Ocupacion']/100))/df['Deteccion']),3))
            df.loc[:,'Velocidad'] =np.where((df['Deteccion'] == 0)|(df['Ocupacion'] == 0),0, np.round(6/((900*(df['Ocupacion']/100))/df['Deteccion'])*3.6,3))
            df = df[['Nombre','Tiempo','EXT','Acceso','Tipo_sensor','Sensor','Num_deteccion','Deteccion','Ocupacion','Brecha','Velocidad']]
            data.append(df)

        Hoy_TR_P = pd.concat(data, axis=0)
        Hoy_TR_P = Hoy_TR_P.drop_duplicates(keep='last').sort_values(by=["EXT", "Tiempo"], ascending=[True, False]).reset_index(drop=True)

        Hoy_TR_1 = Hoy_TR_P.dropna(subset=['Ocupacion']).copy()
        hoy_tiempo = pd.to_datetime(Hoy_TR_1['Tiempo'], errors='coerce', utc=True).dt.tz_convert(None)
        Hoy_TR_1.loc[:, 'Tiempo'] = hoy_tiempo
        Hoy_TR_1 = Hoy_TR_1.dropna(subset=['Tiempo']).copy()
        Hoy_TR_1.loc[:, 'Dia_sem'] = hoy_tiempo.dt.strftime('%A')
        Hoy_TR_1.loc[:, 'Horas'] = hoy_tiempo.dt.strftime('%H:%M')
        Hoy_TR_1 = Hoy_TR_1.reset_index()
        Hoy_TR_1=Hoy_TR_1.drop(['index'], axis=1)
        Hoy_TR=Hoy_TR_1
        if Hoy_TR.empty:
            raise DataAvailabilityError("No se obtuvieron registros válidos del día actual (30min)")

        latest_today_ts = pd.to_datetime(Hoy_TR["Tiempo"], errors="coerce").max()
        min_required_ts = now - timedelta(minutes=max_delay_minutes)
        in_freshness_alert_window = (
            freshness_alert_start_hour <= now.hour < freshness_alert_end_hour
        )
        if in_freshness_alert_window and (pd.isna(latest_today_ts) or latest_today_ts < min_required_ts):
            raise DataAvailabilityError(
                "Registros del día actual fuera de ventana. "
                f"Último={latest_today_ts}, requerido>={min_required_ts}, "
                f"tolerancia={max_delay_minutes} minutos, "
                f"ventana_alerta={freshness_alert_start_hour}:00-{freshness_alert_end_hour}:00"
            )

        EXT_unicos = list(Hoy_TR['EXT'].unique())
        Nombre_unicos = list(Hoy_TR['Nombre'].unique())

        ## ACTUALIZACION DE LOS DIAS ANTERIORES

        ## Conexión con la carpeta donde se localiza los archivos de volúmenes, esta debe ser actualizada antes de correr el código.
        results = service.files().list(q = f"'{folder_historico}' in parents",pageSize=1000, fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, createdTime)").execute()
        items = results.get('files', [])

        data = []
        ids=[]
        for row in items:
            if row["mimeType"] != "application/vnd.google-apps.folder":
                row_data = []
                try:
                    row_data.append(round(int(row["size"])/100000000, 2))
                except KeyError:
                    row_data.append(0.00)
                row_data.append(row["id"])
                row_data.append(row["name"])
                row_data.append(row["createdTime"])
                row_data.append(row["modifiedTime"])
                row_data.append(row["mimeType"])
                ids.append(row["id"])
                data.append(row_data)

        cleared_df = pd.DataFrame(data, columns = ['size_in_MB', 'id', 'name', 'creation','last_modification', 'type_of_file'])
        cleared_df['date'] = cleared_df['name'].str.extract('([0-9]+)', expand=True).iloc[:,0]
        cleared_df['date']= pd.to_datetime(cleared_df['date'], format='%y%m%d')
        cleared_df['mes'] = cleared_df['date'].dt.strftime("%y%m")
        cleared_df = cleared_df.sort_values(by=['date'], ascending=False)
        cleared_df = cleared_df[cleared_df['type_of_file'] == 'text/csv']
        cleared_df = cleared_df[
            cleared_df['name'].str.contains(
                "Detektor-Messquerschnitt|Detector-Measurement Point-Traffic Data - Processed",
                case=False,  # Ignora mayúsculas/minúsculas si lo deseas
                na=False     # Evita errores si hay valores NaN
                    )
            ]#cleared_df = cleared_df[cleared_df['name'].str.contains("Detektor-Messquerschnitt")]

        has_yesterday = (cleared_df["date"].dt.date == ayer_truncado.date()).any()
        if not has_yesterday:
            raise DataAvailabilityError(
                f"No se encontraron archivos del día anterior ({ayer_truncado.date()}) en carpeta histórica"
            )

        hace_dos_dias = datetime.now() - timedelta(days=dias_antes)

        select_data = cleared_df[cleared_df["date"] > hace_dos_dias]
        ids =select_data['id'].tolist()
        if not ids:
            raise DataAvailabilityError(
                "No hay archivos históricos elegibles (incluyendo día anterior) para construir referencia 30min"
            )

        #Procesamiento dias anteriores
        data=[]
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

        for file_id in ids:
            url = "https://www.googleapis.com/drive/v3/files/" + file_id + "?alt=media"
            res = requests.get(url, headers={"Authorization": "Bearer " + access_token}, timeout=120)
            res.raise_for_status()
            df = pd.read_csv(io.StringIO(res.text),sep=';',encoding='latin1')

            for col in numeric_cols:
                if col in df.columns:
                    df[col] = safe_to_float(df[col])
            df[numeric_cols] = df[numeric_cols].fillna(0)

            df=df.iloc[:,:8].drop(['UTC'], axis=1)
            df=df.set_axis(['Nombre', 'Tiempo', 'Sensor','Deteccion','Estado','Num_deteccion','Ocupacion'], axis='columns')
            df = df.query('(Num_deteccion > 8) & ~(Ocupacion <= 0.01 & Deteccion > 1)').copy()
            df.loc[:, 'Tiempo'] = pd.to_datetime(df['Tiempo'], format='%d.%m.%Y %H:%M:%S')- timedelta(hours=0, minutes=1)
            # df.loc[:,'IG']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 1].str.extract('(\\d+)').astype(str)
            df.loc[:,'EXT']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 2].str.extract('(\\d+)').astype(str)
            # df.loc[:,'Movimiento']=df['Nombre'].str.extract('(ig([^FD]+)FD)([^_]+)_([^_]*)_([^/]+)', expand=True).iloc[:, 3]
            df.loc[:,'Acceso']=df['Sensor'].str.extract('(\\d+(?=.))').iloc[:, 0].str.extract(r'([0-9]+)').infer_objects().fillna(0).astype(str)
            df.loc[df['Acceso']== '0', 'Acceso'] = df['Sensor'].str.extract('(\\d+)').iloc[:, 0]
            df.loc[:,'Tipo_sensor']=df['Sensor'].str.extract('(^[A-Z])').iloc[:, 0]
            df=df.drop(['Estado'], axis=1)
            df.loc[:,'Deteccion']=np.round((df['Deteccion']/4), 0).astype(int)
            # df['EXT']=df['EXT'].astype(int)
            #df['Intervalo']=900/df['Deteccion']
            #df['paso']=(900*(df['Ocupacion']/100))/df['Deteccion']
            df.loc[:,'Brecha'] =np.where((df['Deteccion'] == 0)|(df['Ocupacion'] == 0),0, np.round((900/df['Deteccion'])-((900*(df['Ocupacion']/100))/df['Deteccion']),3))
            df.loc[:,'Velocidad'] =np.where((df['Deteccion'] == 0)|(df['Ocupacion'] == 0),0, np.round(6/((900*(df['Ocupacion']/100))/df['Deteccion'])*3.6,3))
            # df['Relacion'] =np.round(df['Deteccion']/df['Ocupacion'],3)
            # df['Utilidad'] = df.apply(niveles,axis=1)
            df.loc[:,'Ocupacion'] =np.round(df['Ocupacion'],4)
            df = df[['Nombre','Tiempo','EXT','Acceso','Tipo_sensor','Sensor','Num_deteccion','Deteccion','Ocupacion','Brecha','Velocidad']]
            data.append(df)

        diario_TR_P = pd.concat(data, axis=0)
        diario_TR_P = diario_TR_P.drop_duplicates(keep='last').sort_values(by=["EXT", "Tiempo"], ascending=[True, False]).reset_index(drop=True)

        diario_TR_1 = diario_TR_P.dropna(subset=['Ocupacion']).copy()
        diario_tiempo = pd.to_datetime(diario_TR_1['Tiempo'], errors='coerce', utc=True).dt.tz_convert(None)
        diario_TR_1.loc[:, 'Tiempo'] = diario_tiempo
        diario_TR_1 = diario_TR_1.dropna(subset=['Tiempo']).copy()
        diario_TR_1.loc[:, 'Dia_sem'] = diario_tiempo.dt.strftime('%A')
        diario_TR_1.loc[:, 'Horas'] = diario_tiempo.dt.strftime('%H:%M')
        diario_TR_1 = diario_TR_1.reset_index()
        diario_TR_1=diario_TR_1.drop(['index'], axis=1)
        Diario_TR=diario_TR_1

        Diario_TR_F = Diario_TR[
        (Diario_TR['EXT'].isin(EXT_unicos)) &
        (Diario_TR['Nombre'].isin(Nombre_unicos))
        ]

        ## UNIR LOS DOS DATAFRAMES AYER Y EL DE HOY
        Tr_unido = pd.concat([Diario_TR_F, Hoy_TR], ignore_index=True)
        Tr_unido = Tr_unido.sort_values(by=['EXT', 'Acceso','Nombre','Tiempo'], ascending=[True, True,False,True])
        Tr_unido = Tr_unido.drop_duplicates(subset=['Nombre', 'EXT','Tiempo','Acceso','Dia_sem'], keep='last')
        Tr_unido = Tr_unido.reset_index()
        Tr_unido=Tr_unido.drop(['index'], axis=1)

        dias_unicos = list(Tr_unido['Dia_sem'].unique())

        select_template = '''SELECT * FROM PROM_DET_SEMA WHERE "EXT" = '{EXT_name}' AND "Dia_sem" = '{DIA_name}' '''  # EXT = 1006 AND
        frames_dict = {}
        appended_data = []
        total_data = []


        for EXTname in EXT_unicos:
            for DIAname in dias_unicos:
                query = select_template.format(EXT_name = EXTname, DIA_name = DIAname)
                frames_dict[DIAname] = pd.read_sql(query, engine)
                appended_data.append(frames_dict[DIAname])

        non_empty_frames = [df for df in appended_data if not df.empty]
        if not non_empty_frames:
            raise DataAvailabilityError("No se encontraron promedios base en PROM_DET_SEMA")

        promedios = pd.concat(non_empty_frames, axis=0, ignore_index=True)
        promedios=promedios.dropna(subset = ['Ocupacion'])
        promedios = promedios.rename(columns={'ext': 'EXT'})
        promedios = promedios.astype({'Num_deteccion':'int', 'Deteccion':'int',})
        promedios = promedios[
            (promedios['EXT'].isin(EXT_unicos)) &
            (promedios['Nombre'].isin(Nombre_unicos)) &
            (promedios['Dia_sem'].isin(dias_unicos))
            ]
        promedios = promedios[['Nombre','EXT','Acceso','Dia_sem','Horas', 'Deteccion','Ocupacion','Brecha','Velocidad']].rename(columns={
            'Deteccion': 'Deteccion_prom','Ocupacion': 'Ocupacion_prom','Brecha': 'Brecha_prom','Velocidad': 'Velocidad_prom'
        })

        T_real = Tr_unido.merge(
            promedios,
            on=['Nombre', 'EXT', 'Acceso', 'Dia_sem', 'Horas'],
            how='left'
        )

        # Creacion de una llave de union
        T_real = T_real.drop_duplicates(subset=['Nombre', 'EXT','Tiempo','Acceso','Dia_sem'], keep='last')
        T_real['Llave'] = T_real['Nombre'] + ' ' + T_real['Tiempo'].astype(str)
        T_real = T_real.reset_index()
        T_real=T_real.drop(['index'], axis=1)

        #Tabla de pivote
        T_real_p=T_real[['Nombre', 'Tiempo', 'EXT', 'Acceso', 'Tipo_sensor', 'Sensor','Deteccion','Dia_sem', 'Horas', 'Deteccion_prom','Llave','Ocupacion','Ocupacion_prom']].copy()
        T_real_p.loc[:, 'var_deteccion'] = ((T_real_p['Deteccion'] - T_real_p['Deteccion_prom']) / T_real_p['Deteccion_prom'])*100  #Atencion 
        T_real_p.loc[:, 'var_ocupacion'] = ((T_real_p['Ocupacion'] - T_real_p['Ocupacion_prom']) / T_real_p['Ocupacion_prom'])*100 #Atencion 
        T_real_p = T_real_p.melt(['Nombre', 'Tiempo', 'EXT', 'Acceso', 'Tipo_sensor', 'Sensor','Dia_sem', 'Horas','Llave'],var_name='Analisis', value_name='Value')
        T_real_p.replace([np.inf, -np.inf], np.nan, inplace=True)
        T_real_p['Value'] = pd.to_numeric(T_real_p['Value'], errors='coerce')


        # Lista ordenada de días SOLO para referencia visual
        dias_ordenados = ['Monday', 'Tuesday', 'Wednesday', 'Thursday','Friday', 'Saturday', 'Sunday']
        # Índice del día actual (0 = Monday, 6 = Sunday)
        indice_hoy = datetime.now().weekday()
        # Valores a asignar
        valores = [2, 1, 0, -1, -2, -3, -4]
        # Mapeo basado en índice (NO en texto)
        mapeo_dias = {}
        for i, valor in enumerate(valores):
            indice = (indice_hoy - i) % 7
            mapeo_dias[indice] = valor
        # Crear columna auxiliar de índice de día
        T_real_p['dia_idx'] = T_real_p['Tiempo'].dt.weekday
        # Asignar orden
        T_real_p['Orden'] = (T_real_p['dia_idx'].map(mapeo_dias).astype(str)+ T_real_p['Tiempo'].dt.strftime('%a').str.capitalize()+ ' '+ T_real_p['Horas'])



        ## ESCRITURA DE ARCHIVOS EN ORACLE

        # Actualización base de datos historicas mensuales
        dtype_PROM = { "Nombre" : VARCHAR2(100), "Tiempo" : TIMESTAMP, "EXT" : VARCHAR2(20), "Acceso" : VARCHAR2(20), "Tipo_sensor" : VARCHAR2(20),"Sensor" : VARCHAR2(20),
                            "Num_deteccion": NUMBER,"Deteccion": NUMBER,"Ocupacion": ORACLE_FLOAT,"Brecha": ORACLE_FLOAT,"Velocidad": ORACLE_FLOAT,"Dia_sem" : VARCHAR2(20),"Horas" : VARCHAR2(20),"Llave" : VARCHAR2(50),
                            "Deteccion_prom": NUMBER,"Ocupacion_prom": ORACLE_FLOAT,"Brecha_prom": ORACLE_FLOAT,"Velocidad_prom": ORACLE_FLOAT,
                    }
        T_real.to_sql(
            name="treal_det_sema",
            con=engine,
            if_exists="replace", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_PROM
        )


        # Actualización base de datos historicas mensuales
        dtype_PIVOT = { "Nombre" : VARCHAR2(100), "Tiempo" : TIMESTAMP, "EXT" : VARCHAR2(20), "Acceso" : VARCHAR2(20), "Tipo_sensor" : VARCHAR2(20),"Sensor" : VARCHAR2(20),
                        "Dia_sem" : VARCHAR2(20),"Horas" : VARCHAR2(20),"Llave" : VARCHAR2(50),
                            "Analisis": VARCHAR2(20),'Value': ORACLE_FLOAT, "Orden": VARCHAR2(20)
                    }
        T_real_p.to_sql(
            name="treal_pivot_sema",
            con=engine,
            if_exists="replace", # Append to existing table
            index=False,        # Do not write row index
            dtype=dtype_PIVOT
        )

        print ('Finaliza el actualizacion diario de detecciones SEMA')
        
if __name__ == "__main__":
    started_at_dt = datetime.now()
    started_at = started_at_dt.isoformat()
    try:
        main()
        if os.getenv("PIPELINE_INVOKED_BY_SCHEDULER", "") != "1":
            ended_at_dt = datetime.now()
            record_pipeline_success(
                "02_DETECCIONES_30MIN",
                source="pipeline",
                started_at=started_at,
                ended_at=ended_at_dt.isoformat(),
                duration_sec=round((ended_at_dt - started_at_dt).total_seconds(), 3),
            )
    except Exception as exc:
        error_trace = traceback.format_exc()
        logger.exception("Fallo pipeline 30min")
        if os.getenv("PIPELINE_INVOKED_BY_SCHEDULER", "") != "1":
            ended_at_dt = datetime.now()
            record_pipeline_failure(
                pipeline_id="02_DETECCIONES_30MIN",
                message=str(exc),
                error_type="pipeline_exception",
                details=error_trace,
                source="pipeline",
                started_at=started_at,
                ended_at=ended_at_dt.isoformat(),
                duration_sec=round((ended_at_dt - started_at_dt).total_seconds(), 3),
            )
        send_error_email(
            subject="[SEMA][ERROR] Pipeline 02_DETECCIONES_30MIN",
            body=f"Error: {exc}\n\nTraceback:\n{error_trace}",
        )
        raise
