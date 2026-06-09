from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.express as px

from sema_dashboard.charts.detecciones_chart import _resolve_column


def _resolve_plan_column(hist: pd.DataFrame) -> str:
    plan_final_column = _resolve_column(hist, "plan_Final", "Plan_Final", "PLAN_FINAL")
    if plan_final_column is not None:
        return plan_final_column

    plan_ingresa_column = _resolve_column(hist, "Plan_ingresa", "plan_ingresa")
    if plan_ingresa_column is not None:
        return plan_ingresa_column

    raise KeyError("plan_Final")


def _split_segments_by_day(df: pd.DataFrame) -> pd.DataFrame:
    segments: list[dict] = []

    for row in df.itertuples(index=False):
        current_start = row.Tiempo
        current_end = row.Tiempo_fin

        if pd.isna(current_start) or pd.isna(current_end) or current_end <= current_start:
            continue

        while current_start.normalize() != current_end.normalize():
            day_end = current_start.normalize() + timedelta(days=1)
            segments.append(
                {
                    "Plan": row.Plan,
                    "Inicio": current_start,
                    "Fin": day_end,
                    "Fecha": current_start.strftime("%d/%m/%Y"),
                }
            )
            current_start = day_end

        segments.append(
            {
                "Plan": row.Plan,
                "Inicio": current_start,
                "Fin": current_end,
                "Fecha": current_start.strftime("%d/%m/%Y"),
            }
        )

    return pd.DataFrame(segments)


def _collapse_consecutive_plans(hist: pd.DataFrame) -> pd.DataFrame:
    hist = hist.sort_values(["Tiempo", "Referencia"]).copy()
    hist = hist.drop_duplicates(subset=["Tiempo"], keep="last").reset_index(drop=True)
    hist = hist[hist["Plan"].ne(hist["Plan"].shift())].reset_index(drop=True)
    return hist


def build_planes_chart(planes_df: pd.DataFrame, externo: str, fecha_inicio=None, fecha_fin=None):
    hist = planes_df.copy()
    if hist.empty:
        raise ValueError("No hay datos de planes para construir la grafica.")

    ext_column = _resolve_column(hist, "Externo", "externo")
    tiempo_column = _resolve_column(hist, "Tiempo", "tiempo")
    referencia_column = _resolve_column(hist, "Referencia", "referencia")
    plan_column = _resolve_plan_column(hist)

    if ext_column is None:
        raise KeyError("Externo")
    if tiempo_column is None:
        raise KeyError("Tiempo")
    if referencia_column is None:
        raise KeyError("Referencia")

    hist = hist.rename(
        columns={
            ext_column: "Externo",
            tiempo_column: "Tiempo",
            referencia_column: "Referencia",
            plan_column: "Plan",
        }
    )

    hist["Tiempo"] = pd.to_datetime(hist["Tiempo"], errors="coerce")
    hist["Externo"] = pd.to_numeric(hist["Externo"], errors="coerce")
    hist["Referencia"] = pd.to_numeric(hist["Referencia"], errors="coerce")
    hist["Plan"] = pd.to_numeric(hist["Plan"], errors="coerce")
    hist = hist.dropna(subset=["Tiempo", "Externo", "Referencia", "Plan"])
    hist = hist[hist["Externo"].astype(int).astype(str) == str(externo)].copy()
    if hist.empty:
        raise ValueError(f"No hay datos para el externo {externo}.")

    hist = _collapse_consecutive_plans(hist)

    now = pd.Timestamp.now().floor("s")
    hist["Tiempo_fin"] = hist["Tiempo"].shift(-1)

    last_start = hist.iloc[-1]["Tiempo"]
    if last_start.normalize() == now.normalize():
        hist.loc[hist.index[-1], "Tiempo_fin"] = now
    else:
        hist.loc[hist.index[-1], "Tiempo_fin"] = last_start.normalize() + timedelta(days=1)

    hist = hist[hist["Tiempo_fin"] > hist["Tiempo"]].copy()

    if fecha_inicio is not None:
        fecha_inicio_ts = pd.to_datetime(fecha_inicio)
        hist["Tiempo"] = hist["Tiempo"].clip(lower=fecha_inicio_ts)
    else:
        fecha_inicio_ts = hist["Tiempo"].min().normalize()

    if fecha_fin is not None:
        fecha_fin_ts = pd.to_datetime(fecha_fin) + pd.Timedelta(days=1)
        hist["Tiempo_fin"] = hist["Tiempo_fin"].clip(upper=fecha_fin_ts)
    else:
        fecha_fin_ts = hist["Tiempo_fin"].max()

    hist = hist[(hist["Tiempo_fin"] > hist["Tiempo"]) & (hist["Tiempo"] < fecha_fin_ts) & (hist["Tiempo_fin"] > fecha_inicio_ts)].copy()
    if hist.empty:
        raise ValueError(f"No hay planes visibles para el externo {externo} en el rango seleccionado.")

    df_filtrado = _split_segments_by_day(hist)
    if df_filtrado.empty:
        raise ValueError(f"No hay planes visibles para el externo {externo} en el rango seleccionado.")

    axis_day = pd.Timestamp("2000-01-01")
    df_filtrado["Plan"] = df_filtrado["Plan"].astype(int).astype(str)
    df_filtrado["HoraInicio"] = axis_day + (df_filtrado["Inicio"] - df_filtrado["Inicio"].dt.normalize())
    df_filtrado["HoraFin"] = axis_day + (df_filtrado["Fin"] - df_filtrado["Fin"].dt.normalize())
    midnight_mask = (
        df_filtrado["Fin"].dt.time == pd.Timestamp("00:00:00").time()
    ) & (df_filtrado["Fin"].dt.normalize() > df_filtrado["Inicio"].dt.normalize())
    df_filtrado.loc[midnight_mask, "HoraFin"] = axis_day + pd.Timedelta(days=1)
    df_filtrado["FechaInicio"] = df_filtrado["Inicio"].dt.strftime("%d/%m/%Y %H:%M:%S")
    df_filtrado["FechaFin"] = df_filtrado["Fin"].dt.strftime("%d/%m/%Y %H:%M:%S")
    df_filtrado["duracion"] = (df_filtrado["Fin"] - df_filtrado["Inicio"]).dt.total_seconds().clip(lower=0)
    df_filtrado["duracion"] = pd.to_timedelta(df_filtrado["duracion"], unit="s").astype(str)
    fechas_descendentes = sorted(
        df_filtrado["Fecha"].unique().tolist(),
        key=lambda item: pd.to_datetime(item, format="%d/%m/%Y"),
        reverse=True,
    )
    fecha_posicion = {fecha: len(fechas_descendentes) - index for index, fecha in enumerate(fechas_descendentes)}
    df_filtrado["FechaPos"] = df_filtrado["Fecha"].map(fecha_posicion).astype(float)
    df_filtrado = df_filtrado.sort_values(["FechaPos", "HoraInicio"]).copy()

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
        "11": "#1E90FF",
        "12": "#32CD32",
        "13": "#FF1493",
        "14": "#FF5A3D",
        "15": "#FFD166",
        "16": "#06D6A0",
        "17": "#A970FF",
        "18": "#118AB2",
        "19": "#EF476F",
        "20": "#F4A261",
        "0": "#444444",
    }

    fig = px.timeline(
        df_filtrado,
        x_start="HoraInicio",
        x_end="HoraFin",
        y="FechaPos",
        color="Plan",
        text="Plan",
        color_discrete_map=color_map,
        hover_data={
            "FechaInicio": True,
            "FechaFin": True,
            "duracion": True,
            "HoraInicio": False,
            "HoraFin": False,
            "FechaPos": False,
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
        plot_bgcolor="#111111",
        title=dict(
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
        range=[axis_day, axis_day + pd.Timedelta(days=1)],
        zeroline=False,
    )
    fig.update_yaxes(
        title_font=dict(size=13),
        tickfont=dict(size=10),
        showgrid=True,
        gridcolor="#333333",
        tickmode="array",
        tickvals=[fecha_posicion[fecha] for fecha in fechas_descendentes],
        ticktext=fechas_descendentes,
        range=[0.5, len(fechas_descendentes) + 0.5],
        zeroline=False,
    )

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "doubleClick": "reset",
        "showTips": False,
    }
    return fig, config
