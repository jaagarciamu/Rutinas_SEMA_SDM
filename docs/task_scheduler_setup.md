# Configuracion Task Scheduler

## Nombre de la tarea
`SEMA Scheduler`

## Objetivo
Ejecutar `run_scheduler.py` cada 5 minutos para disparar los pipelines definidos en `config/schedule.yaml` segun su `interval_minutes` y `offset_minutes`.

## Equipo
Completar al momento de instalar:

- Equipo: `[NOMBRE_DEL_EQUIPO]`
- Usuario de ejecucion: `[USUARIO_LOCAL_O_DE_SERVICIO]`
- Fecha de instalacion: `[AAAA-MM-DD]`

## Ruta del proyecto
`C:\Users\jagarcia\Documents\01_SEMAFOROS\00_Rutinas_SEMA_SDM`

## Archivos involucrados
- [`run_scheduler.py`](/c:/Users/jagarcia/Documents/01_SEMAFOROS/00_Rutinas_SEMA_SDM/run_scheduler.py)
- [`run_scheduler_task.bat`](/c:/Users/jagarcia/Documents/01_SEMAFOROS/00_Rutinas_SEMA_SDM/run_scheduler_task.bat)
- [`config/config.yaml`](/c:/Users/jagarcia/Documents/01_SEMAFOROS/00_Rutinas_SEMA_SDM/config/config.yaml)
- [`config/schedule.yaml`](/c:/Users/jagarcia/Documents/01_SEMAFOROS/00_Rutinas_SEMA_SDM/config/schedule.yaml)
- [`config/.env`](/c:/Users/jagarcia/Documents/01_SEMAFOROS/00_Rutinas_SEMA_SDM/config/.env)

## Configuracion de la tarea

### General
- Nombre: `SEMA Scheduler`
- Ejecutar tanto si el usuario inicio sesion como si no
- Ejecutar con los privilegios mas altos
- Configurar para la version actual de Windows del equipo

### Trigger
- Tipo: `Diario`
- Hora inicial sugerida: `00:00:00`
- Repetir tarea cada: `5 minutos`
- Durante: `1 dia`
- Habilitado: `Si`

### Accion
- Programa o script:
  `cmd.exe`
- Agregar argumentos:
  `/c "C:\Users\jagarcia\Documents\01_SEMAFOROS\00_Rutinas_SEMA_SDM\run_scheduler_task.bat"`
- Iniciar en:
  `C:\Users\jagarcia\Documents\01_SEMAFOROS\00_Rutinas_SEMA_SDM`

### Condiciones
- Desactivar restricciones de energia si el equipo debe correr siempre
- Desactivar restricciones de inactividad si no aplican

### Configuracion adicional
- Permitir que la tarea se ejecute a peticion
- Ejecutar la tarea tan pronto como sea posible despues de un inicio omitido
- Si la tarea ya se esta ejecutando: `No iniciar una nueva instancia`
- Si la tarea falla: reiniciar cada `5 minutos`
- Intentar reiniciar hasta `3 veces`

## Cadencia oficial del scheduler
En [`config/config.yaml`](/c:/Users/jagarcia/Documents/01_SEMAFOROS/00_Rutinas_SEMA_SDM/config/config.yaml) debe existir:

```yaml
scheduler:
  tick_minutes: 5
```

Todos los `interval_minutes` y `offset_minutes` de [`config/schedule.yaml`](/c:/Users/jagarcia/Documents/01_SEMAFOROS/00_Rutinas_SEMA_SDM/config/schedule.yaml) deben ser multiplos de `5`.

## Ventanas actuales de ejecucion
- `01_DETECCIONES_SEMA`: `02:00` y `14:00`
- `02_DETECCIONES_30MIN`: `:25` y `:55`
- `03_ESTADOS_SEMA_SUA`: `:15` y `:45`
- `04_PLANES_SEMA`: `:20` y `:50`
- `05_ANEXO_SEMA`: `01:00` y `13:00`
- `06_MANTENIMIENTO_SEMA`: `03:00` los lunes

## Precondiciones antes de habilitar la tarea
- Existe `.\venv\Scripts\python.exe`
- `config\.env` esta completo
- El archivo de credenciales Google existe y es accesible
- Hay conectividad a Oracle
- `PLANES_ACTUALIZACION_SUANET_SGM_ENABLED=false` mientras no se corrijan credenciales de `SGMEDICION`

## Pruebas manuales previas
Ejecutar desde PowerShell en la raiz del proyecto:

```powershell
.\venv\Scripts\python.exe .\run_scheduler.py
cmd /c .\run_scheduler_task.bat
```

## Validacion post-instalacion
1. Ejecutar manualmente la tarea desde el Programador de tareas.
2. Confirmar una nueva linea `START` y `END` en `logs\task_scheduler.log`.
3. Confirmar que `run_scheduler.py` termina con `rc=0` cuando no hay error de negocio.
4. Confirmar que los pipelines solo corren en sus ventanas programadas.
5. Confirmar heartbeats en `logs\heartbeats\`.

## Logs a revisar
- [`logs/task_scheduler.log`](/c:/Users/jagarcia/Documents/01_SEMAFOROS/00_Rutinas_SEMA_SDM/logs/task_scheduler.log)
- [`logs/pipeline_alerts.jsonl`](/c:/Users/jagarcia/Documents/01_SEMAFOROS/00_Rutinas_SEMA_SDM/logs/pipeline_alerts.jsonl)
- [`logs/heartbeats`](/c:/Users/jagarcia/Documents/01_SEMAFOROS/00_Rutinas_SEMA_SDM/logs/heartbeats)

## Estado validado del proyecto
- `run_scheduler.py`: validado
- `run_scheduler_task.bat`: validado
- `03_ESTADOS_SEMA_SUA`: validado con archivos nuevos
- `04_PLANES_SEMA`: validado correctamente con `PLANES_ACTUALIZACION_SUANET_SGM_ENABLED=false`

## Observaciones operativas
- Si el scheduler no dispara un pipeline esperado, revisar primero la hora real de corrida del Task Scheduler contra el `offset_minutes`.
- Si `04_PLANES_SEMA` falla con `ORA-01017`, revisar credenciales de `PLANES_SGM_ORACLE_*` o mantener deshabilitada la actualizacion SGM.
- Si `03_ESTADOS_SEMA_SUA` falla con `ORA-00942`, revisar permisos reales del usuario Oracle sobre `DROP TABLE` cuando usa `if_exists="replace"`.
