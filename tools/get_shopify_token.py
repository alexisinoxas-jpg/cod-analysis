r"""
Camino alternativo (OAuth local) para obtener el Admin API token de Shopify
de la tienda COD, usando las credenciales de la app "alertas stock v2".

Uso:
    .\.venv\Scripts\python tools\get_shopify_token.py

Antes de correrlo, en la config de la app "alertas stock v2" (Shopify →
Configuración → Apps y canales → Desarrollar apps → alertas stock v2, o en
Partners) agregá esta URL en "Allowed redirection URL(s)":
    http://localhost:3456/callback

El script abre el login de Shopify, captura el código, lo canjea por el
token y lo escribe directo en .env (SHOPIFY_ACCESS_TOKEN_COD). No imprime
el token en pantalla salvo que falle la escritura.
"""
from __future__ import annotations

import http.server
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv  # noqa: E402
import os  # noqa: E402

ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)

STORE = os.getenv("SHOPIFY_STORE_COD", "").replace("https://", "").strip("/")
KEY = os.getenv("SHOPIFY_OAUTH_KEY", "")
SECRET = os.getenv("SHOPIFY_OAUTH_SECRET", "")
PORT = int(os.getenv("SHOPIFY_OAUTH_PORT", "3456"))
SCOPES = "read_orders,read_products"
REDIRECT_URI = f"http://localhost:{PORT}/callback"
STATE = secrets.token_hex(16)

_result: dict[str, str] = {}


def _fail(msg: str) -> None:
    print(f"\n❌ {msg}\n")
    sys.exit(1)


if not STORE or not KEY or not SECRET:
    _fail("Faltan SHOPIFY_STORE_COD / SHOPIFY_OAUTH_KEY / "
          "SHOPIFY_OAUTH_SECRET en .env")


def _write_env_token(token: str) -> bool:
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        out, found = [], False
        for ln in lines:
            if ln.strip().startswith("SHOPIFY_ACCESS_TOKEN_COD="):
                out.append(f"SHOPIFY_ACCESS_TOKEN_COD={token}")
                found = True
            else:
                out.append(ln)
        if not found:
            out.append(f"SHOPIFY_ACCESS_TOKEN_COD={token}")
        ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # silenciar logs HTTP
        pass

    def _send(self, code: int, html: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/callback":
            self._send(404, "Not found")
            return
        q = urllib.parse.parse_qs(u.query)
        if q.get("state", [""])[0] != STATE:
            self._send(400, "State inválido (posible CSRF). Reintentá.")
            _result["error"] = "state mismatch"
            return
        code = q.get("code", [""])[0]
        if not code:
            self._send(400, "No llegó 'code'.")
            _result["error"] = "no code"
            return
        # Canjear code → access token
        body = urllib.parse.urlencode({
            "client_id": KEY, "client_secret": SECRET, "code": code,
        }).encode()
        req = urllib.request.Request(
            f"https://{STORE}/admin/oauth/access_token", data=body,
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                import json
                data = json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            self._send(500, f"Error al canjear el token: {e}")
            _result["error"] = str(e)
            return
        token = data.get("access_token", "")
        if not token:
            self._send(500, f"Respuesta sin access_token: {data}")
            _result["error"] = "no access_token"
            return
        _result["token"] = token
        ok = _write_env_token(token)
        msg = ("✅ Token obtenido y guardado en .env "
               "(SHOPIFY_ACCESS_TOKEN_COD)." if ok else
               "✅ Token obtenido (revisá la consola para copiarlo).")
        self._send(200, f"<h2>{msg}</h2><p>Ya podés cerrar esta pestaña.</p>")


def main():
    auth_url = (
        f"https://{STORE}/admin/oauth/authorize?"
        + urllib.parse.urlencode({
            "client_id": KEY, "scope": SCOPES,
            "redirect_uri": REDIRECT_URI, "state": STATE,
        })
    )
    srv = http.server.HTTPServer(("localhost", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print(f"\nTienda: {STORE}")
    print(f"Redirect URI (debe estar permitido en la app): {REDIRECT_URI}")
    print("\nAbriendo el navegador para autorizar… si no abre, pegá esta URL:")
    print(auth_url + "\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("Esperando la autorización (Ctrl+C para cancelar)…")
    import time
    for _ in range(600):  # ~5 min
        if _result:
            break
        time.sleep(0.5)
    srv.shutdown()

    if _result.get("token"):
        if _write_env_token(_result["token"]):
            print("\n✅ Listo. SHOPIFY_ACCESS_TOKEN_COD escrito en .env.")
            print("   Verificá con: 🩺 Diagnóstico en la app.\n")
        else:
            print("\n⚠️  No pude escribir .env. Copiá este token a mano en "
                  "SHOPIFY_ACCESS_TOKEN_COD:\n")
            print(_result["token"] + "\n")
    else:
        _fail(f"No se obtuvo token. Detalle: {_result.get('error', 'timeout')}. "
              f"Verificá que {REDIRECT_URI} esté en 'Allowed redirection "
              f"URL(s)' de la app y que tenga scope read_orders.")


if __name__ == "__main__":
    main()
