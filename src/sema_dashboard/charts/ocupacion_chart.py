from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from sema_dashboard.charts.detecciones_chart import _resolve_column


def build_ocupacion_chart(detecciones_df: pd.DataFrame, externo: str | None = None):
    df = detecciones_df.copy()
    if df.empty:
        raise ValueError("No hay datos de ocupacion para construir la grafica.")

    ext_column = _resolve_column(df, "ext", "est", "EXT", "EST")
    acceso_column = _resolve_column(df, "Acceso", "acceso")
    ocupacion_column = _resolve_column(df, "Ocupacion", "ocupacion")
    tiempo_column = _resolve_column(df, "Tiempo", "tiempo")

    if ext_column is None:
        raise KeyError("ext")
    if acceso_column is None:
        raise KeyError("Acceso")
    if ocupacion_column is None:
        raise KeyError("Ocupacion")
    if tiempo_column is None:
        raise KeyError("Tiempo")

    df = df.rename(
        columns={
            ext_column: "ext",
            acceso_column: "Acceso",
            ocupacion_column: "Ocupacion",
            tiempo_column: "Tiempo",
        }
    )

    df["Tiempo"] = pd.to_datetime(df["Tiempo"], errors="coerce")
    df["ext"] = df["ext"].astype(str)
    df["Acceso"] = df["Acceso"].astype(str)
    df["Ocupacion"] = pd.to_numeric(df["Ocupacion"], errors="coerce")
    if externo:
        df = df[df["ext"] == str(externo)].copy()
    df = df.dropna(subset=["Tiempo", "Acceso", "Ocupacion"])
    if df.empty:
        if externo:
            raise ValueError(f"No hay datos para el externo {externo}.")
        raise ValueError("No hay datos de ocupacion para la red en el rango seleccionado.")

    df_plot = df.groupby(["Tiempo", "Acceso"], as_index=False)["Ocupacion"].mean()

    fig = go.Figure()
    for acceso in sorted(df_plot["Acceso"].unique()):
        temp = df_plot[df_plot["Acceso"] == acceso].sort_values("Tiempo")
        ocup_media = temp["Ocupacion"].mean()
        fig.add_trace(
            go.Scatter(
                x=temp["Tiempo"],
                y=temp["Ocupacion"],
                mode="lines",
                name=f"Acceso {acceso}<br>{ocup_media:.1f}%",
                line=dict(width=2),
                hoverlabel=dict(
                    bgcolor="#111111",
                    bordercolor="#333333",
                    font_size=11,
                    font_family="Arial",
                ),
                hovertemplate=(
                    f"<b>ACCESO {acceso}</b><br>"
                    "Ocupación: %{y:.1f}%"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        template="plotly_dark",
        title=dict(text=(f"Ocupación por Acceso - Ext {externo}" if externo else "Ocupación por Acceso - Red SEMA"), x=0.5),
        paper_bgcolor="#111111",
        plot_bgcolor="#111111",
        font=dict(color="white", size=13),
        height=620,
        hovermode="x unified",
        showlegend=True,
        legend=dict(
            title="Accesos",
            x=0.99,
            y=0.99,
            xanchor="right",
            yanchor="top",
            bgcolor="rgba(0,0,0,0.70)",
            bordercolor="#888888",
            borderwidth=1,
            font=dict(size=11, color="white"),
        ),
        margin=dict(l=50, r=50, t=80, b=50),
    )
    fig.update_xaxes(
        title="Tiempo",
        showgrid=True,
        gridcolor="#333333",
        rangeslider_visible=True,
    )
    fig.update_yaxes(
        title="Ocupación (%)",
        showgrid=True,
        gridcolor="#333333",
        rangemode="tozero",
    )

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "doubleClick": "reset",
        "showTips": False,
    }
    return fig, config
