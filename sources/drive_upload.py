"""
Subida automática del Excel de pendientes a Google Drive vía OAuth user
delegation (NO Service Account).

Por qué OAuth y no SA: las Service Accounts no tienen quota de Drive en
cuentas Gmail personales (solo en Google Workspace con Shared Drives), así
que el upload falla con 'Service Accounts do not have storage quota'. OAuth
delegation usa el Drive del usuario (la cuenta personal), donde sí hay quota,
y los archivos aparecen como propios.

Scope: drive.file → la app solo ve los archivos que ella misma crea. Es
'non-sensitive' para Google → la app se puede publicar a producción sin
verificación → los refresh tokens NO expiran.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

import config

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
DRIVE_API = "https://www.googleapis.com/drive/v3/files"
ABOUT_API = "https://www.googleapis.com/drive/v3/about"


def is_configured() -> bool:
    return bool(config.GOOGLE_DRIVE_OAUTH_KEY
                and config.GOOGLE_DRIVE_OAUTH_SECRET
                and config.GOOGLE_DRIVE_REFRESH_TOKEN
                and config.GOOGLE_DRIVE_FOLDER_ID)


def _get_access_token() -> tuple[str | None, str | None]:
    cid = config.GOOGLE_DRIVE_OAUTH_KEY
    secret = config.GOOGLE_DRIVE_OAUTH_SECRET
    refresh = config.GOOGLE_DRIVE_REFRESH_TOKEN
    if not (cid and secret and refresh):
        return None, ("Faltan GOOGLE_DRIVE_OAUTH_KEY / OAUTH_SECRET / "
                      "REFRESH_TOKEN en .env (corré get_drive_token.py).")
    try:
        r = requests.post(TOKEN_URL, data={
            "client_id": cid, "client_secret": secret,
            "refresh_token": refresh, "grant_type": "refresh_token",
        }, timeout=30)
        d = r.json()
        if "access_token" not in d:
            return None, (f"Drive token refresh falló: "
                          f"{d.get('error_description', d.get('error', d))}")
        return d["access_token"], None
    except requests.RequestException as e:
        return None, f"Red al refrescar Drive token: {e}"


def upload_file(file_bytes: bytes, filename: str,
                mime_type: str = ("application/vnd.openxmlformats-"
                                  "officedocument.spreadsheetml.sheet")
                ) -> tuple[str | None, str | None]:
    """Sube file_bytes a config.GOOGLE_DRIVE_FOLDER_ID. Devuelve
    (webViewLink, error_msg). Uno de los dos será None."""
    folder = (config.GOOGLE_DRIVE_FOLDER_ID or "").strip()
    if not folder:
        return None, "Falta GOOGLE_DRIVE_FOLDER_ID en .env"
    token, err = _get_access_token()
    if err:
        return None, err

    metadata = {"name": filename, "parents": [folder]}
    boundary = "_cod_analysis_boundary_"
    body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    try:
        r = requests.post(
            UPLOAD_URL,
            params={"uploadType": "multipart", "fields": "id,webViewLink"},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": f"multipart/related; boundary={boundary}"},
            data=body, timeout=120,
        )
    except requests.RequestException as e:
        return None, f"Red Drive upload: {e}"
    try:
        d = r.json()
    except ValueError:
        return None, f"Respuesta no-JSON de Drive (status {r.status_code})"
    if "id" not in d:
        err = d.get("error", d)
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return None, f"Drive rechazó la subida: {msg}"
    return (d.get("webViewLink")
            or f"https://drive.google.com/file/d/{d['id']}/view"), None


def check_drive_access() -> tuple[bool, str]:
    """Healthcheck: ¿el refresh token funciona y la carpeta es accesible?"""
    if not (config.GOOGLE_DRIVE_OAUTH_KEY
            and config.GOOGLE_DRIVE_REFRESH_TOKEN):
        return False, ("Sin credenciales OAuth de Drive — subida a Drive "
                       "deshabilitada (Excel sigue descargable localmente).")
    folder = (config.GOOGLE_DRIVE_FOLDER_ID or "").strip()
    if not folder:
        return False, "Falta GOOGLE_DRIVE_FOLDER_ID."
    token, err = _get_access_token()
    if err:
        return False, err
    try:
        r = requests.get(
            f"{DRIVE_API}/{folder}",
            params={"fields": "id,name"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        d = r.json()
        if "name" in d:
            return True, f"OK · carpeta «{d['name']}»"
        msg = (d.get("error") or {}).get("message", str(d))
        return False, f"Drive no accede a la carpeta: {msg}"
    except requests.RequestException as e:
        return False, f"Red Drive: {e}"


def get_oauth_user_email() -> str | None:
    """Devuelve el email del user que autorizó (para mostrar en la UI)."""
    token, err = _get_access_token()
    if err:
        return None
    try:
        r = requests.get(
            ABOUT_API, params={"fields": "user(emailAddress)"},
            headers={"Authorization": f"Bearer {token}"}, timeout=15,
        )
        d = r.json()
        return ((d.get("user") or {}).get("emailAddress"))
    except requests.RequestException:
        return None


# Compatibilidad con código viejo que llamaba a get_sa_email()
def get_sa_email() -> str | None:
    return get_oauth_user_email()
