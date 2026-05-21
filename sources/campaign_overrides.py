"""
Overrides manuales del cruce campaña Meta → producto Dropi.

Cuando el match automático (id exacto / nombre fuzzy) falla o el usuario
quiere forzar otra asignación, se guarda acá en un JSON simple:
    { "nombre exacto de la campaña (lower)": "<dropi_product_id>" }
"""
from __future__ import annotations

import json
from pathlib import Path

OVERRIDES_FILE = (Path(__file__).resolve().parent.parent
                  / "campaign_overrides.json")


def _norm_key(name: str) -> str:
    return (name or "").strip().lower()


def load_overrides() -> dict[str, str]:
    if not OVERRIDES_FILE.exists():
        return {}
    try:
        return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_override(campaign_name: str, product_id: str | None) -> None:
    """Si product_id es vacío/None → elimina el override."""
    overrides = load_overrides()
    key = _norm_key(campaign_name)
    if not key:
        return
    if product_id:
        overrides[key] = str(product_id)
    else:
        overrides.pop(key, None)
    OVERRIDES_FILE.write_text(
        json.dumps(overrides, ensure_ascii=False, indent=2),
        encoding="utf-8")


def resolve_override(campaign_name: str) -> str | None:
    return load_overrides().get(_norm_key(campaign_name))
