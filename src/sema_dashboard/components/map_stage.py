from __future__ import annotations

import html
import math

import pandas as pd
import pydeck as pdk
import streamlit as st

from sema_dashboard.config import SEMA_EN_LINEA_SELECTED_VIEW_STATE, SEMA_EN_LINEA_VIEW_STATE
from sema_dashboard.charts.sema_en_linea_charts import (
    build_sema_en_linea_detecciones_chart,
    build_sema_en_linea_ocupacion_chart,
)
from sema_dashboard.maps.detecciones_map import build_detecciones_map
from sema_dashboard.maps.estados_map import build_estados_map
from sema_dashboard.maps.inventario_map import build_inventario_map
from sema_dashboard.maps.novedades_map import build_novedades_map
from sema_dashboard.maps.sema_en_linea_map import build_sema_en_linea_map
from sema_dashboard.repositories.estados_repository import fetch_estados
from sema_dashboard.repositories.inventario_repository import fetch_inventario
from sema_dashboard.repositories.novedades_repository import fetch_novedades
from sema_dashboard.services.dashboard_data_service import get_detecciones_map_dataset, get_sema_en_linea_payload
from sema_dashboard.transforms.estados import build_estados_dataset
from sema_dashboard.transforms.inventario import build_inventario_dataset
from sema_dashboard.transforms.novedades import build_novedades_dataset
from sema_dashboard.ui.interactions import update_filter


def render_map_stage() -> None:
    mapa_actual = st.session_state.active_map
    filters = dict(st.session_state.filters)

    titulos = {
        "inventario": "Mapa Sistema Semaforización Inteligente",
        "detecciones": "Mapa Detecciones SEMA",
        "estados": "Mapa Estados Concert SEMA",
        "novedades": "Mapa Atención Novedades SEMA",
        "sema_en_linea": "SEMA en linea",
    }

    titulo = titulos.get(
        mapa_actual,
        "Mapa SEMA"
    )

    deck, selection_dataset, external_column, map_context = _build_current_map(filters)
    st.markdown(
        f"""
        <div class="map-title-floating">
            {titulo}
        </div>
        """,
        unsafe_allow_html=True
    )

    if mapa_actual == "sema_en_linea":
        st.markdown(
            _build_title_badge(map_context),
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div class="map-stage-shell">',
        unsafe_allow_html=True
    )

    event = st.pydeck_chart(
        deck,
        width="stretch",
        height=680,
        on_select="rerun",
        selection_mode="single-object",
        key=f"sema-map-{mapa_actual}",
    )

    _sync_map_selection(event, selection_dataset, external_column)

    _render_map_overlays(filters)

    if mapa_actual == "novedades":
        _render_novedades_bottom_panels()
    if mapa_actual == "sema_en_linea":
        _render_sema_en_linea_bottom_charts(map_context)

    st.markdown(
        "</div>",
        unsafe_allow_html=True
    )


def _build_current_map(filters: dict) -> tuple[pdk.Deck, pd.DataFrame, str | None, dict[str, object]]:
    try:
        if st.session_state.active_map == "inventario":
            inventario_raw = fetch_inventario()
            inventario = build_inventario_dataset(
                inventario_raw,
                filters,
            )
            if inventario.empty:
                st.info("No hay intersecciones para los filtros actuales.")
                return _build_empty_map(), pd.DataFrame(), None, {}
            return build_inventario_map(inventario, st.session_state.map_view_state), inventario.reset_index(drop=True), "externo", {}

        if st.session_state.active_map == "detecciones":
            detecciones = get_detecciones_map_dataset(filters)
            if detecciones.empty:
                st.info("No hay detecciones para los filtros actuales.")
                return _build_empty_map(), pd.DataFrame(), None, {}
            return build_detecciones_map(detecciones, st.session_state.map_view_state), detecciones.reset_index(drop=True), "ext", {}

        if st.session_state.active_map == "estados":
            estados_raw = fetch_estados()
            estados = build_estados_dataset(estados_raw, filters)
            if estados.empty:
                st.info("No hay estados para los filtros actuales.")
                return _build_empty_map(), pd.DataFrame(), None, {}
            return build_estados_map(estados, st.session_state.map_view_state), estados.reset_index(drop=True), "externo", {}

        if st.session_state.active_map == "novedades":
            novedades_raw = fetch_novedades()
            novedades = build_novedades_dataset(novedades_raw, filters)
            if novedades.empty:
                st.info("No hay novedades para los filtros actuales.")
                return _build_empty_map(), pd.DataFrame(), None, {}
            return build_novedades_map(novedades, st.session_state.map_view_state), novedades.reset_index(drop=True), "externo", {}

        if st.session_state.active_map == "sema_en_linea":
            payload = get_sema_en_linea_payload(filters)
            dataset = payload.get("map_dataset", pd.DataFrame())
            if dataset.empty:
                st.info("Este no tiene info para SEMA en linea con los filtros actuales.")
                return _build_empty_map(), pd.DataFrame(), None, payload
            view_state = _resolve_sema_en_linea_view_state(dataset, filters)
            return (
                build_sema_en_linea_map(dataset, view_state),
                dataset.reset_index(drop=True),
                "ext",
                payload,
            )
    except Exception as exc:
        st.warning(f"No fue posible cargar datos del mapa `{st.session_state.active_map}`: {exc}")
        return _build_empty_map(), pd.DataFrame(), None, {}
    return _build_empty_map(), pd.DataFrame(), None, {}


def _build_empty_map() -> pdk.Deck:
    view_state = pdk.ViewState(
        **st.session_state.map_view_state,
        pitch=10,
        bearing=100,
    )
    return pdk.Deck(
        map_style=pdk.map_styles.CARTO_DARK,
        initial_view_state=view_state,
        layers=[],
        tooltip={"text": "Mapa base listo para integrar capas SEMA"},
    )


def _sync_map_selection(event: object, dataset: pd.DataFrame, external_column: str | None) -> None:
    if external_column is None or dataset.empty or external_column not in dataset.columns or event is None:
        return

    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")
    if not selection:
        return

    objects = None
    if isinstance(selection, dict):
        objects = selection.get("objects")
        if objects is None and "indices" in selection:
            indices = selection.get("indices")
            if isinstance(indices, dict):
                for layer_indices in indices.values():
                    if layer_indices:
                        objects = [{"index": layer_indices[0]}]
                        break
    elif hasattr(selection, "get"):
        objects = selection.get("objects")

    if not objects:
        return

    first = None
    if isinstance(objects, list):
        if not objects:
            return
        first = objects[0]
    elif isinstance(objects, dict):
        if not objects:
            return
        first_value = next(iter(objects.values()))
        if isinstance(first_value, list):
            if not first_value:
                return
            first = first_value[0]
        elif isinstance(first_value, dict):
            first = first_value
    if first is None:
        return

    index = first.get("index") if isinstance(first, dict) else None
    if index is None:
        return

    try:
        externo = str(dataset.iloc[int(index)][external_column])
    except Exception:
        return

    if st.session_state.active_map == "sema_en_linea":
        try:
            selected_row = dataset.iloc[int(index)]
            latitude = float(selected_row["latitud"])
            longitude = float(selected_row["longitud"])
            st.session_state.sema_en_linea_view_state = {
                **SEMA_EN_LINEA_VIEW_STATE,
                "latitude": latitude,
                "longitude": longitude,
                "zoom": float(SEMA_EN_LINEA_SELECTED_VIEW_STATE.get("zoom", 13.6)),
            }
        except Exception:
            st.session_state.sema_en_linea_view_state = SEMA_EN_LINEA_VIEW_STATE.copy()

    current = st.session_state.filters.get("externo", "")
    if externo:
        st.session_state.selected_externo = externo
        if current != externo:
            update_filter("externo", externo)
        st.rerun()


def _resolve_sema_en_linea_view_state(dataset: pd.DataFrame, filters: dict) -> dict[str, float]:
    externo = str(filters.get("externo", "")).strip()
    if externo and not dataset.empty and "ext" in dataset.columns:
        selected = dataset[dataset["ext"].astype(str).str.strip() == externo].copy()
        if not selected.empty:
            row = selected.iloc[0]
            try:
                return {
                    **SEMA_EN_LINEA_VIEW_STATE,
                    "latitude": float(row["latitud"]),
                    "longitude": float(row["longitud"]),
                    "zoom": float(SEMA_EN_LINEA_SELECTED_VIEW_STATE.get("zoom", 13.6)),
                }
            except Exception:
                pass
    return {
        **SEMA_EN_LINEA_VIEW_STATE,
        **st.session_state.get("sema_en_linea_view_state", {}),
    }


def _build_title_badge(map_context: dict[str, object]) -> str:
    if st.session_state.active_map != "sema_en_linea":
        return ""

    summary = map_context.get("summary") if isinstance(map_context, dict) else None
    updated_at = summary.get("updated_at") if isinstance(summary, dict) else None
    label = "Actualización"
    value = "-"
    if pd.notna(updated_at):
        timestamp = pd.to_datetime(updated_at, errors="coerce")
        if pd.notna(timestamp):
            value = timestamp.strftime("%d/%m/%Y %H:%M")
    return f"""
    <div style="position:fixed;right:210px;top:168px;z-index:899;pointer-events:none;">
        <div style="
            position:relative;
            min-width:255px;
            padding:10px 16px;
            border-radius:10px;
            border:1px solid rgba(255,255,255,0.25);
            background:rgba(10,16,24,0.82);
            font-size:16px;
            line-height:1.45;
            color:white;
            text-align:center;
            box-shadow:0 6px 20px rgba(0,0,0,0.35);
        ">
            <div style="font-weight:600;">{label}</div>
            <div>{value}</div>
        </div>
    </div>
    """


def _render_sema_en_linea_bottom_charts(map_context: dict[str, object]) -> None:
    series_dataset = map_context.get("series_dataset", pd.DataFrame()) if isinstance(map_context, dict) else pd.DataFrame()
    if not isinstance(series_dataset, pd.DataFrame) or series_dataset.empty:
        return

    externo = st.session_state.filters.get("externo", "") or None
    st.markdown(
        """
        <style>
        .st-key-sema_inline_charts_root {
            position: relative;
            height: 0;
            z-index: 42;
            pointer-events: none;
        }
        .st-key-sema_inline_chart_detecciones,
        .st-key-sema_inline_chart_ocupacion {
            position: absolute;
            left: 12px;
            width: 500px;
            height: 200px;
            background: rgba(9,18,30,0.72);
            border: 1px solid rgba(118,189,255,0.18);
            border-radius: 14px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.34);
            padding: 6px 10px 2px 10px;
            pointer-events: auto;
            overflow: hidden;
        }
        .st-key-sema_inline_chart_detecciones {
            bottom: 258px;
        }
        .st-key-sema_inline_chart_ocupacion {
            bottom: 50px;
        }
        .st-key-sema_inline_chart_detecciones [data-testid="stPlotlyChart"],
        .st-key-sema_inline_chart_ocupacion [data-testid="stPlotlyChart"] {
            height: 100%;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.container(key="sema_inline_charts_root"):
        with st.container(key="sema_inline_chart_detecciones"):
            try:
                fig, config = build_sema_en_linea_detecciones_chart(series_dataset, externo)
                st.plotly_chart(fig, width="stretch", theme=None, config=config)
            except Exception as exc:
                st.info(f"No fue posible construir la gráfica fija de detecciones: {exc}")

        with st.container(key="sema_inline_chart_ocupacion"):
            try:
                fig, config = build_sema_en_linea_ocupacion_chart(series_dataset, externo)
                st.plotly_chart(fig, width="stretch", theme=None, config=config)
            except Exception as exc:
                st.info(f"No fue posible construir la gráfica fija de ocupación: {exc}")


def _render_map_overlays(filters: dict) -> None:
    active_map = st.session_state.active_map
    if active_map == "inventario":
        inventario_raw = fetch_inventario()
        dataset = build_inventario_dataset(inventario_raw, filters)
        if dataset.empty:
            return
        total_inter = len(dataset)
        total_zonas = dataset["ZONA AUTO"].nunique()
        total_wide = pd.to_numeric(dataset.get("Wide"), errors="coerce").fillna(0).sum()
        total_narrow = pd.to_numeric(dataset.get("Narrow"), errors="coerce").fillna(0).sum()
        fecha_inv = pd.to_datetime(dataset.get("FECHA DE INSTALACION"), errors="coerce").max()
        summary_html = (
            "<b>Inventario Red</b><br><br>"
            f"{total_inter:,} intersecciones<br><br>"
            f"{total_zonas:,} zonas auto<br><br>"
            f"Wide : {int(total_wide):,}<br><br>"
            f"Narrow : {int(total_narrow):,}<br><br>"
            "<b>Ultima instalacion</b><br>"
            f"{fecha_inv.strftime('%Y-%m-%d') if pd.notna(fecha_inv) else '-'}"
        )
        _render_info_box(
            info_html=summary_html,
            info_bottom="34px",
            info_align="left",
        )
        return

    if active_map == "detecciones":
        dataset = get_detecciones_map_dataset(filters)
        if dataset.empty:
            return
        legend_html = _legend_items_html(
            [
                ("Sin datos", "#808080"),
                ("Fluido", "#00FF66"),
                ("Saturado", "#FFD700"),
                ("Congestionado", "#FF3333"),
            ]
        )
        fecha_inicio = pd.to_datetime(dataset.get("FechaInicio"), errors="coerce").min()
        fecha_fin = pd.to_datetime(dataset.get("FechaFin"), errors="coerce").max()
        info_html = (
            "<b>Periodo analisis</b><br>"
            f"{fecha_inicio.strftime('%Y-%m-%d') if pd.notna(fecha_inicio) else '-'}"
            " a "
            f"{fecha_fin.strftime('%Y-%m-%d') if pd.notna(fecha_fin) else '-'}"
        )
        _render_overlay_boxes(
            legend_title="Nivel de Ocupacion",
            legend_html=legend_html,
            legend_bottom="34px",
            info_html=info_html,
            info_bottom="146px",
            info_align="center",
        )
        return

    if active_map == "estados":
        estados_raw = fetch_estados()
        dataset = build_estados_dataset(estados_raw, filters)
        if dataset.empty:
            return
        legend_html = _legend_items_html(
            [
                ("Operando", "#18FF1C"),
                ("Operacion sin conexion", "#12B8FF"),
                ("En falla", "#6D2C1E"),
                ("Equipo en Error", "#17D7C0"),
            ]
        )
        fecha_estado = pd.to_datetime(dataset.get("fecha"), errors="coerce").max()
        estado_count = dataset.groupby("estado")["externo"].nunique()
        total = int(dataset["externo"].nunique())
        info_html = (
            "<b>Estados Concert</b><br><br>"
            f"Total externos : {total}<br><br>"
            f"Operando : {int(estado_count.get('Operando', 0))}<br>"
            f"Operacion sin conexion : {int(estado_count.get('Operacion sin conexion', 0))}<br>"
            f"En falla : {int(estado_count.get('En falla', 0))}<br>"
            f"Equipo en Error : {int(estado_count.get('Equipo en Error', 0))}<br><br>"
            "<b>Fecha estado</b><br>"
            f"{fecha_estado.strftime('%Y-%m-%d %H:%M') if pd.notna(fecha_estado) else '-'}"
        )
        _render_overlay_boxes(
            legend_title="Estado",
            legend_html=legend_html,
            legend_bottom="34px",
            info_html=info_html,
            info_bottom="176px",
            info_align="left",
        )
        return

    if active_map == "novedades":
        novedades_raw = fetch_novedades()
        dataset = build_novedades_dataset(novedades_raw, filters)
        if dataset.empty:
            return
        legend_html = _legend_items_html(
            [
                ("EN SERVICIO", "#00FF66"),
                ("AISLADA", "#29B6F6"),
                ("APAGADA", "#C44E7A"),
                ("INTERMITENTE", "#9BBB59"),
                ("MANTENIMIENTO", "#FF8C00"),
                ("EN PMT", "#0D3B66"),
            ]
        )
        incidentes = dataset[
            dataset["id_de_solicitud"].notna() & (dataset["id_de_solicitud"].astype(str).str.strip() != "")
        ].copy()
        incidentes = incidentes[incidentes["estado_de_la_interseccion"] != "EN PMT"].copy()
        if len(incidentes) > 0:
            horas = pd.to_timedelta(incidentes["tiempo_transcurrido"], errors="coerce").dt.total_seconds() / 3600
            promedio_atencion = horas.mean()
        else:
            promedio_atencion = 0.0
        estado_count = dataset["estado_de_la_interseccion"].value_counts()
        resumen = (
            "<b>Seguimiento Atencion</b><br><br>"
            f"Incidentes activos : {len(incidentes)}<br><br>"
            f"Promedio atencion : {promedio_atencion:.1f} h<br><br>"
        )
        for est in ["EN SERVICIO", "AISLADA", "INTERMITENTE", "APAGADA", "MANTENIMIENTO", "EN PMT"]:
            if est in estado_count:
                resumen += f"{est}: {estado_count[est]}<br>"
        _render_overlay_boxes(
            legend_title="Estado Interseccion",
            legend_html=legend_html,
            legend_bottom="250px",
            info_html=resumen,
            info_bottom="34px",
            info_align="left",
        )
        return

    if active_map == "sema_en_linea":
        payload = get_sema_en_linea_payload(filters)
        dataset = payload.get("map_dataset", pd.DataFrame())
        summary = payload.get("summary", {})
        if dataset.empty:
            return
        legend_html = _legend_items_html(
            [
                ("Fluido", "#00FF66"),
                ("Moderado", "#FFD700"),
                ("Saturado", "#FF3333"),
            ]
        )
        updated_at = summary.get("updated_at")
        info_html = (
            "<b>SEMA en linea</b><br><br>"
            f"Externos activos : {int(summary.get('externos_activos', 0))}<br><br>"
            f"Total detecciones dia : {float(summary.get('detecciones_dia', 0.0)):,.0f}<br>"
            f"Detecciones ultimo periodo : {float(summary.get('detecciones_ultimo_periodo', 0.0)):,.0f}<br><br>"
            f"Prom. ocupacion dia : {float(summary.get('ocupacion_promedio_dia', 0.0)):.1f}%<br>"
            f"Prom. ocupacion ultimo periodo : {float(summary.get('ocupacion_promedio_ultimo_periodo', 0.0)):.1f}%<br><br>"
            "<b>Ultima actualizacion</b><br>"
            f"{pd.to_datetime(updated_at).strftime('%Y-%m-%d %H:%M') if pd.notna(updated_at) else '-'}"
        )
        _render_overlay_boxes(
            legend_title="Congestion",
            legend_html=legend_html,
            legend_bottom="34px",
            info_html=info_html,
            info_bottom="132px",
            info_align="left",
        )


def _legend_items_html(items: list[tuple[str, str]]) -> str:
    return "".join(
        f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0;">'
        f'<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:{color};"></span>'
        f'<span>{label}</span></div>'
        for label, color in items
    )


def _render_overlay_boxes(
    legend_title: str,
    legend_html: str,
    legend_bottom: str,
    info_html: str | None,
    info_bottom: str | None,
    info_align: str,
) -> None:
    info_block = ""
    if info_html and info_bottom:
        info_block = f"""
        <div style="
            position:absolute;
            right:10px;
            bottom:{info_bottom};
            min-width:180px;
            max-width:260px;
            background:rgba(0,0,0,0.70);
            color:white;
            border:1px solid #888888;
            border-radius:8px;
            padding:8px 10px;
            font-size:12px;
            line-height:1.45;
            text-align:{info_align};
            box-shadow:0 6px 20px rgba(0,0,0,0.35);
        ">{info_html}</div>
        """
    st.markdown(
        f"""
        <div style="position:relative;height:0;z-index:30;pointer-events:none;">
            <div style="
                position:absolute;
                right:10px;
                bottom:{legend_bottom};
                min-width:170px;
                background:rgba(0,0,0,0.70);
                color:white;
                border:1px solid #888888;
                border-radius:8px;
                padding:8px 10px;
                font-size:11px;
                line-height:1.35;
                box-shadow:0 6px 20px rgba(0,0,0,0.35);
            ">
                <div style="font-weight:600;margin-bottom:6px;">{legend_title}</div>
                {legend_html}
            </div>
            {info_block}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_novedades_bottom_panels() -> None:
    novedades_raw = fetch_novedades()
    dataset = build_novedades_dataset(novedades_raw, st.session_state.filters)
    if dataset.empty:
        return

    novedades_visibles = dataset[dataset["estado_de_la_interseccion"] != "EN SERVICIO"].copy()
    intermitentes_svg = _build_novedades_semicircle_intermitente_svg(novedades_visibles)

    incidentes = dataset[dataset["tiempo_transcurrido"].notna()].copy()
    incidentes = incidentes[incidentes["estado_de_la_interseccion"] != "EN PMT"].copy()
    incidentes["horas_atencion"] = (
        pd.to_timedelta(incidentes["tiempo_transcurrido"], errors="coerce")
        .dt.total_seconds()
        / 3600
    )
    incidentes = incidentes[incidentes["horas_atencion"].notna()].copy()
    duracion_svg = _build_novedades_semicircle_duracion_svg(incidentes)

    st.markdown(
        f"""
        <div style="position:relative;height:0;z-index:42;pointer-events:none;">
            <div style="
                position:absolute;
                left:14px;
                bottom:224px;
                width:220px;
                height:176px;
                background:rgba(0,0,0,0.72);
                color:white;
                border:1px solid #888888;
                border-radius:10px;
                padding:8px;
                box-shadow:0 6px 20px rgba(0,0,0,0.35);
                overflow:hidden;
            ">
                {intermitentes_svg}
            </div>
            <div style="
                position:absolute;
                left:14px;
                bottom:34px;
                width:220px;
                height:176px;
                background:rgba(0,0,0,0.72);
                color:white;
                border:1px solid #888888;
                border-radius:10px;
                padding:8px;
                box-shadow:0 6px 20px rgba(0,0,0,0.35);
                overflow:hidden;
            ">
                {duracion_svg}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _build_novedades_semicircle_intermitente_svg(novedades: pd.DataFrame) -> str:
    total = int(len(novedades))
    apagadas_intermitentes = int(
        novedades["estado_de_la_interseccion"].isin(["APAGADA", "INTERMITENTE"]).sum()
    )
    return _build_semicircle_svg(
        title_lines=["Intersecciones apagadas e", "Intermitentes"],
        value=apagadas_intermitentes,
        max_value=total,
        active_color="#FF5A5F",
        center_text=f"{apagadas_intermitentes}",
        footer_text=None,
        value_format="int",
    )


def _build_novedades_semicircle_duracion_svg(incidentes: pd.DataFrame) -> str:
    if incidentes.empty:
        promedio_horas = 0.0
        horas_totales = 0.0
    else:
        promedio_horas = float(incidentes["horas_atencion"].mean())
        horas_totales = float(incidentes["horas_atencion"].sum())

    return _build_semicircle_svg(
        title_lines=["Duracion de las", "Fallas (Horas)"],
        value=promedio_horas,
        max_value=24.0,
        active_color="#FFF200",
        center_text=f"{promedio_horas:.1f}",
        footer_text=f"Total: {horas_totales:.1f} h",
        value_format="float",
    )


def _build_semicircle_svg(
    title_lines: list[str],
    value: float,
    max_value: float,
    active_color: str,
    center_text: str,
    footer_text: str | None,
    value_format: str,
) -> str:
    max_value = max(float(max_value), 1.0)
    progress = min(max(float(value), 0.0), max_value) / max_value

    cx = 110
    cy = 118
    radius = 60
    stroke = 18
    circumference = math.pi * radius
    active_length = circumference * progress
    remainder_length = max(circumference - active_length, 0.0)
    title_y = 18
    title_svg = "".join(
        f'<text x="110" y="{title_y + index * 20}" fill="white" font-size="14" font-weight="600" text-anchor="middle">{html.escape(line)}</text>'
        for index, line in enumerate(title_lines)
    )
    footer_svg = ""
    if footer_text:
        footer_svg = (
            f'<text x="110" y="166" fill="#d8d8d8" font-size="15.75" text-anchor="middle">{html.escape(footer_text)}</text>'
        )

    left_label = "0"
    if value_format == "int":
        right_label = str(int(max_value))
    else:
        right_label = str(int(max_value)) if float(max_value).is_integer() else f"{max_value:.1f}"

    return (
        '<svg width="100%" height="100%" viewBox="0 0 220 176" xmlns="http://www.w3.org/2000/svg">'
        + title_svg
        + f'<path d="M {cx - radius} {cy} A {radius} {radius} 0 0 1 {cx + radius} {cy}" fill="none" stroke="#D9D9D9" stroke-width="{stroke}" stroke-linecap="butt" />'
        + (
            f'<path d="M {cx - radius} {cy} A {radius} {radius} 0 0 1 {cx + radius} {cy}" '
            f'fill="none" stroke="{active_color}" stroke-width="{stroke}" stroke-linecap="butt" '
            f'stroke-dasharray="{active_length:.2f} {remainder_length:.2f}" />'
            if active_length > 0
            else ""
        )
        + f'<text x="{cx}" y="{cy + 18}" fill="white" font-size="27" font-weight="500" text-anchor="middle">{html.escape(center_text)}</text>'
        + f'<text x="{cx - radius - 6}" y="{cy + 20}" fill="white" font-size="12" text-anchor="middle">{html.escape(left_label)}</text>'
        + f'<text x="{cx + radius + 6}" y="{cy + 20}" fill="white" font-size="12" text-anchor="middle">{html.escape(right_label)}</text>'
        + footer_svg
        + "</svg>"
    )


def _render_info_box(
    info_html: str,
    info_bottom: str,
    info_align: str,
) -> None:
    st.markdown(
        f"""
        <div style="position:relative;height:0;z-index:30;pointer-events:none;">
            <div style="
                position:absolute;
                right:10px;
                bottom:{info_bottom};
                min-width:180px;
                max-width:260px;
                background:rgba(0,0,0,0.70);
                color:white;
                border:1px solid #888888;
                border-radius:8px;
                padding:8px 10px;
                font-size:12px;
                line-height:1.45;
                text-align:{info_align};
                box-shadow:0 6px 20px rgba(0,0,0,0.35);
            ">{info_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
