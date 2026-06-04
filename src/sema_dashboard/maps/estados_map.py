from __future__ import annotations

import pandas as pd
import pydeck as pdk


def build_estados_map(dataset: pd.DataFrame, view_state: dict) -> pdk.Deck:
    layer = pdk.Layer(
        "ScatterplotLayer",
        id="estados-layer",
        data=dataset,
        get_position="[longitud, latitud]",
        get_fill_color="color_rgb",
        get_radius="radius_value",
        pickable=True,
        stroked=True,
        filled=True,
        radius_min_pixels=3,
        radius_max_pixels=6,
        line_width_min_pixels=1,
        get_line_color=[255, 255, 255, 160],
        opacity=0.75,
    )
    return pdk.Deck(
        map_style=pdk.map_styles.CARTO_DARK,
        initial_view_state=pdk.ViewState(**view_state, pitch=10, bearing=100),
        layers=[layer],
        tooltip={
            "html": "{tooltip_html}",
            "style": {
                "backgroundColor": "#222222",
                "color": "white",
                "fontFamily": "Arial",
                "fontSize": "9px",
                "borderRadius": "10px",
                "padding": "6px 8px",
                "maxWidth": "260px",
            },
        },
    )
