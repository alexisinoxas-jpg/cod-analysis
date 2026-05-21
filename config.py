"""Carga de configuración desde .env (python-dotenv)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ── Dropi (instancia Chile) ──────────────────────────────────
DROPI_EMAIL = os.getenv("DROPI_EMAIL", "")
DROPI_PASSWORD = os.getenv("DROPI_PASSWORD", "")

LOGIN_URL = "https://app.dropi.cl/auth/login"
ORDERS_URL = "https://app.dropi.cl/dashboard/orders"
REPORTS_URL = "https://app.dropi.cl/dashboard/reports"

# ── Meta Ads API ─────────────────────────────────────────────
# El ad account es el de la tienda COD (act_1577542186081655). El token se
# reutilizaría del proyecto notion-bonus-sync. No se usa nada de ISYRA.
#
# SEGURIDAD (protocolo anti-ban Meta): toda llamada viva a graph.facebook.com
# está DESHABILITADA por defecto. La cuenta dev está suspendida (7.e.i.3) →
# regla "no más requests/probing post-ban". El gasto Meta entra por modo
# MANUAL (pegado del Ads Manager). Solo poner META_API_ENABLED=true cuando
# exista un token válido y la cuenta no esté sancionada.
META_API_ENABLED = os.getenv("META_API_ENABLED", "false").lower() in (
    "1", "true", "yes", "si", "sí")
META_AD_ACCOUNT_ID_COD = os.getenv("META_AD_ACCOUNT_ID_COD", "")
META_API_VERSION = "v21.0"  # versión pineada (nunca 'latest')

_BONUS_SYNC_ENV = Path.home() / "notion-bonus-sync" / ".env.local"


def _read_env_file(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip()
    return ""


META_ACCESS_TOKEN_COD = (
    os.getenv("META_ACCESS_TOKEN_COD")
    or _read_env_file(_BONUS_SYNC_ENV, "META_TOKEN")
)
META_APP_ID_COD = os.getenv("META_APP_ID_COD", "2323453014813196")
META_APP_SECRET_COD = os.getenv("META_APP_SECRET_COD", "")

# Regex con grupo (?P<key>...) para mapear nombre de campaña → producto.
META_CAMPAIGN_PRODUCT_REGEX = os.getenv("META_CAMPAIGN_PRODUCT_REGEX", "")

# ── Shopify de la tienda COD (Noxa Store — distinta de ISYRA) ─
# Usado para enriquecer la atribución: ID DE ORDEN DE TIENDA (Dropi)
# → line items + utm_source/landing del pedido Shopify.
SHOPIFY_STORE_COD = os.getenv("SHOPIFY_STORE_COD", "")
SHOPIFY_ACCESS_TOKEN_COD = os.getenv("SHOPIFY_ACCESS_TOKEN_COD", "")
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-10")

# ── Paths ────────────────────────────────────────────────────
DEBUG_DIR = BASE_DIR / "debug"
DEBUG_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR = Path.home() / "Downloads"

# ── Gmail IMAP (lectura automática del reporte programado de Ads Manager) ──
# Vía contraseña de aplicación (no OAuth → setup mucho más simple).
# Requiere 2FA activado en la cuenta de Google.
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
GMAIL_REPORT_SUBJECT = os.getenv("GMAIL_REPORT_SUBJECT",
                                 "COD - Spend diario por campaña")
GMAIL_REPORT_SENDER = os.getenv("GMAIL_REPORT_SENDER", "facebookmail.com")

# ── Google Drive (subida del Excel de pendientes vía OAuth user delegation) ──
# Las Service Accounts NO sirven para cuentas Gmail personales (no tienen
# quota propia de Drive). Usamos OAuth: vos autorizás con tu cuenta y la
# app sube como vos. Scope drive.file → solo accede a archivos que crea.
# Setup: ver tools/get_drive_token.py.
GOOGLE_DRIVE_OAUTH_KEY = os.getenv("GOOGLE_DRIVE_OAUTH_KEY", "")
GOOGLE_DRIVE_OAUTH_SECRET = os.getenv("GOOGLE_DRIVE_OAUTH_SECRET", "")
GOOGLE_DRIVE_REFRESH_TOKEN = os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN", "")
GOOGLE_DRIVE_OAUTH_PORT = int(os.getenv("GOOGLE_DRIVE_OAUTH_PORT", "8765"))
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")

PORT = int(os.getenv("PORT", "5057"))


def require(name: str) -> str:
    """Devuelve la var o lanza un error claro si está vacía."""
    val = globals().get(name) or os.getenv(name, "")
    if not val:
        raise RuntimeError(
            f"Falta {name} en .env. Copiá .env.example a .env y completalo."
        )
    return val
