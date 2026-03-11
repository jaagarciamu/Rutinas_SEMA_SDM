"""Scheduler liviano para ejecutar pipelines por intervalo y offset."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pipelines.alerting import (
    check_and_alert_missed_runs,
    record_pipeline_failure,
    record_pipeline_success,
)

ROOT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = ROOT_DIR / "config" / "config.yaml"
SCHEDULE_PATH = ROOT_DIR / "config" / "schedule.yaml"


def load_environment() -> None:
    """Carga variables desde config/.env y luego .env en raíz (si existe)."""
    load_dotenv(ROOT_DIR / "config" / ".env")
    load_dotenv(ROOT_DIR / ".env")


class JsonFormatter(logging.Formatter):
    """Formatter JSON para logs del scheduler."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        return json.dumps(payload, ensure_ascii=True)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"El archivo {path} debe contener un objeto YAML raíz")
    return data


def setup_logger(logs_dir: Path) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("scheduler")

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = JsonFormatter()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(logs_dir / "scheduler.log", encoding="utf-8")
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def should_run(now: datetime, interval_minutes: int, offset_minutes: int) -> bool:
    if interval_minutes <= 0:
        raise ValueError("interval_minutes debe ser mayor que 0")
    if offset_minutes < 0:
        raise ValueError("offset_minutes no puede ser negativo")

    minute_of_day = now.hour * 60 + now.minute
    return (minute_of_day - offset_minutes) % interval_minutes == 0


def weekday_is_allowed(now: datetime, weekdays: list[Any] | None) -> bool:
    if not weekdays:
        return True

    day_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
        "mon": 0,
        "tue": 1,
        "wed": 2,
        "thu": 3,
        "fri": 4,
        "sat": 5,
        "sun": 6,
    }
    allowed: set[int] = set()
    for raw in weekdays:
        if isinstance(raw, int) and 0 <= raw <= 6:
            allowed.add(raw)
            continue
        value = str(raw).strip().lower()
        if value in day_map:
            allowed.add(day_map[value])
            continue
        if value.isdigit():
            day = int(value)
            if 0 <= day <= 6:
                allowed.add(day)
    if not allowed:
        return True
    return now.weekday() in allowed


def execute_pipeline(
    logger: logging.Logger,
    pipeline_id: str,
    script: str,
    args: list[str],
) -> int:
    script_path = ROOT_DIR / script
    started_at_dt = datetime.now()
    started_at = started_at_dt.isoformat()

    if not script_path.exists():
        logger.error(
            "Script no encontrado",
            extra={"event": "pipeline_missing_script", "pipeline": pipeline_id},
        )
        record_pipeline_failure(
            pipeline_id=pipeline_id,
            message="Script no encontrado",
            error_type="missing_script",
            details=str(script_path),
            source="scheduler",
            started_at=started_at,
            ended_at=datetime.now().isoformat(),
            duration_sec=0.0,
        )
        return 2

    cmd = [sys.executable, str(script_path), *args]
    logger.info(
        "Ejecutando pipeline",
        extra={
            "event": "pipeline_execute",
            "pipeline": pipeline_id,
            "command": " ".join(cmd),
        },
    )

    result = subprocess.run(
        cmd,
        cwd=str(ROOT_DIR),
        env={**os.environ, "PIPELINE_INVOKED_BY_SCHEDULER": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    ended_at_dt = datetime.now()
    ended_at = ended_at_dt.isoformat()
    duration_sec = round((ended_at_dt - started_at_dt).total_seconds(), 3)

    if result.stdout:
        logger.info(
            result.stdout.strip(),
            extra={"event": "pipeline_stdout", "pipeline": pipeline_id},
        )

    if result.stderr:
        logger.warning(
            result.stderr.strip(),
            extra={"event": "pipeline_stderr", "pipeline": pipeline_id},
        )

    if result.returncode != 0:
        logger.error(
            "Pipeline finalizó con error",
            extra={
                "event": "pipeline_failed",
                "pipeline": pipeline_id,
                "return_code": result.returncode,
            },
        )
        record_pipeline_failure(
            pipeline_id=pipeline_id,
            message=f"Pipeline finalizó con código {result.returncode}",
            error_type="pipeline_failed",
            details=result.stderr.strip(),
            source="scheduler",
            started_at=started_at,
            ended_at=ended_at,
            duration_sec=duration_sec,
        )
    else:
        logger.info(
            "Pipeline finalizó OK",
            extra={"event": "pipeline_success", "pipeline": pipeline_id},
        )
        record_pipeline_success(
            pipeline_id=pipeline_id,
            source="scheduler",
            started_at=started_at,
            ended_at=ended_at,
            duration_sec=duration_sec,
        )

    return result.returncode


def main() -> int:
    load_environment()

    config = load_yaml(CONFIG_PATH)
    schedule = load_yaml(SCHEDULE_PATH)

    logs_dir = ROOT_DIR / config.get("paths", {}).get("logs_dir", "logs")
    logger = setup_logger(logs_dir)

    pipelines = schedule.get("pipelines", [])
    if not isinstance(pipelines, list):
        logger.error(
            "Formato inválido de pipelines en schedule.yaml",
            extra={"event": "invalid_schedule"},
        )
        return 1

    now = datetime.now()
    logger.info(
        "Inicio de ciclo scheduler",
        extra={"event": "scheduler_cycle_start", "current_time": now.isoformat()},
    )

    max_rc = 0

    for pipeline in pipelines:
        pipeline_id = str(pipeline.get("id", "unknown"))
        enabled = bool(pipeline.get("enabled", True))
        if not enabled:
            logger.info(
                "Pipeline deshabilitado, se omite",
                extra={"event": "pipeline_skipped_disabled", "pipeline": pipeline_id},
            )
            continue

        interval = int(pipeline.get("interval_minutes", 60))
        offset = int(pipeline.get("offset_minutes", 0))
        weekdays = pipeline.get("weekdays", [])
        if weekdays is not None and not isinstance(weekdays, list):
            logger.error(
                "weekdays debe ser una lista",
                extra={"event": "invalid_weekdays", "pipeline": pipeline_id},
            )
            max_rc = max(max_rc, 1)
            continue

        if should_run(now, interval, offset) and weekday_is_allowed(now, weekdays):
            script = str(pipeline.get("script", ""))
            args = pipeline.get("args", [])
            if not isinstance(args, list):
                logger.error(
                    "args debe ser una lista",
                    extra={"event": "invalid_args", "pipeline": pipeline_id},
                )
                max_rc = max(max_rc, 1)
                continue

            rc = execute_pipeline(logger, pipeline_id, script, [str(arg) for arg in args])
            max_rc = max(max_rc, rc)
        else:
            logger.info(
                "Pipeline fuera de ventana, se omite",
                extra={
                    "event": "pipeline_skipped_window",
                    "pipeline": pipeline_id,
                    "interval_minutes": interval,
                    "offset_minutes": offset,
                    "weekdays": weekdays,
                },
            )

    logger.info(
        "Fin de ciclo scheduler",
        extra={"event": "scheduler_cycle_end", "max_return_code": max_rc},
    )
    missed = check_and_alert_missed_runs(now=now)
    if missed:
        logger.warning(
            "Se detectaron ejecuciones esperadas no cumplidas",
            extra={"event": "missed_expected_runs", "count": len(missed)},
        )
    return max_rc


if __name__ == "__main__":
    raise SystemExit(main())
