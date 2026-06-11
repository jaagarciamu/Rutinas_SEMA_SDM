from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


def _prepare_realtime_series(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("No hay datos de SEMA en línea para construir la gráfica.")

    data = df.copy()
    required_columns = ["Tiempo", "ext", "Acceso", "Deteccion", "Ocupacion"]
    missing = [column for column in required_columns if column not in data.columns]
    if missing:
        raise KeyError(missing[0])

    for column in ["Deteccion_prom", "Ocupacion_prom", "Sensor"]:
        if column not in data.columns:
            data[column] = None

    data["Tiempo"] = pd.to_datetime(data["Tiempo"], errors="coerce")
    data["ext"] = data["ext"].astype(str).str.strip()
    data["Acceso"] = data["Acceso"].astype(str).str.strip()
    data["Deteccion"] = pd.to_numeric(data["Deteccion"], errors="coerce")
    data["Ocupacion"] = pd.to_numeric(data["Ocupacion"], errors="coerce")
    data["Deteccion_prom"] = pd.to_numeric(data["Deteccion_prom"], errors="coerce")
    data["Ocupacion_prom"] = pd.to_numeric(data["Ocupacion_prom"], errors="coerce")
    data["Sensor"] = data["Sensor"].astype(str).str.strip()
    data = data.dropna(subset=["Tiempo"]).copy()
    if data.empty:
        raise ValueError("No hay datos de SEMA en línea para construir la gráfica.")
    return data


def _base_layout(title: str, border_color: str) -> dict:
    return dict(
        template="plotly_dark",
        title=dict(text=title, x=0.5, y=0.96, font=dict(size=12, color="white")),
        height=200,
        width=500,
        paper_bgcolor="rgba(9,18,30,0.72)",
        plot_bgcolor="rgba(9,18,30,0.72)",
        font=dict(color="white", size=12),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="rgba(9,18,30,0.70)",
            bordercolor="rgba(118,189,255,0.28)",
            font=dict(color="white", size=11),
        ),
        showlegend=False,
        margin=dict(l=10, r=10, t=25, b=15),
        shapes=[
            dict(
                type="rect",
                xref="paper",
                yref="paper",
                x0=0,
                y0=0,
                x1=1,
                y1=1,
                line=dict(color=border_color, width=0.35),
                fillcolor="rgba(0,0,0,0)",
                layer="below",
            )
        ],
    )


def build_sema_en_linea_detecciones_chart(realtime_df: pd.DataFrame, externo: str | None = None):
    data = _prepare_realtime_series(realtime_df)

    df_plot = (
        data.groupby("Tiempo", as_index=False)
        .agg(
            Deteccion_total=("Deteccion", "sum"),
            Deteccion_prom_total=("Deteccion_prom", "sum"),
            Accesos=("Acceso", "nunique"),
            Sensores=("Sensor", "nunique"),
        )
        .sort_values("Tiempo")
    )
    if df_plot.empty:
        raise ValueError("No hay datos de detecciones para construir la gráfica.")

    df_plot["FechaHora"] = df_plot["Tiempo"].dt.strftime("%d/%m/%Y %H:%M:%S")
    total_deteccion = float(df_plot["Deteccion_total"].fillna(0).sum())
    total_promedio = float(df_plot["Deteccion_prom_total"].fillna(0).sum())
    title = f"Detecciones · Externo {externo}" if externo else "Detecciones · Red SEMA en línea"

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df_plot["Tiempo"],
            y=df_plot["Deteccion_total"],
            mode="lines",
            name=f"Detección<br>{total_deteccion:,.0f} veh",
            line=dict(color="#4C9A2A", width=2.3),
            customdata=df_plot[["FechaHora", "Accesos", "Sensores"]],
            hovertemplate=
            "Detección: %{y:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df_plot["Tiempo"],
            y=df_plot["Deteccion_prom_total"],
            mode="lines",
            name=f"Detección promedio<br>{total_promedio:,.0f} veh",
            line=dict(color="#8B1E24", width=3, dash="dot"),
            customdata=df_plot[["FechaHora", "Accesos", "Sensores"]],
            hovertemplate=
            "Detección promedio: %{y:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(**_base_layout(title, "#4C9A2A"))
    fig.update_xaxes(
        title=dict(text="Tiempo",standoff=2),
        title_font=dict(size=10),
        tickfont=dict(size=8),
        showgrid=True,
        gridwidth=0.5,
        gridcolor="#2A3A4E",
        tickformat="%H:%M",
        hoverformat="%d/%m/%Y %H:%M:%S",
        zeroline=False,
        rangeslider_visible=False,
    )
    fig.update_yaxes(
        title=dict(text="Número de detecciones",standoff=2),
        title_font=dict(size=10),
        tickfont=dict(size=8),
        showgrid=True,
        gridwidth=0.5,
        gridcolor="#2A3A4E",
        rangemode="tozero",
        zeroline=False,
    )
    return fig, {
        "scrollZoom": False,
        "displayModeBar": True,
        "doubleClick": "reset",
        "showTips": False,
        "displaylogo": False,
        "modeBarButtonsToRemove": [
            "zoom2d",
            "pan2d",
            "select2d",
            "lasso2d",
            "zoomIn2d",
            "zoomOut2d",
            "autoScale2d",
            "hoverClosestCartesian",
            "hoverCompareCartesian",
            "toggleSpikelines",
            "toImage",
        ],
    }


def build_sema_en_linea_ocupacion_chart(realtime_df: pd.DataFrame, externo: str | None = None):
    data = _prepare_realtime_series(realtime_df)

    df_plot = (
        data.groupby("Tiempo", as_index=False)
        .agg(
            Ocupacion_total=("Ocupacion", "mean"),
            Ocupacion_prom_total=("Ocupacion_prom", "mean"),
            Accesos=("Acceso", "nunique"),
            Sensores=("Sensor", "nunique"),
        )
        .sort_values("Tiempo")
    )
    if df_plot.empty:
        raise ValueError("No hay datos de ocupación para construir la gráfica.")

    df_plot["FechaHora"] = df_plot["Tiempo"].dt.strftime("%d/%m/%Y %H:%M:%S")
    promedio_ocupacion = float(df_plot["Ocupacion_total"].dropna().mean()) if df_plot["Ocupacion_total"].notna().any() else 0.0
    promedio_referencia = float(df_plot["Ocupacion_prom_total"].dropna().mean()) if df_plot["Ocupacion_prom_total"].notna().any() else 0.0
    title = f"Ocupación · Externo {externo}" if externo else "Ocupación · Red SEMA en línea"

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df_plot["Tiempo"],
            y=df_plot["Ocupacion_total"],
            mode="lines",
            name=f"Ocupación<br>",
            line=dict(color="#1F77B4", width=2.3),
            customdata=df_plot[["FechaHora", "Accesos", "Sensores"]],
            hovertemplate=
            "Ocupación: %{y:,.1f}%<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df_plot["Tiempo"],
            y=df_plot["Ocupacion_prom_total"],
            mode="lines",
            name=f"Ocupación promedio<br>",
            line=dict(color="#8B1E24", width=3, dash="dot"),
            customdata=df_plot[["FechaHora", "Accesos", "Sensores"]],
            hovertemplate=
            "Ocupación promedio: %{y:,.1f}%<extra></extra>",
        )
    )
    fig.update_layout(**_base_layout(title, "#1F77B4"))
    fig.update_xaxes(
        title=dict(text="Tiempo",standoff=2),
        title_font=dict(size=10),
        tickfont=dict(size=8),
        showgrid=True,
        gridwidth=0.5,
        gridcolor="#2A3A4E",
        tickformat="%H:%M",
        hoverformat="%d/%m/%Y %H:%M:%S",
        zeroline=False,
        rangeslider_visible=False,
    )
    fig.update_yaxes(
        title=dict(text="Ocupación (%)",standoff=2),
        title_font=dict(size=10),
        tickfont=dict(size=8),
        showgrid=True,
        gridwidth=0.5,
        gridcolor="#2A3A4E",
        rangemode="tozero",
        zeroline=False,
    )
    return fig, {
        "scrollZoom": False,
        "displayModeBar": True,
        "doubleClick": "reset",
        "showTips": False,
        "displaylogo": False,
        "modeBarButtonsToRemove": [
            "zoom2d",
            "pan2d",
            "select2d",
            "lasso2d",
            "zoomIn2d",
            "zoomOut2d",
            "autoScale2d",
            "hoverClosestCartesian",
            "hoverCompareCartesian",
            "toggleSpikelines",
            "toImage",
        ],
    }
