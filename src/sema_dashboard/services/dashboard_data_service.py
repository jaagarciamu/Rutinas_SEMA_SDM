from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from sema_dashboard.repositories.detecciones_repository import fetch_detecciones
from sema_dashboard.repositories.estados_repository import fetch_estados
from sema_dashboard.repositories.planes_repository import fetch_planes
from sema_dashboard.transforms.detecciones import build_detecciones_dataset


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
    return get_detecciones_raw_cached(_filters_signature(filters), externo)


@st.cache_data(ttl=300, show_spinner=False)
def get_detecciones_map_dataset_cached(filters_signature: tuple[tuple[str, Any], ...]) -> pd.DataFrame:
    filters = dict(filters_signature)
    detecciones_raw = get_detecciones_raw_cached(filters_signature, None)
    estados_raw = fetch_estados()
    return build_detecciones_dataset(detecciones_raw, estados_raw, filters)


def get_detecciones_map_dataset(filters: dict[str, Any]) -> pd.DataFrame:
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
    return get_planes_raw_cached(_filters_signature(filters), externo)
