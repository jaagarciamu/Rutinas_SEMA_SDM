"""Utilidades para conexión a Oracle usando variables de entorno."""

from __future__ import annotations

import logging
import os

import oracledb

logger = logging.getLogger(__name__)


class OracleConnectionError(RuntimeError):
    """Error funcional para fallas al construir la conexión Oracle."""


def get_connection() -> oracledb.Connection:
    """Crea y retorna una conexión Oracle desde variables de entorno.

    Requiere:
    - ORACLE_USER
    - ORACLE_PASSWORD
    - ORACLE_DSN
    """
    user = os.getenv("ORACLE_USER")
    password = os.getenv("ORACLE_PASSWORD")
    dsn = os.getenv("ORACLE_DSN")

    missing_vars = [
        var_name
        for var_name, value in {
            "ORACLE_USER": user,
            "ORACLE_PASSWORD": password,
            "ORACLE_DSN": dsn,
        }.items()
        if not value
    ]

    if missing_vars:
        raise OracleConnectionError(
            f"Variables de entorno requeridas ausentes: {', '.join(missing_vars)}"
        )

    try:
        return oracledb.connect(user=user, password=password, dsn=dsn)
    except oracledb.Error as exc:
        logger.exception(
            "No fue posible establecer conexión Oracle",
            extra={"event": "oracle_connection_error", "dsn": dsn},
        )
        raise OracleConnectionError("Fallo de conexión a Oracle") from exc
