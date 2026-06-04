from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from sema_dashboard.charts.detecciones_chart import _resolve_column


def build_scatter_chart(detecciones_df: pd.DataFrame, externo: str | None = None):
    df = detecciones_df.copy()
    if df.empty:
        raise ValueError("No hay datos de detecciones para construir el scatter.")

    ext_column = _resolve_column(df, "ext", "est", "EXT", "EST")
    acceso_column = _resolve_column(df, "Acceso", "acceso")
    deteccion_column = _resolve_column(df, "Deteccion", "deteccion")
    ocupacion_column = _resolve_column(df, "Ocupacion", "ocupacion")
    tiempo_column = _resolve_column(df, "Tiempo", "tiempo")

    if ext_column is None:
        raise KeyError("ext")
    if acceso_column is None:
        raise KeyError("Acceso")
    if deteccion_column is None:
        raise KeyError("Deteccion")
    if ocupacion_column is None:
        raise KeyError("Ocupacion")
    if tiempo_column is None:
        raise KeyError("Tiempo")

    df = df.rename(
        columns={
            ext_column: "ext",
            acceso_column: "Acceso",
            deteccion_column: "Deteccion",
            ocupacion_column: "Ocupacion",
            tiempo_column: "Tiempo",
        }
    )

    df["Tiempo"] = pd.to_datetime(df["Tiempo"], errors="coerce")
    df["ext"] = df["ext"].astype(str)
    df["Acceso"] = df["Acceso"].astype(str)
    df["Deteccion"] = pd.to_numeric(df["Deteccion"], errors="coerce")
    df["Ocupacion"] = pd.to_numeric(df["Ocupacion"], errors="coerce")
    if externo:
        df = df[df["ext"] == str(externo)].copy()
    df = df.dropna(subset=["Tiempo", "Acceso", "Deteccion", "Ocupacion"])
    if df.empty:
        if externo:
            raise ValueError(f"No hay datos para el externo {externo}.")
        raise ValueError("No hay datos de detecciones para la red en el rango seleccionado.")

    df_plot = (
        df.groupby(["Tiempo", "Acceso"], as_index=False)
        .agg({"Deteccion": "sum", "Ocupacion": "mean"})
    )
    df_plot["FechaHora"] = df_plot["Tiempo"].dt.strftime("%d/%m/%Y %H:%M")

    accesos = sorted(df_plot["Acceso"].unique())
    if not accesos:
        raise ValueError(f"No hay accesos para el externo {externo}." if externo else "No hay accesos para la red en el rango seleccionado.")

    cols = min(4, len(accesos))
    rows = math.ceil(len(accesos) / 4)
    fig = make_subplots(
        rows=rows,
        cols=cols,
        subplot_titles=[f"Acceso {acceso}" for acceso in accesos],
        horizontal_spacing=0.05,
        vertical_spacing=0.10,
    )

    colores = [
        "#00FFFF",
        "#FFD700",
        "#FF7F50",
        "#7CFC00",
        "#FF69B4",
        "#9370DB",
        "#00CED1",
        "#FFA500",
    ]

    plotted = 0
    for i, acceso in enumerate(accesos):
        row = i // 4 + 1
        col = i % 4 + 1
        temp = df_plot[df_plot["Acceso"] == acceso].copy().sort_values("Tiempo")
        if len(temp) < 10:
            continue

        x = temp["Ocupacion"].to_numpy(dtype=float)
        y = temp["Deteccion"].to_numpy(dtype=float)
        if len(np.unique(x)) < 3:
            continue

        coef = np.polyfit(x, y, 2)
        a, b, c = coef
        modelo = np.poly1d(coef)
        y_pred = modelo(x)

        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 0.0 if ss_tot == 0 else 1 - (ss_res / ss_tot)

        x_fit = np.linspace(x.min(), x.max(), 300)
        y_fit = modelo(x_fit)
        color = colores[i % len(colores)]

        fig.add_trace(
            go.Scatter(
                x=temp["Ocupacion"],
                y=temp["Deteccion"],
                mode="markers",
                showlegend=False,
                marker=dict(size=6, color=color, opacity=0.40),
                customdata=np.stack((temp["FechaHora"],), axis=-1),
                hovertemplate=(
                    f"<b>{("EXT " + str(externo)) if externo else "RED SEMA"}</b><br><br>"
                    f"<b>ACCESO {acceso}</b><br><br>"
                    "%{customdata[0]}<br><br>"
                    "Ocupación Promedio: %{x:.1f}%<br>"
                    "Detecciones Totales: %{y}"
                    "<extra></extra>"
                ),
            ),
            row=row,
            col=col,
        )
        fig.add_trace(
            go.Scatter(
                x=x_fit,
                y=y_fit,
                mode="lines",
                showlegend=False,
                line=dict(color=color, width=3),
                hoverinfo="skip",
            ),
            row=row,
            col=col,
        )

        ecuacion = f"y={a:.4f}x² + {b:.4f}x + {c:.4f}"
        fig.add_annotation(
            x=0.02,
            y=0.98,
            xref=f"x{i+1} domain" if i > 0 else "x domain",
            yref=f"y{i+1} domain" if i > 0 else "y domain",
            text=(
                f"<b>{ecuacion}</b><br>"
                f"R² = {r2:.3f}<br>"
                f"N = {len(temp):,}<br>"
                f"Det = {temp['Deteccion'].sum():,.0f}"
            ),
            showarrow=False,
            align="left",
            font=dict(size=10, color="white"),
            bgcolor="rgba(0,0,0,0.75)",
            bordercolor=color,
            borderwidth=1,
        )
        plotted += 1

    if plotted == 0:
        raise ValueError(f"No hay suficientes datos para construir el scatter del externo {externo}." if externo else "No hay suficientes datos para construir el scatter de la red.")

    fig.update_layout(
        template="plotly_dark",
        width=min(1800, cols * 450),
        height=rows * 450,
        paper_bgcolor="#111111",
        plot_bgcolor="#111111",
        title=dict(
            text=(f"Detecciones Totales vs Ocupación Promedio por Acceso - Ext {externo}" if externo else "Detecciones Totales vs Ocupación Promedio por Acceso - Red SEMA"),
            x=0.5,
        ),
        font=dict(color="white", size=13),
        showlegend=False,
        margin=dict(l=40, r=40, t=80, b=40),
    )
    fig.update_xaxes(title="Ocupación Promedio (%)", gridcolor="#333333", zeroline=False)
    fig.update_yaxes(title="Detecciones Totales", gridcolor="#333333", zeroline=False)

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "doubleClick": "reset",
        "showTips": False,
    }
    return fig, config
