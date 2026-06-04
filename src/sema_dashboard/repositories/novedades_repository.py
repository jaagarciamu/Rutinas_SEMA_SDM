from __future__ import annotations

import pandas as pd

from sema_dashboard.repositories.estados_repository import fetch_estados


def fetch_novedades() -> pd.DataFrame:
    return fetch_estados().copy()
