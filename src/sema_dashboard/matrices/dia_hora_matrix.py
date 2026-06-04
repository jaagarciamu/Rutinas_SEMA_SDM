from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from sema_dashboard.charts.detecciones_chart import _resolve_column


def build_dia_hora_matrix(detecciones_df: pd.DataFrame, externo: str | None = None, fecha_inicio=None, fecha_fin=None):
    df = detecciones_df.copy()
    if df.empty:
        raise ValueError("No hay datos de detecciones para construir la matriz.")

    ext_column = _resolve_column(df, "ext", "est", "EXT", "EST")
    tiempo_column = _resolve_column(df, "Tiempo", "tiempo")
    deteccion_column = _resolve_column(df, "Deteccion", "deteccion")

    if ext_column is None:
        raise KeyError("ext")
    if tiempo_column is None:
        raise KeyError("Tiempo")
    if deteccion_column is None:
        raise KeyError("Deteccion")

    df = df.rename(columns={ext_column: "ext", tiempo_column: "Tiempo", deteccion_column: "Deteccion"})
    df["Tiempo"] = pd.to_datetime(df["Tiempo"], errors="coerce")
    df["ext"] = df["ext"].astype(str)
    df["Deteccion"] = pd.to_numeric(df["Deteccion"], errors="coerce")
    if externo:
        df = df[df["ext"] == str(externo)].copy()
    df = df[df["Tiempo"].notna() & df["Deteccion"].notna()].copy()
    if df.empty:
        if externo:
            raise ValueError(f"No hay datos para el externo {externo}.")
        raise ValueError("No hay datos de detecciones para la red en el rango seleccionado.")

    dias = {
        0: "Lunes",
        1: "Martes",
        2: "Miércoles",
        3: "Jueves",
        4: "Viernes",
        5: "Sábado",
        6: "Domingo",
    }

    df["DiaSemana"] = df["Tiempo"].dt.dayofweek
    df["Hora"] = df["Tiempo"].dt.hour
    df["FechaDia"] = df["Tiempo"].dt.normalize()

    matriz = df.groupby(["Hora", "DiaSemana"], as_index=False)["Deteccion"].sum()
    orden_dias_idx = list(range(7))
    pivot = matriz.pivot(index="Hora", columns="DiaSemana", values="Deteccion")
    pivot = pivot.reindex(columns=orden_dias_idx)
    pivot = pivot.reindex(index=range(24), fill_value=0).fillna(0)

    fechas_por_dia = (
        df.sort_values("Tiempo")
        .groupby("DiaSemana")["FechaDia"]
        .first()
        .to_dict()
    )

    x_labels = []
    x_hover_dates = []
    for day_idx in orden_dias_idx:
        nombre = dias[day_idx]
        fecha = fechas_por_dia.get(day_idx)
        if pd.notna(fecha):
            fecha_corta = pd.to_datetime(fecha).strftime("%d/%m")
            x_labels.append(f"<b>{nombre}</b><br><b>{fecha_corta}</b>")
            x_hover_dates.append(fecha_corta)
        else:
            x_labels.append(f"<b>{nombre}</b>")
            x_hover_dates.append("-")

    colorscale = [
        [0.00, "#1F3B2D"],
        [0.25, "#4E8F73"],
        [0.50, "#C9B458"],
        [0.75, "#C98A4A"],
        [1.00, "#B85A5A"],
    ]

    fig = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=x_labels,
            y=[f"{h:02d}:00" for h in pivot.index],
            text=pivot.values,
            customdata=[x_hover_dates for _ in range(len(pivot.index))],
            texttemplate="%{text:,.0f}",
            textfont=dict(color="rgba(255,255,255,0.85)", size=10),
            colorscale=colorscale,
            xgap=1,
            ygap=1,
            colorbar=dict(
                title=dict(text="Detecciones", side="right"),
                tickfont=dict(size=11),
            ),
            hovertemplate=(
                "<b>%{x}</b><br>"
                "<b>Fecha:</b> %{customdata}<br>"
                "<b>Hora:</b> %{y}<br>"
                "<b>Detecciones:</b> %{z:,.0f}"
                "<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        template="plotly_dark",
        title=dict(text=(f"Mapa de Calor de Detecciones - Ext {externo}" if externo else "Mapa de Calor de Detecciones - Red SEMA"), x=0.5, y=0.98, font=dict(size=17)),
        paper_bgcolor="#111111",
        plot_bgcolor="#111111",
        font=dict(color="white", size=13),
        height=520,
        margin=dict(l=70, r=70, t=90, b=40),
    )
    fig.update_xaxes(side="top", title="", tickfont=dict(size=12, color="white"), showgrid=False)
    fig.update_yaxes(title="", tickfont=dict(size=13, color="white"), showgrid=False)

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "doubleClick": "reset",
        "showTips": False,
    }
    return fig, config
