from __future__ import annotations

from pathlib import Path
import sys

import streamlit as st
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"

load_dotenv(ROOT_DIR / "config" / ".env")

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sema_dashboard.config import PAGE_TITLE
from sema_dashboard.state import initialize_state
from sema_dashboard.ui.layout import render_app
from sema_dashboard.ui.theme import load_global_theme


def main() -> None:
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon="🚦",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    initialize_state()
    load_global_theme(ROOT_DIR / "app" / "assets" / "styles" / "control_center.css")
    render_app()


if __name__ == "__main__":
    main()
