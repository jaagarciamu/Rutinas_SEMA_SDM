from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from sema_dashboard.repositories.detecciones_repository import fetch_detecciones
from sema_dashboard.repositories.estados_repository import fetch_estados
from sema_dashboard.repositories.inventario_repository import fetch_inventario
from sema_dashboard.repositories.planes_repository import fetch_planes
from sema_dashboard.repositories.sema_en_linea_repository import fetch_sema_en_linea
from sema_dashboard.services.filter_service import apply_common_filters
from sema_dashboard.services.filter_state_service import get_active_filters
from sema_dashboard.transforms.detecciones import build_detecciones_dataset
from sema_dashboard.transforms.sema_en_linea import build_sema_en_linea_payload


def _filters_signature(filters: dict[str, Any] | None) -> tuple[tuple[str, Any], ...]:
    if not filters:
        return tuple()
    return tuple(sorted(filters.items(), key=lambda item: item[0]))


@st.cache_data(ttl=300, show_spinner=False)
def get_detecciones_raw_cached(
    filters_signature: tuple[tuple[str, Any], ...],
    externo: str | None = None,
) -> pd.DataFrame:
    filters = dict(filters_signature)
    return fetch_detecciones(
        filters.get("fecha_inicio"),
        filters.get("fecha_fin"),
        externo,
    )


def get_detecciones_raw(filters: dict[str, Any], externo: str | None = None) -> pd.DataFrame:
    if "fecha_inicio" not in filters or "fecha_fin" not in filters:
        filters = get_active_filters()
    detecciones = get_detecciones_raw_cached(_filters_signature(filters), externo)
    if detecciones.empty:
        return detecciones
    return apply_common_filters(detecciones, filters)


@st.cache_data(ttl=300, show_spinner=False)
def get_detecciones_map_dataset_cached(filters_signature: tuple[tuple[str, Any], ...]) -> pd.DataFrame:
    filters = dict(filters_signature)
    detecciones_raw = get_detecciones_raw(filters, None)
    estados_raw = fetch_estados()
    return build_detecciones_dataset(detecciones_raw, estados_raw, filters)


def get_detecciones_map_dataset(filters: dict[str, Any]) -> pd.DataFrame:
    if "fecha_inicio" not in filters or "fecha_fin" not in filters:
        filters = get_active_filters()
    return get_detecciones_map_dataset_cached(_filters_signature(filters))


@st.cache_data(ttl=300, show_spinner=False)
def get_planes_raw_cached(
    filters_signature: tuple[tuple[str, Any], ...],
    externo: str | None = None,
) -> pd.DataFrame:
    filters = dict(filters_signature)
    return fetch_planes(
        filters.get("fecha_inicio"),
        filters.get("fecha_fin"),
        externo,
    )


def get_planes_raw(filters: dict[str, Any], externo: str | None = None) -> pd.DataFrame:
    if "fecha_inicio" not in filters or "fecha_fin" not in filters:
        filters = get_active_filters()
    return get_planes_raw_cached(_filters_signature(filters), externo)


@st.cache_data(ttl=180, show_spinner=False)
def get_sema_en_linea_raw_cached(
    filters_signature: tuple[tuple[str, Any], ...],
    externo: str | None = None,
) -> pd.DataFrame:
    filters = dict(filters_signature)
    return fetch_sema_en_linea(
        filters.get("fecha_inicio"),
        filters.get("fecha_fin"),
        externo,
    )


def get_sema_en_linea_raw(filters: dict[str, Any], externo: str | None = None) -> pd.DataFrame:
    if "fecha_inicio" not in filters or "fecha_fin" not in filters:
        filters = get_active_filters("sema_en_linea")
    return get_sema_en_linea_raw_cached(_filters_signature(filters), externo)


@st.cache_data(ttl=180, show_spinner=False)
def get_sema_en_linea_payload_cached(filters_signature: tuple[tuple[str, Any], ...]) -> dict[str, object]:
    filters = dict(filters_signature)
    realtime_raw = get_sema_en_linea_raw_cached(filters_signature, filters.get("externo") or None)
    estados_raw = fetch_estados()
    inventario_raw = fetch_inventario()
    return build_sema_en_linea_payload(realtime_raw, estados_raw, inventario_raw, filters)


def get_sema_en_linea_payload(filters: dict[str, Any]) -> dict[str, object]:
    if "fecha_inicio" not in filters or "fecha_fin" not in filters:
        filters = get_active_filters("sema_en_linea")
    return get_sema_en_linea_payload_cached(_filters_signature(filters))
