from __future__ import annotations


def material_icon(name: str, extra_class: str = "") -> str:
    class_attr = f"material-symbols-outlined {extra_class}".strip()
    return f'<span class="{class_attr}">{name}</span>'
