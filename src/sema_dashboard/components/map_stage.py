from __future__ import annotations

import html
import math

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
from sema_dashboard.ui.interactions import update_filter


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

    deck, selection_dataset, external_column = _build_current_map()

    event = st.pydeck_chart(
        deck,
        width="stretch",
        height=680,
        on_select="rerun",
        selection_mode="single-object",
        key=f"sema-map-{mapa_actual}",
    )

    _sync_map_selection(event, selection_dataset, external_column)

    _render_map_overlays()

    if mapa_actual == "novedades":
        _render_novedades_bottom_panels()

    st.markdown(
        "</div>",
        unsafe_allow_html=True
    )


def _build_current_map() -> tuple[pdk.Deck, pd.DataFrame, str | None]:
    try:
        if st.session_state.active_map == "inventario":
            inventario_raw = fetch_inventario()
            inventario = build_inventario_dataset(
                inventario_raw,
                st.session_state.filters,
            )
            if inventario.empty:
                st.info("No hay intersecciones para los filtros actuales.")
                return _build_empty_map(), pd.DataFrame(), None
            return build_inventario_map(inventario, st.session_state.map_view_state), inventario.reset_index(drop=True), "externo"

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
                return _build_empty_map(), pd.DataFrame(), None
            return build_detecciones_map(detecciones, st.session_state.map_view_state), detecciones.reset_index(drop=True), "ext"

        if st.session_state.active_map == "estados":
            estados_raw = fetch_estados()
            estados = build_estados_dataset(estados_raw, st.session_state.filters)
            if estados.empty:
                st.info("No hay estados para los filtros actuales.")
                return _build_empty_map(), pd.DataFrame(), None
            return build_estados_map(estados, st.session_state.map_view_state), estados.reset_index(drop=True), "externo"

        if st.session_state.active_map == "novedades":
            novedades_raw = fetch_novedades()
            novedades = build_novedades_dataset(novedades_raw, st.session_state.filters)
            if novedades.empty:
                st.info("No hay novedades para los filtros actuales.")
                return _build_empty_map(), pd.DataFrame(), None
            return build_novedades_map(novedades, st.session_state.map_view_state), novedades.reset_index(drop=True), "externo"
    except Exception as exc:
        st.warning(f"No fue posible cargar datos del mapa `{st.session_state.active_map}`: {exc}")
        return _build_empty_map(), pd.DataFrame(), None
    return _build_empty_map(), pd.DataFrame(), None


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

    current = st.session_state.filters.get("externo", "")
    if externo and current != externo:
        st.session_state.selected_externo = externo
        st.session_state.externo_sheet_open = st.session_state.active_map == "inventario"
        update_filter("externo", externo)
        st.rerun()


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
            info_bottom="34px",
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
            legend_bottom="238px",
            info_html=resumen,
            info_bottom="34px",
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

    incidentes = dataset[dataset["tiempo_transcurrido"].notna()].copy()
    if incidentes.empty:
        return

    incidentes["horas_atencion"] = (
        pd.to_timedelta(incidentes["tiempo_transcurrido"], errors="coerce")
        .dt.total_seconds()
        / 3600
    )
    incidentes = incidentes[incidentes["horas_atencion"].notna()].copy()
    if incidentes.empty:
        return

    donut_svg = _build_novedades_donut_svg(incidentes)
    barras_svg = _build_novedades_barras_svg(incidentes)

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
                {donut_svg}
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
                {barras_svg}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _build_novedades_donut_svg(incidentes: pd.DataFrame) -> str:
    donut_df = incidentes.copy()
    donut_df["grupo_tiempo"] = pd.cut(
        donut_df["horas_atencion"],
        bins=[0, 1, 3, 5, float("inf")],
        labels=["<1 hora", "1-3 horas", "3-5 horas", ">5 horas"],
        include_lowest=True,
    )
    resumen = (
        donut_df["grupo_tiempo"]
        .value_counts()
        .reindex(["<1 hora", "1-3 horas", "3-5 horas", ">5 horas"], fill_value=0)
    )
    color_tiempo = {
        "<1 hora": "#00FF66",
        "1-3 horas": "#FFD700",
        "3-5 horas": "#FF8C00",
        ">5 horas": "#FF3333",
    }
    total = int(resumen.sum())
    if total <= 0:
        return "<div style='font-size:12px;color:white;'>Sin datos</div>"

    cx, cy = 72, 80
    radius = 42
    stroke = 16
    circumference = 2 * math.pi * radius
    offset = 0.0
    segments = []
    for label, value in resumen.items():
        if int(value) <= 0:
            continue
        fraction = float(value) / total
        seg_length = circumference * fraction
        segments.append(
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
            f'stroke="{color_tiempo[label]}" stroke-width="{stroke}" '
            f'stroke-dasharray="{seg_length:.2f} {circumference - seg_length:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx} {cy})" />'
        )
        offset += seg_length

    legend_rows = []
    legend_y = 38
    for label, value in resumen.items():
        if int(value) <= 0:
            continue
        legend_rows.append(
            f'<circle cx="146" cy="{legend_y}" r="4" fill="{color_tiempo[label]}" />'
            f'<text x="156" y="{legend_y + 3}" fill="white" font-size="9">{html.escape(str(label))}</text>'
            f'<text x="204" y="{legend_y + 3}" fill="#d8d8d8" font-size="9" text-anchor="end">{int(value)}</text>'
        )
        legend_y += 18

    return (
        '<svg width="100%" height="100%" viewBox="0 0 220 176" xmlns="http://www.w3.org/2000/svg">'
        '<text x="110" y="16" fill="white" font-size="12" font-weight="600" text-anchor="middle">Tiempo de Atención</text>'
        f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="{stroke}" />'
        + "".join(segments)
        + f'<circle cx="{cx}" cy="{cy}" r="24" fill="#111111" />'
        + f'<text x="{cx}" y="{cy - 2}" fill="white" font-size="14" font-weight="600" text-anchor="middle">{total}</text>'
        + f'<text x="{cx}" y="{cy + 14}" fill="white" font-size="10" text-anchor="middle">casos</text>'
        + "".join(legend_rows)
        + "</svg>"
    )


def _build_novedades_barras_svg(incidentes: pd.DataFrame) -> str:
    causas_df = (
        incidentes["causa"]
        .fillna("Sin causa")
        .astype(str)
        .str.strip()
        .replace("", "Sin causa")
        .value_counts()
        .head(5)
        .reset_index()
    )
    causas_df.columns = ["causa", "cantidad"]
    if causas_df.empty:
        return "<div style='font-size:12px;color:white;'>Sin datos</div>"

    causas_df = causas_df.sort_values("cantidad", ascending=True).reset_index(drop=True)
    max_value = max(int(causas_df["cantidad"].max()), 1)
    rows = []
    base_y = 120
    step = 34
    bar_left = 10
    bar_max = 184
    palette = ["#29B6F6", "#00B8FF", "#7CFC00", "#FFD700", "#FF8C00"]
    for idx, row in causas_df.iterrows():
        y = base_y - idx * step
        label = str(row["causa"])
        if len(label) > 28:
            label = label[:28] + "…"
        width = max(18, int((int(row["cantidad"]) / max_value) * bar_max))
        color = palette[idx % len(palette)]
        rows.append(
            f'<text x="10" y="{y}" fill="white" font-size="8.5">{html.escape(label)}</text>'
            f'<rect x="{bar_left}" y="{y + 6}" width="{bar_max}" height="12" rx="6" fill="rgba(255,255,255,0.08)" />'
            f'<rect x="{bar_left}" y="{y + 6}" width="{width}" height="12" rx="6" fill="{color}" />'
            f'<text x="{bar_left + width - 4}" y="{y + 15}" fill="white" font-size="8.5" text-anchor="end">{int(row["cantidad"])}</text>'
        )

    return (
        '<svg width="100%" height="100%" viewBox="0 0 220 176" xmlns="http://www.w3.org/2000/svg">'
        '<text x="110" y="16" fill="white" font-size="12" font-weight="600" text-anchor="middle">Causas de Incidentes</text>'
        + "".join(rows)
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
