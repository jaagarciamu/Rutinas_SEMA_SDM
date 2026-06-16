from __future__ import annotations

from datetime import date, timedelta

import streamlit as st

from sema_dashboard.config import DEFAULT_FILTERS, GENERAL_FILTER_MAPS

DATE_RANGE_STATE_KEY = "date_ranges"
GENERAL_DATE_RANGE_KEY = "general"
SEMA_EN_LINEA_DATE_RANGE_KEY = "sema_en_linea"
DATE_RANGE_FIELDS = ("fecha_inicio", "fecha_fin")


def _default_date_range() -> dict[str, date]:
    today = date.today()
    return {
        "fecha_inicio": today - timedelta(days=7),
        "fecha_fin": today,
    }


def build_default_date_ranges() -> dict[str, dict[str, date]]:
    default_range = _default_date_range()
    return {
        GENERAL_DATE_RANGE_KEY: dict(default_range),
        SEMA_EN_LINEA_DATE_RANGE_KEY: dict(default_range),
    }


def get_date_range_context(map_key: str | None = None) -> str:
    resolved_map = map_key or st.session_state.get("active_map")
    if resolved_map in GENERAL_FILTER_MAPS:
        return GENERAL_DATE_RANGE_KEY
    return SEMA_EN_LINEA_DATE_RANGE_KEY


def normalize_date_ranges(date_ranges: dict | None) -> dict[str, dict[str, date]]:
    defaults = build_default_date_ranges()
    normalized = {key: dict(value) for key, value in defaults.items()}
    if not isinstance(date_ranges, dict):
        return normalized

    for context_key in [GENERAL_DATE_RANGE_KEY, SEMA_EN_LINEA_DATE_RANGE_KEY]:
        current = date_ranges.get(context_key, {})
        if not isinstance(current, dict):
            continue
        for field in DATE_RANGE_FIELDS:
            value = current.get(field)
            if isinstance(value, date):
                normalized[context_key][field] = value

    return normalized


def get_active_date_range(map_key: str | None = None) -> dict[str, date]:
    date_ranges = normalize_date_ranges(st.session_state.get(DATE_RANGE_STATE_KEY))
    return dict(date_ranges[get_date_range_context(map_key)])


def set_active_date_range(fecha_inicio: date, fecha_fin: date, map_key: str | None = None) -> None:
    date_ranges = normalize_date_ranges(st.session_state.get(DATE_RANGE_STATE_KEY))
    date_ranges[get_date_range_context(map_key)] = {
        "fecha_inicio": fecha_inicio,
        "fecha_fin": fecha_fin,
    }
    st.session_state[DATE_RANGE_STATE_KEY] = date_ranges


def get_active_filters(map_key: str | None = None) -> dict:
    filters = dict(st.session_state.get("filters", DEFAULT_FILTERS.copy()))
    active_date_range = get_active_date_range(map_key)
    return {
        **filters,
        **active_date_range,
    }
