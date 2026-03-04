# Arquitectura - 00_RUTINAS_SEMA_SDM

## Objetivo
Automatizar pipelines de ingestión y carga de datos desde Google Workspace hacia Oracle, con ejecución programada desde Windows y diseño portable a orquestadores como Jenkins o Airflow.

## Componentes
- `pipelines/`: lógica de negocio por pipeline y clases base reutilizables.
- `config/`: configuración desacoplada (`config.yaml`, `schedule.yaml`, variables de entorno).
- `run_scheduler.py`: orquestador liviano para evaluación temporal y disparo de scripts.
- `logs/`: trazabilidad operacional por archivo.
- `google_apps_script/`: automatizaciones complementarias para Gmail/Sheets.

## Flujo General
1. Scheduler carga configuración y calendario.
2. Evalúa reglas `interval_minutes` + `offset_minutes`.
3. Ejecuta scripts habilitados con `subprocess`.
4. Cada ejecución escribe logs estructurados y códigos de salida.
5. Pipelines consumen fuentes Google y persisten en Oracle.

## Escalabilidad
- La definición de pipelines en YAML permite migrar reglas a Jenkins/Airflow sin rediseñar lógica interna.
- La clase base `Pipeline` estandariza observabilidad y manejo de errores.
