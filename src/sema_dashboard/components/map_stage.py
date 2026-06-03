from __future__ import annotations

import pydeck as pdk
import streamlit as st


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

    view_state = pdk.ViewState(
        **st.session_state.map_view_state,
        pitch=10,
        bearing=100
    )

    deck = pdk.Deck(
        map_style=pdk.map_styles.CARTO_DARK,
        initial_view_state=view_state,
        layers=[],
        tooltip={
            "text": "Mapa base listo para integrar capas SEMA"
        },
    )

    st.pydeck_chart(
        deck,
        use_container_width=True,
        height=680
    )

    st.markdown(
        "</div>",
        unsafe_allow_html=True
    )