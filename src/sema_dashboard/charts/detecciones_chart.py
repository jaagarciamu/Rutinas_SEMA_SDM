from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


def _resolve_column(df: pd.DataFrame, *candidates: str) -> str | None:
    existing = {str(column).strip().lower(): column for column in df.columns}
    for candidate in candidates:
        match = existing.get(candidate.lower())
        if match is not None:
            return match
    return None


def build_detecciones_chart(detecciones_df: pd.DataFrame, externo: str):
    df = detecciones_df.copy()
    if df.empty:
        raise ValueError("No hay datos de detecciones para construir la grafica.")

    ext_column = _resolve_column(df, "ext", "est", "EXT", "EST")
    acceso_column = _resolve_column(df, "Acceso", "acceso")
    deteccion_column = _resolve_column(df, "Deteccion", "deteccion")
    tiempo_column = _resolve_column(df, "Tiempo", "tiempo")

    if ext_column is None:
        raise KeyError("ext")
    if acceso_column is None:
        raise KeyError("Acceso")
    if deteccion_column is None:
        raise KeyError("Deteccion")
    if tiempo_column is None:
        raise KeyError("Tiempo")

    df = df.rename(
        columns={
            ext_column: "ext",
            acceso_column: "Acceso",
            deteccion_column: "Deteccion",
            tiempo_column: "Tiempo",
        }
    )

    df["Tiempo"] = pd.to_datetime(df["Tiempo"], errors="coerce")
    df["ext"] = df["ext"].astype(str)
    df["Acceso"] = df["Acceso"].astype(str)
    df = df[df["ext"] == str(externo)].copy()
    if df.empty:
        raise ValueError(f"No hay datos para el externo {externo}.")

    df_plot = (
        df.groupby(["Tiempo", "Acceso"], as_index=False)["Deteccion"]
        .sum()
    )

    fig = go.Figure()
    for acceso in sorted(df_plot["Acceso"].unique()):
        temp = df_plot[df_plot["Acceso"] == acceso].sort_values("Tiempo")
        total_det = temp["Deteccion"].sum()
        fig.add_trace(
            go.Scatter(
                x=temp["Tiempo"],
                y=temp["Deteccion"],
                mode="lines",
                name=f"Acceso {acceso}<br>{total_det:,.0f} veh",
                line=dict(width=2),
                hoverlabel=dict(
                    bgcolor="#111111",
                    bordercolor="#333333",
                    font_size=11,
                    font_family="Arial",
                ),
                hovertemplate=(
                    f"<b>ACCESO {acceso}</b><br>"
                    "Detecciones: %{y}"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        template="plotly_dark",
        title=dict(text=f"Detecciones por Acceso - Ext {externo}", x=0.5),
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
        gridwidth=0.5,
        rangeslider_visible=True,
    )
    fig.update_yaxes(
        title="Detecciones",
        showgrid=True,
        gridwidth=0.5,
        rangemode="tozero",
    )

    config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "doubleClick": "reset",
        "showTips": False,
    }
    return fig, config
