# 00_RUTINAS_SEMA_SDM

Automatización de pipelines Python para procesar entradas de Google Workspace (correo y Google Sheets), transformar datos y cargarlos en esquemas Oracle.

## Arquitectura General
- `run_scheduler.py`: orquestador liviano ejecutado por agenda (Task Scheduler, Jenkins, Airflow trigger, etc.).
- `config/config.yaml`: configuración de rutas y directorios.
- `config/schedule.yaml`: definición declarativa de pipelines y ventanas de ejecución.
- `pipelines/`: implementación de cada rutina y componentes compartidos.
- `logs/`: salida de observabilidad en archivos `.log` estructurados (JSON line).
- `google_apps_script/`: integración base para Gmail/Sheets.

Más detalle técnico en `docs/arquitectura.md`.

## Instalación
1. Crear entorno virtual:
   ```bash
   python -m venv venv
   ```
2. Activar entorno:
   - Windows (PowerShell):
     ```powershell
     .\venv\Scripts\Activate.ps1
     ```
   - Linux/macOS:
     ```bash
     source venv/bin/activate
     ```
3. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```

## Variables de Entorno
1. Copiar `config/.env.example` a `config/.env`.
2. Completar credenciales Oracle y Google.
3. El scheduler y pipelines cargan `config/.env` automáticamente.

Variables clave:
- `ORACLE_USER`
- `ORACLE_PASSWORD`
- `ORACLE_DSN`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_WORKSPACE_DELEGATED_USER`

## Cambio de Ubicacion en Drive (Detecciones)
Cuando la carpeta de origen cambia y los IDs de Drive se regeneran, el flujo recomendado es:

1. Ejecutar revision previa (no escribe cambios):
   ```bash
   python tools/sync_detecciones_registry.py \
     --show-pending 100 \
     --pending-csv data/tmp/pendientes_preview.csv \
     --show-id-changes 100 \
     --id-changes-csv data/tmp/id_changes_preview.csv
   ```
2. Validar `pendientes_preview.csv`, `id_changes_preview.csv` y la `Fecha corte (max date en Procesado)` mostrada en consola.
3. Si la validacion es correcta, aplicar sincronizacion de IDs:
   ```bash
   python tools/sync_detecciones_registry.py \
     --apply
   ```
4. Ejecutar el pipeline normalmente.

Notas:
- `sync_detecciones_registry.py` compara por `name`, actualiza `id` cuando cambia, y crea filas nuevas en `Pendiente`.
- Si `--new-folder` no se envia, toma `GOOGLE_DETECCIONES_DRIVE_FOLDER_ID` desde `config/.env`.
- El `pendientes_preview` replica el criterio real del pipeline `01_DETECCIONES_SEMA.py` (filtro por nombre, `text/csv`, y corte por ultima fecha `Procesado`).
- `01_DETECCIONES_SEMA.py` ya integra sincronizacion automatica al inicio con la carpeta definida en `GOOGLE_DETECCIONES_DRIVE_FOLDER_ID`.

### Operacion Segura (con pasos intermedios)
Tambien puedes usar un comando guiado con confirmaciones intermedias:

```bash
tools/operacion_segura_detecciones.sh \
  --show-pending 200
```

El script realiza:
1. `review` (dry-run) + export de previews a `data/tmp/`.
2. Pregunta de confirmacion para `apply` en Google Sheets.
3. Pregunta de confirmacion para ejecutar `pipelines/01_DETECCIONES_SEMA.py`.

## Configuración del Scheduler
`config/schedule.yaml` define para cada pipeline:
- `id`: identificador único.
- `script`: ruta relativa al script.
- `interval_minutes`: periodicidad de ejecución.
- `offset_minutes`: desplazamiento del inicio dentro del intervalo.
- `enabled`: habilitar/deshabilitar.
- `args`: argumentos opcionales para CLI del script.

Regla de ejecución:
- Se ejecuta cuando `(minuto_del_dia - offset_minutes) % interval_minutes == 0`.

## Alertas y Monitoreo de Ejecución
El proyecto incluye una estrategia transversal de alertas para todos los pipelines:

- Log local central en `logs/pipeline_alerts.jsonl`.
- Heartbeats de éxito por pipeline en `logs/heartbeats/<pipeline_id>.jsonl`.
- Detección de ejecuciones esperadas no cumplidas según `config/alerts.yaml`.
- Registro opcional de alertas en Google Sheets (`ALERTS_SHEET_URL`, `ALERTS_SHEET_WORKSHEET`).

### Configuración de horarios esperados
Editar `config/alerts.yaml` para cada pipeline:
- `id`: identificador del pipeline.
- `expected_times`: horas esperadas (formato `HH:MM`).
- `grace_minutes`: tolerancia máxima para considerar la ejecución como cumplida.

Ejemplo para detecciones:
- `01_DETECCIONES_SEMA` a `02:00` y `14:00`.

Si pasada la ventana de gracia no hay heartbeat de éxito, se registra evento:
- `event_type=missed_expected_run`
- `severity=error`

## Ejecución Manual
```bash
python run_scheduler.py
```

## Programación en Windows Task Scheduler
1. Crear tarea básica o avanzada.
2. Trigger sugerido: cada 1 minuto (o cada 5 minutos según necesidad de granularidad).
3. Acción:
   - Programa/script: ruta a `python.exe` del entorno virtual.
   - Argumentos: `run_scheduler.py`
   - Iniciar en: ruta raíz del proyecto.
4. Activar opción de reintento en caso de error (opcional).

## Migración Futura a Jenkins o Airflow
- Mantener la lógica de negocio dentro de scripts `pipelines/*.py`.
- Usar Jenkins/Airflow solo como capa de orquestación.
- Reutilizar `schedule.yaml` como fuente de parámetros o traducirlo a DAG/jobs.
