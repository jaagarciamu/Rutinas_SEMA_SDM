"""Alerting transversal para pipelines: log local, heartbeat y Google Sheets."""

from __future__ import annotations

import json
import multiprocessing
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import gspread
import gspread_dataframe as gd
import pandas as pd
import yaml
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
LOGS_DIR = ROOT_DIR / "logs"
ALERT_LOG_PATH = LOGS_DIR / "pipeline_alerts.jsonl"
HEARTBEATS_DIR = LOGS_DIR / "heartbeats"
MISSED_STATE_PATH = LOGS_DIR / "missed_run_state.json"
ALERTS_CONFIG_PATH = ROOT_DIR / "config" / "alerts.yaml"
SHEET_CELL_MAX_CHARS = 49000


@dataclass
class MissedRun:
    pipeline_id: str
    slot_iso: str
    expected_time: str
    grace_minutes: int


def load_environment() -> None:
    load_dotenv(ROOT_DIR / "config" / ".env")
    load_dotenv(ROOT_DIR / ".env")


def _now_iso() -> str:
    return datetime.now().isoformat()


def _ensure_dirs() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    HEARTBEATS_DIR.mkdir(parents=True, exist_ok=True)


def _append_local_event(event: dict[str, Any]) -> None:
    _ensure_dirs()
    with ALERT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=True) + "\n")


def _get_sheet_client() -> tuple[gspread.Client, str, str] | None:
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    sheet_url = os.getenv("ALERTS_SHEET_URL", "").strip()
    worksheet_name = os.getenv("ALERTS_SHEET_WORKSHEET", "").strip()
    if not credentials_path or not sheet_url or not worksheet_name:
        return None
    creds_file = (ROOT_DIR / credentials_path).resolve()
    client = gspread.service_account(filename=str(creds_file))
    return client, sheet_url, worksheet_name


def _sort_sheet_events(df: pd.DataFrame) -> pd.DataFrame:
    sorted_df = df.copy()
    if "timestamp" not in sorted_df.columns:
        return sorted_df.reset_index(drop=True)

    sorted_df["_timestamp_sort"] = pd.to_datetime(sorted_df["timestamp"], errors="coerce")
    if "pipeline_id" in sorted_df.columns:
        sorted_df["pipeline_id"] = sorted_df["pipeline_id"].astype(str)
    if "event_type" in sorted_df.columns:
        sorted_df["event_type"] = sorted_df["event_type"].astype(str)

    order_columns = ["_timestamp_sort"]
    ascending = [False]
    if "pipeline_id" in sorted_df.columns:
        order_columns.append("pipeline_id")
        ascending.append(True)
    if "event_type" in sorted_df.columns:
        order_columns.append("event_type")
        ascending.append(True)

    sorted_df = sorted_df.sort_values(
        by=order_columns,
        ascending=ascending,
        na_position="last",
    )
    return sorted_df.drop(columns=["_timestamp_sort"], errors="ignore").reset_index(drop=True)


def _prepare_sheet_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    prepared = df.copy()
    for column in prepared.columns:
        prepared[column] = prepared[column].map(
            lambda value: (
                str(value)[:SHEET_CELL_MAX_CHARS] + " [truncated]"
                if len(str(value)) > SHEET_CELL_MAX_CHARS
                else value
            )
        )
    return prepared


def _append_event_to_sheet_sync(event: dict[str, Any]) -> None:
    try:
        sheet_cfg = _get_sheet_client()
        if not sheet_cfg:
            return
        client, sheet_url, worksheet_name = sheet_cfg
        worksheet = client.open_by_url(sheet_url).worksheet(worksheet_name)
        df = _prepare_sheet_dataframe(pd.DataFrame([event]))

        raw = worksheet.get_all_values()
        if not raw or not any(cell.strip() for cell in raw[0]):
            gd.set_with_dataframe(worksheet=worksheet, dataframe=_sort_sheet_events(df))
            return

        existing_df = pd.DataFrame.from_records(raw)
        existing_df.columns = existing_df.iloc[0]
        existing_df = existing_df.drop(existing_df.index[0]).reset_index(drop=True)
        merged = pd.concat([existing_df, df], ignore_index=True)
        merged = _sort_sheet_events(merged)
        worksheet.clear()
        gd.set_with_dataframe(worksheet=worksheet, dataframe=merged)
    except Exception:
        # Las alertas en Google Sheets son opcionales; no deben romper el pipeline.
        return


def _append_event_to_sheet(event: dict[str, Any]) -> None:
    timeout_seconds = int(os.getenv("ALERTS_SHEET_TIMEOUT_SECONDS", "30").strip() or "30")
    if timeout_seconds <= 0:
        _append_event_to_sheet_sync(event)
        return

    process = multiprocessing.get_context("spawn").Process(
        target=_append_event_to_sheet_sync,
        args=(event,),
    )
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(5)


def record_event(
    pipeline_id: str,
    event_type: str,
    severity: str,
    message: str,
    details: str = "",
    source: str = "pipeline",
    started_at: str = "",
    ended_at: str = "",
    duration_sec: float | None = None,
) -> None:
    load_environment()
    event = {
        "timestamp": _now_iso(),
        "pipeline_id": pipeline_id,
        "event_type": event_type,
        "severity": severity,
        "message": message,
        "details": details,
        "source": source,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_sec": duration_sec if duration_sec is not None else "",
    }
    _append_local_event(event)
    _append_event_to_sheet(event)


def record_pipeline_success(
    pipeline_id: str,
    source: str = "pipeline",
    started_at: str = "",
    ended_at: str = "",
    duration_sec: float | None = None,
) -> None:
    _ensure_dirs()
    payload = {"timestamp": _now_iso(), "pipeline_id": pipeline_id}
    hb_file = HEARTBEATS_DIR / f"{pipeline_id}.jsonl"
    with hb_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    record_event(
        pipeline_id=pipeline_id,
        event_type="success",
        severity="info",
        message="Pipeline finalizado con exito",
        source=source,
        started_at=started_at,
        ended_at=ended_at,
        duration_sec=duration_sec,
    )


def record_pipeline_failure(
    pipeline_id: str,
    message: str,
    error_type: str = "runtime_error",
    details: str = "",
    source: str = "pipeline",
    started_at: str = "",
    ended_at: str = "",
    duration_sec: float | None = None,
) -> None:
    record_event(
        pipeline_id=pipeline_id,
        event_type=error_type,
        severity="error",
        message=message,
        details=details,
        source=source,
        started_at=started_at,
        ended_at=ended_at,
        duration_sec=duration_sec,
    )


def _load_success_timestamps(pipeline_id: str) -> list[datetime]:
    hb_file = HEARTBEATS_DIR / f"{pipeline_id}.jsonl"
    if not hb_file.exists():
        return []
    values: list[datetime] = []
    for line in hb_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            values.append(datetime.fromisoformat(payload["timestamp"]))
        except Exception:
            continue
    return values


def _load_missed_state() -> dict[str, bool]:
    if not MISSED_STATE_PATH.exists():
        return {}
    try:
        return json.loads(MISSED_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_missed_state(state: dict[str, bool]) -> None:
    _ensure_dirs()
    MISSED_STATE_PATH.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")


def _parse_alerts_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def check_and_alert_missed_runs(
    now: datetime | None = None,
    config_path: Path = ALERTS_CONFIG_PATH,
) -> list[MissedRun]:
    load_environment()
    _ensure_dirs()
    now = now or datetime.now()
    cfg = _parse_alerts_config(config_path)
    pipelines = cfg.get("pipelines", [])
    if not isinstance(pipelines, list):
        return []

    state = _load_missed_state()
    missed: list[MissedRun] = []

    for pipeline_cfg in pipelines:
        pipeline_id = str(pipeline_cfg.get("id", "")).strip()
        if not pipeline_id:
            continue
        if not bool(pipeline_cfg.get("enabled", True)):
            continue

        expected_times = pipeline_cfg.get("expected_times", [])
        if not isinstance(expected_times, list):
            continue
        grace_minutes = int(pipeline_cfg.get("grace_minutes", 60))
        successes = _load_success_timestamps(pipeline_id)
        latest_success = max(successes) if successes else None

        for hhmm in expected_times:
            try:
                hour_str, minute_str = str(hhmm).split(":")
                slot = now.replace(
                    hour=int(hour_str),
                    minute=int(minute_str),
                    second=0,
                    microsecond=0,
                )
            except Exception:
                continue

            if now < slot + timedelta(minutes=grace_minutes):
                continue

            slot_end = slot + timedelta(minutes=grace_minutes)
            is_covered = any(slot <= ts <= slot_end for ts in successes)
            state_key = f"{pipeline_id}|{slot.isoformat()}"
            if is_covered:
                state[state_key] = True
                continue

            if state.get(state_key):
                continue

            missed_run = MissedRun(
                pipeline_id=pipeline_id,
                slot_iso=slot.isoformat(),
                expected_time=str(hhmm),
                grace_minutes=grace_minutes,
            )
            missed.append(missed_run)
            state[state_key] = True

            record_event(
                pipeline_id=pipeline_id,
                event_type="missed_expected_run",
                severity="error",
                message=(
                    f"Ejecucion esperada no cumplida para {hhmm}; "
                    f"gracia {grace_minutes} min"
                ),
                details=f"slot={slot.isoformat()}",
                source="monitor",
            )

        max_silence_raw = pipeline_cfg.get("max_silence_minutes", "")
        max_silence_minutes = 0
        if str(max_silence_raw).strip():
            try:
                max_silence_minutes = int(max_silence_raw)
            except Exception:
                max_silence_minutes = 0

        if max_silence_minutes > 0:
            threshold = timedelta(minutes=max_silence_minutes)
            is_silent = latest_success is None or (now - latest_success) > threshold
            silence_key = f"{pipeline_id}|max_silence_active"

            if is_silent:
                if not state.get(silence_key, False):
                    record_event(
                        pipeline_id=pipeline_id,
                        event_type="max_silence_exceeded",
                        severity="error",
                        message=(
                            f"Pipeline sin ejecucion exitosa por mas de {max_silence_minutes} minutos"
                        ),
                        details=f"last_success={latest_success.isoformat() if latest_success else 'none'}",
                        source="monitor",
                    )
                    state[silence_key] = True
            else:
                state[silence_key] = False

    _save_missed_state(state)
    return missed
