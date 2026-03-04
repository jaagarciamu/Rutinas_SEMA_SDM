"""Clase base para estandarizar ejecución y observabilidad de pipelines."""

from __future__ import annotations

import json
import logging
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Formatter JSON para logging estructurado."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "event"):
            payload["event"] = record.event
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


class Pipeline(ABC):
    """Contrato base para pipelines ejecutables y auditables."""

    def __init__(self, name: str, logger: logging.Logger | None = None) -> None:
        self.name = name
        self.logger = logger or self._build_logger()

    @staticmethod
    def _build_logger() -> logging.Logger:
        logger = logging.getLogger("pipelines")
        if logger.handlers:
            return logger

        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        return logger

    @abstractmethod
    def execute(self) -> None:
        """Implementación específica del pipeline."""

    def run(self) -> None:
        """Orquesta ejecución con trazabilidad de inicio/fin y errores."""
        start_time = time.perf_counter()
        self.logger.info(
            "Pipeline iniciado",
            extra={"event": "pipeline_start", "pipeline": self.name},
        )

        try:
            self.execute()
            elapsed = round(time.perf_counter() - start_time, 3)
            self.logger.info(
                "Pipeline finalizado correctamente",
                extra={
                    "event": "pipeline_success",
                    "pipeline": self.name,
                    "duration_sec": elapsed,
                },
            )
        except Exception:
            elapsed = round(time.perf_counter() - start_time, 3)
            self.logger.exception(
                "Pipeline finalizado con error",
                extra={
                    "event": "pipeline_error",
                    "pipeline": self.name,
                    "duration_sec": elapsed,
                },
            )
            raise
