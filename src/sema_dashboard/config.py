from __future__ import annotations

PAGE_TITLE = "Centro de Control SEMA"

MAPS = {
    "inventario": "Mapa Sistema Semaforizacion Inteligente",
    "detecciones": "Mapa Detecciones SEMA",
    "estados": "Mapa Estados Concert SEMA",
    "novedades": "Mapa Atencion Novedades SEMA",
}

CHARTS = {
    "detecciones": "Detecciones",
    "ocupacion": "Ocupacion",
    "scatter": "Scatter Ocupacion vs Deteccion",
    "planes": "Planes Semaforicos",
}

MATRICES = {
    "dia_hora": "Matriz Dia x Hora",
}

EXTERNAL_LINKS = {

    "SUANET": {
        "url": "https://suanet.movilidadbogota.gov.co/",
        "logo": "suanet.png",
    },

    "WEB SEMA": {
        "url": "https://sites.google.com/movilidadbogota.gov.co/websema",
        "logo": "web_sema.png",
    },

    "DATASTUDIO": {
        "url": "https://app.powerbi.com/view?r=eyJrIjoiYzJjZWE4YjQtYmFmNi00YjFjLThmNzUtM2UzNjFmMGNhMzczIiwidCI6IjFjMTg4ZWY2LTllOGYtNGQ5My04YjhjLWM4Njg4ZWFiYTAyYiIsImMiOjR9",
        "logo": "datastudio.png",
    },

    "ARCGIS": {
        "url": "https://experience.arcgis.com/experience/b2e34b5cced34d04986d8209b63e6533/page/HOME?views=ANALISIS-Y-DATOS%2CCORRECTIVOS-METRO%2CINTERSECCIONES-NUEVAS%2CVIDEODETECCI%C3%93N",
        "logo": "arcgis.png",
    },
}

DEFAULT_FILTERS = {
    "fecha_inicio": None,
    "fecha_fin": None,
    "externo": "",
    "direccion": "",
    "acceso": "",
    "zona_auto": "",
    "estado_concert": "",
    "gestion_sema": "",
}
