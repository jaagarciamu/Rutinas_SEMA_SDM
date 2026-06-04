from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import plotly.express as px

from sema_dashboard.charts.detecciones_chart import _resolve_column


def build_planes_chart(planes_df: pd.DataFrame, externo: str, fecha_inicio=None, fecha_fin=None):
    hist = planes_df.copy()
    if hist.empty:
        raise ValueError("No hay datos de planes para construir la grafica.")

    ext_column = _resolve_column(hist, "Externo", "externo")
    tiempo_column = _resolve_column(hist, "Tiempo", "tiempo")
    plan_ingresa_column = _resolve_column(hist, "Plan_ingresa", "plan_ingresa")
    plan_finaliza_column = _resolve_column(hist, "Plan_finaliza", "plan_finaliza")
    referencia_column = _resolve_column(hist, "Referencia", "referencia")

    if ext_column is None:
        raise KeyError("Externo")
    if tiempo_column is None:
        raise KeyError("Tiempo")
    if plan_ingresa_column is None:
        raise KeyError("Plan_ingresa")
    if plan_finaliza_column is None:
        raise KeyError("Plan_finaliza")
    if referencia_column is None:
        raise KeyError("Referencia")

    hist = hist.rename(
        columns={
            ext_column: "Externo",
            tiempo_column: "Tiempo",
            plan_ingresa_column: "Plan_ingresa",
            plan_finaliza_column: "Plan_finaliza",
            referencia_column: "Referencia",
        }
    )

    hist["Tiempo"] = pd.to_datetime(hist["Tiempo"], errors="coerce")
    hist["Externo"] = pd.to_numeric(hist["Externo"], errors="coerce")
    hist["Plan_ingresa"] = pd.to_numeric(hist["Plan_ingresa"], errors="coerce")
    hist["Plan_finaliza"] = pd.to_numeric(hist["Plan_finaliza"], errors="coerce")
    hist["Referencia"] = pd.to_numeric(hist["Referencia"], errors="coerce")
    hist = hist.dropna(subset=["Tiempo", "Externo", "Plan_ingresa", "Plan_finaliza", "Referencia"])
    hist = hist[hist["Externo"].astype(int).astype(str) == str(externo)].copy()
    if hist.empty:
        raise ValueError(f"No hay datos para el externo {externo}.")

    ahora = pd.Timestamp.now().floor("S")
    hoy = ahora.normalize()
    planes_sel = hist.sort_values(by=["Referencia"], ascending=False).copy()

    nuevo = planes_sel[["Referencia", "Externo", "Plan_ingresa", "Tiempo"]].reset_index(drop=True)
    inicio = pd.DataFrame({"fecha_inicio": nuevo["Tiempo"].dt.date, "hora_inicio": nuevo["Tiempo"].dt.time})
    nuevo = pd.concat([nuevo, inicio], axis=1)

    ultima_fecha = planes_sel.iloc[0]["Tiempo"].normalize()
    if ultima_fecha == hoy:
        tiempo_fin = ahora
    else:
        tiempo_fin = (
            planes_sel.iloc[0]["Tiempo"].to_period("D").to_timestamp(how="end").round("1s")
            - timedelta(seconds=1)
        )

    add = pd.DataFrame({"Plan_finaliza": [planes_sel.iloc[0]["Plan_ingresa"]], "Tiempo": [tiempo_fin]})
    movido = planes_sel[["Plan_finaliza", "Tiempo"]]
    prueba = pd.concat([add, movido], ignore_index=True).set_axis(["Plan_finaliza", "Tiempo_finaliza"], axis=1)[:-1]
    tiempos = pd.DataFrame(
        {
            "fecha_finaliza": prueba["Tiempo_finaliza"].dt.date,
            "hora_finaliza": prueba["Tiempo_finaliza"].dt.time,
        }
    )
    prueba = pd.concat([prueba, tiempos], axis=1)

    union = pd.concat([nuevo, prueba], axis=1)
    union["plan_Final"] = np.where(union["Plan_ingresa"] == union["Plan_finaliza"], union["Plan_ingresa"], 0)

    temp_df = []
    for row in union.itertuples(index=False):
        if row.fecha_inicio != row.fecha_finaliza:
            lista1 = list(row)
            lista1[8] = lista1[4]
            lista1[9] = "23:59:59"
            temp_df.append(lista1)

            lista2 = list(row)
            lista2[4] = lista2[8]
            lista2[5] = "00:00:01"
            temp_df.append(lista2)
        else:
            temp_df.append(list(row))

    df_planes = pd.DataFrame(temp_df, columns=union.columns)
    df_planes["fecha_inicio"] = pd.to_datetime(df_planes["fecha_inicio"])
    df_planes["hora_inicio"] = pd.to_datetime(df_planes["hora_inicio"], format="%H:%M:%S")
    df_planes["fecha_finaliza"] = pd.to_datetime(df_planes["fecha_finaliza"])
    df_planes["hora_finaliza"] = pd.to_datetime(df_planes["hora_finaliza"], format="%H:%M:%S")
    df_planes["duracion"] = (
        pd.Timestamp("now").normalize() + (df_planes["hora_finaliza"] - df_planes["hora_inicio"])
    ).dt.time

    if fecha_inicio is not None:
        df_planes = df_planes[df_planes["fecha_inicio"] >= pd.to_datetime(fecha_inicio)]
    if df_planes.empty:
        raise ValueError(f"No hay planes visibles para el externo {externo} en el rango seleccionado.")

    df_filtrado = df_planes.copy()
    df_filtrado["FechaInicio"] = df_filtrado["Tiempo"].dt.strftime("%d/%m/%Y %H:%M:%S")
    df_filtrado["FechaFin"] = df_filtrado["Tiempo_finaliza"].dt.strftime("%d/%m/%Y %H:%M:%S")
    df_filtrado["Fecha"] = df_filtrado["fecha_finaliza"].dt.strftime("%d/%m/%Y")
    df_filtrado["plan_Final"] = df_filtrado["plan_Final"].fillna(0).astype(int).astype(str)

    color_map = {
        "1": "#00FF00",
        "2": "#7FFF00",
        "3": "#FFFF00",
        "4": "#FFB000",
        "5": "#FF7F00",
        "6": "#FF4500",
        "7": "#FF0000",
        "8": "#FF00FF",
        "9": "#00FFFF",
        "10": "#FFFFFF",
        "0": "#444444",
    }

    fig = px.timeline(
        df_filtrado,
        x_start="hora_inicio",
        x_end="hora_finaliza",
        y="Fecha",
        color="plan_Final",
        text="plan_Final",
        color_discrete_map=color_map,
        hover_data={
            "FechaInicio": True,
            "FechaFin": True,
            "duracion": True,
            "hora_inicio": False,
            "hora_finaliza": False,
            "Fecha": False,
        },
    )
    fig.update_traces(
        textposition="inside",
        textfont=dict(size=13, color="white"),
        marker_line_color="white",
        marker_line_width=0.5,
        hovertemplate=(
            "<b>Plan %{text}</b><br>"
            "Inicio: %{customdata[0]}<br>"
            "Fin: %{customdata[1]}<br>"
            "Duración: %{customdata[2]}<extra></extra>"
        ),
    )

    day1 = pd.to_datetime(fecha_inicio).strftime("%d/%m/%y") if fecha_inicio is not None else df_filtrado["Fecha"].min()
    day2 = pd.to_datetime(fecha_fin).strftime("%d/%m/%y") if fecha_fin is not None else df_filtrado["Fecha"].max()

    fig.update_layout(
        template="plotly_dark",
        height=520,
        paper_bgcolor="#111111",
        plot_bgcolor="#111111",        title=dict(
            text=(
                "Planes Semafóricos<br>"
                f"<span style='font-size:0.72em;'>Externo {externo}</span><br>"
                f"<span style='font-size:0.68em;'>Periodo: {day1} a {day2}</span>"
            ),
            x=0.5,
            y=0.96,
            xanchor="center",
            yanchor="top",
            font=dict(size=13, color="white"),
        ),
        xaxis_title="Hora del día",
        yaxis_title="Fecha",
        font=dict(color="white", size=13),
        hoverlabel=dict(bgcolor="#222222", font_size=13, font_family="Arial"),
        legend=dict(title="Plan", orientation="v", y=1, x=1.01, bgcolor="rgba(0,0,0,0)", font=dict(size=10, color="white")),
        margin=dict(l=70, r=90, t=82, b=40),
    )
    fig.update_xaxes(
        title_font=dict(size=13),
        tickfont=dict(size=10),
        showgrid=True,
        gridcolor="#333333",
        tickformat="%H:%M",
        zeroline=False,
    )
    fig.update_yaxes(
        title_font=dict(size=13),
        tickfont=dict(size=10),
        showgrid=True,
        gridcolor="#333333",
        autorange="reversed",
        zeroline=False,
    )

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "doubleClick": "reset",
        "showTips": False,
    }
    return fig, config
