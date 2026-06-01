"""Scheduler liviano para ejecutar pipelines por intervalo y offset."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from config.colombia_holidays import colombian_holidays
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


def validate_schedule_alignment(
    logger: logging.Logger,
    pipelines: list[Any],
    tick_minutes: int,
) -> bool:
    if tick_minutes <= 0:
        logger.error(
            "scheduler.tick_minutes debe ser mayor que 0",
            extra={"event": "invalid_scheduler_tick"},
        )
        return False

    valid = True
    for pipeline in pipelines:
        pipeline_id = str(pipeline.get("id", "unknown"))
        try:
            interval = int(pipeline.get("interval_minutes", 60))
            offset = int(pipeline.get("offset_minutes", 0))
            start_minute = parse_time_of_day(pipeline.get("start_time"))
            end_minute = parse_time_of_day(pipeline.get("end_time"))
        except (TypeError, ValueError):
            logger.error(
                "interval_minutes/offset_minutes/start_time/end_time invalidos",
                extra={"event": "invalid_schedule_values", "pipeline": pipeline_id},
            )
            valid = False
            continue

        if interval % tick_minutes != 0 or offset % tick_minutes != 0:
            logger.error(
                "Pipeline no alineado con la cadencia del scheduler",
                extra={
                    "event": "pipeline_misaligned_tick",
                    "pipeline": pipeline_id,
                    "interval_minutes": interval,
                    "offset_minutes": offset,
                    "tick_minutes": tick_minutes,
                },
            )
            valid = False

        if start_minute is not None and start_minute % tick_minutes != 0:
            logger.error(
                "start_time no alineado con la cadencia del scheduler",
                extra={
                    "event": "pipeline_start_time_misaligned_tick",
                    "pipeline": pipeline_id,
                    "start_time": pipeline.get("start_time"),
                    "tick_minutes": tick_minutes,
                },
            )
            valid = False

        if end_minute is not None and end_minute % tick_minutes != 0:
            logger.error(
                "end_time no alineado con la cadencia del scheduler",
                extra={
                    "event": "pipeline_end_time_misaligned_tick",
                    "pipeline": pipeline_id,
                    "end_time": pipeline.get("end_time"),
                    "tick_minutes": tick_minutes,
                },
            )
            valid = False

    return valid


def should_run(now: datetime, interval_minutes: int, offset_minutes: int) -> bool:
    if interval_minutes <= 0:
        raise ValueError("interval_minutes debe ser mayor que 0")
    if offset_minutes < 0:
        raise ValueError("offset_minutes no puede ser negativo")

    minute_of_day = now.hour * 60 + now.minute
    return (minute_of_day - offset_minutes) % interval_minutes == 0


def parse_time_of_day(value: Any) -> int | None:
    if value in (None, ""):
        return None

    parts = str(value).strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("La hora debe tener formato HH:MM")

    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("La hora debe estar entre 00:00 y 23:59")
    return hour * 60 + minute


def time_window_is_allowed(now: datetime, start_time: Any, end_time: Any) -> bool:
    start_minute = parse_time_of_day(start_time)
    end_minute = parse_time_of_day(end_time)
    if start_minute is None and end_minute is None:
        return True

    minute_of_day = now.hour * 60 + now.minute
    if start_minute is None:
        return minute_of_day <= end_minute
    if end_minute is None:
        return minute_of_day >= start_minute
    if start_minute <= end_minute:
        return start_minute <= minute_of_day <= end_minute
    return minute_of_day >= start_minute or minute_of_day <= end_minute


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


def previous_day_is_holiday(now: datetime) -> bool:
    previous_day = (now - timedelta(days=1)).date()
    candidate_years = {previous_day.year, now.year}
    holiday_dates: set = set()
    for year in candidate_years:
        holiday_dates.update(colombian_holidays(year))
    return previous_day in holiday_dates


def month_weekday_is_allowed(now: datetime, month_weekday: Any, month_weekday_ordinal: Any) -> bool:
    if month_weekday in (None, "") or month_weekday_ordinal in (None, ""):
        return True

    weekday_map = {
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

    weekday_value = str(month_weekday).strip().lower()
    if weekday_value not in weekday_map:
        return True

    try:
        ordinal = int(month_weekday_ordinal)
    except (TypeError, ValueError):
        return True

    if ordinal <= 0:
        return True

    target_weekday = weekday_map[weekday_value]
    first_day = now.replace(day=1)
    days_ahead = (target_weekday - first_day.weekday()) % 7
    first_occurrence = first_day + timedelta(days=days_ahead)
    target_date = first_occurrence + timedelta(weeks=ordinal - 1)
    return now.date() == target_date.date()


def execute_pipeline(
    logger: logging.Logger,
    pipeline_id: str,
    script: str,
    args: list[str],
    timeout_seconds: int,
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

    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT_DIR),
            env={**os.environ, "PIPELINE_INVOKED_BY_SCHEDULER": "1"},
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        ended_at_dt = datetime.now()
        ended_at = ended_at_dt.isoformat()
        duration_sec = round((ended_at_dt - started_at_dt).total_seconds(), 3)
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        if stdout:
            logger.info(
                stdout,
                extra={"event": "pipeline_stdout", "pipeline": pipeline_id},
            )
        if stderr:
            logger.warning(
                stderr,
                extra={"event": "pipeline_stderr", "pipeline": pipeline_id},
            )
        logger.error(
            "Pipeline excedio el tiempo maximo permitido",
            extra={
                "event": "pipeline_timeout",
                "pipeline": pipeline_id,
                "timeout_seconds": timeout_seconds,
            },
        )
        record_pipeline_failure(
            pipeline_id=pipeline_id,
            message=f"Pipeline excedio el timeout de {timeout_seconds} segundos",
            error_type="pipeline_timeout",
            details=stderr,
            source="scheduler",
            started_at=started_at,
            ended_at=ended_at,
            duration_sec=duration_sec,
        )
        return 124

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

    tick_minutes = int(config.get("scheduler", {}).get("tick_minutes", 5))
    default_pipeline_timeout_seconds = int(
        config.get("scheduler", {}).get("pipeline_timeout_minutes", 20)
    ) * 60
    if not validate_schedule_alignment(logger, pipelines, tick_minutes):
        return 1

    now = datetime.now()
    logger.info(
        "Inicio de ciclo scheduler",
        extra={
            "event": "scheduler_cycle_start",
            "current_time": now.isoformat(),
            "tick_minutes": tick_minutes,
        },
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
        month_weekday = pipeline.get("month_weekday")
        month_weekday_ordinal = pipeline.get("month_weekday_ordinal")
        run_if_previous_day_holiday = bool(pipeline.get("run_if_previous_day_holiday", False))
        start_time = pipeline.get("start_time")
        end_time = pipeline.get("end_time")
        if weekdays is not None and not isinstance(weekdays, list):
            logger.error(
                "weekdays debe ser una lista",
                extra={"event": "invalid_weekdays", "pipeline": pipeline_id},
            )
            max_rc = max(max_rc, 1)
            continue

        day_allowed = weekday_is_allowed(now, weekdays)
        if run_if_previous_day_holiday and previous_day_is_holiday(now):
            day_allowed = True

        month_allowed = month_weekday_is_allowed(now, month_weekday, month_weekday_ordinal)
        has_month_rule = month_weekday is not None or month_weekday_ordinal is not None
        run_allowed = month_allowed if has_month_rule else should_run(now, interval, offset)

        if run_allowed and day_allowed and time_window_is_allowed(now, start_time, end_time):
            script = str(pipeline.get("script", ""))
            args = pipeline.get("args", [])
            if not isinstance(args, list):
                logger.error(
                    "args debe ser una lista",
                    extra={"event": "invalid_args", "pipeline": pipeline_id},
                )
                max_rc = max(max_rc, 1)
                continue

            timeout_seconds = int(
                pipeline.get("timeout_minutes", default_pipeline_timeout_seconds // 60)
            ) * 60
            rc = execute_pipeline(
                logger,
                pipeline_id,
                script,
                [str(arg) for arg in args],
                timeout_seconds,
            )
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
                    "run_if_previous_day_holiday": run_if_previous_day_holiday,
                    "month_weekday": month_weekday,
                    "month_weekday_ordinal": month_weekday_ordinal,
                    "start_time": start_time,
                    "end_time": end_time,
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
