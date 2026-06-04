from __future__ import annotations

import pandas as pd
import pydeck as pdk
import streamlit as st

from sema_dashboard.maps.detecciones_map import build_detecciones_map
from sema_dashboard.maps.estados_map import build_estados_map
from sema_dashboard.maps.inventario_map import build_inventario_map
from sema_dashboard.maps.novedades_map import build_novedades_map
from sema_dashboard.repositories.detecciones_repository import fetch_detecciones
from sema_dashboard.repositories.estados_repository import fetch_estados
from sema_dashboard.repositories.inventario_repository import fetch_inventario
from sema_dashboard.repositories.novedades_repository import fetch_novedades
from sema_dashboard.transforms.detecciones import build_detecciones_dataset
from sema_dashboard.transforms.estados import build_estados_dataset
from sema_dashboard.transforms.inventario import build_inventario_dataset
from sema_dashboard.transforms.novedades import build_novedades_dataset


def render_map_stage() -> None:

    mapa_actual = st.session_state.active_map

    titulos = {
        "inventario": "Mapa Sistema Semaforización Inteligente",
        "detecciones": "Mapa Detecciones SEMA",
        "estados": "Mapa Estados Concert SEMA",
        "novedades": "Mapa Atención Novedades SEMA"
    }

    titulo = titulos.get(
        mapa_actual,
        "Mapa SEMA"
    )

    st.markdown(
        f"""
        <div class="map-title-floating">
            {titulo}
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown(
        '<div class="map-stage-shell">',
        unsafe_allow_html=True
    )

    deck = _build_current_map()

    st.pydeck_chart(
        deck,
        width="stretch",
        height=680
    )

    _render_map_overlays()

    st.markdown(
        "</div>",
        unsafe_allow_html=True
    )


def _build_current_map() -> pdk.Deck:
    try:
        if st.session_state.active_map == "inventario":
            inventario_raw = fetch_inventario()
            inventario = build_inventario_dataset(
                inventario_raw,
                st.session_state.filters,
            )
            if inventario.empty:
                st.info("No hay intersecciones para los filtros actuales.")
                return _build_empty_map()
            return build_inventario_map(inventario, st.session_state.map_view_state)

        if st.session_state.active_map == "detecciones":
            detecciones_raw = fetch_detecciones(
                st.session_state.filters.get("fecha_inicio"),
                st.session_state.filters.get("fecha_fin"),
            )
            estados_raw = fetch_estados()
            detecciones = build_detecciones_dataset(
                detecciones_raw,
                estados_raw,
                st.session_state.filters,
            )
            if detecciones.empty:
                st.info("No hay detecciones para los filtros actuales.")
                return _build_empty_map()
            return build_detecciones_map(detecciones, st.session_state.map_view_state)

        if st.session_state.active_map == "estados":
            estados_raw = fetch_estados()
            estados = build_estados_dataset(estados_raw, st.session_state.filters)
            if estados.empty:
                st.info("No hay estados para los filtros actuales.")
                return _build_empty_map()
            return build_estados_map(estados, st.session_state.map_view_state)

        if st.session_state.active_map == "novedades":
            novedades_raw = fetch_novedades()
            novedades = build_novedades_dataset(novedades_raw, st.session_state.filters)
            if novedades.empty:
                st.info("No hay novedades para los filtros actuales.")
                return _build_empty_map()
            return build_novedades_map(novedades, st.session_state.map_view_state)
    except Exception as exc:
        st.warning(f"No fue posible cargar datos del mapa `{st.session_state.active_map}`: {exc}")
        return _build_empty_map()
    return _build_empty_map()


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


def _render_map_overlays() -> None:
    active_map = st.session_state.active_map
    if active_map == "inventario":
        inventario_raw = fetch_inventario()
        dataset = build_inventario_dataset(inventario_raw, st.session_state.filters)
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
            info_bottom="10px",
            info_align="left",
        )
        return

    if active_map == "detecciones":
        detecciones_raw = fetch_detecciones(
            st.session_state.filters.get("fecha_inicio"),
            st.session_state.filters.get("fecha_fin"),
        )
        estados_raw = fetch_estados()
        dataset = build_detecciones_dataset(detecciones_raw, estados_raw, st.session_state.filters)
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
        fecha_max = pd.to_datetime(dataset.get("Fecha"), errors="coerce").max()
        info_html = (
            "<b>Fecha analisis</b><br>"
            f"{fecha_max.strftime('%Y-%m-%d') if pd.notna(fecha_max) else '-'}"
        )
        _render_overlay_boxes(
            legend_title="Nivel de Ocupacion",
            legend_html=legend_html,
            legend_bottom="10px",
            info_html=info_html,
            info_bottom="118px",
            info_align="center",
        )
        return

    if active_map == "estados":
        estados_raw = fetch_estados()
        dataset = build_estados_dataset(estados_raw, st.session_state.filters)
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
        info_html = (
            "<b>Fecha estado</b><br>"
            f"{fecha_estado.strftime('%Y-%m-%d %H:%M') if pd.notna(fecha_estado) else '-'}"
        )
        _render_overlay_boxes(
            legend_title="Estado",
            legend_html=legend_html,
            legend_bottom="10px",
            info_html=info_html,
            info_bottom="118px",
            info_align="center",
        )
        return

    if active_map == "novedades":
        novedades_raw = fetch_novedades()
        dataset = build_novedades_dataset(novedades_raw, st.session_state.filters)
        if dataset.empty:
            return
        legend_html = _legend_items_html(
            [
                ("EN SERVICIO", "#00FF66"),
                ("AISLADA", "#29B6F6"),
                ("APAGADA", "#C44E7A"),
                ("INTERMITENTE", "#9BBB59"),
                ("MANTENIMIENTO", "#FF8C00"),
            ]
        )
        incidentes = dataset[
            dataset["id_de_solicitud"].notna() & (dataset["id_de_solicitud"].astype(str).str.strip() != "")
        ].copy()
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
        for est in ["EN SERVICIO", "AISLADA", "INTERMITENTE", "APAGADA", "MANTENIMIENTO"]:
            if est in estado_count:
                resumen += f"{est}: {estado_count[est]}<br>"
        _render_overlay_boxes(
            legend_title="Estado Interseccion",
            legend_html=legend_html,
            legend_bottom="210px",
            info_html=resumen,
            info_bottom="10px",
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
