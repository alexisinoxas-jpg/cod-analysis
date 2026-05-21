r"""
OAuth local helper para obtener un refresh_token de Google Drive con tu
cuenta personal (la que tiene quota). Scope `drive.file` → la app solo ve
archivos que ella misma crea.

Uso:
    .\.venv\Scripts\python tools\get_drive_token.py

Pre-requisitos (una sola vez):
  1. En el mismo proyecto Google Cloud `cod-analysis-496923`:
     - APIs y Servicios → Pantalla de consentimiento de OAuth.
     - Tipo: Externo. Datos: nombre app (cod-analysis), tu email, soporte.
     - Scopes: agregá `.../auth/drive.file` (non-sensitive).
     - Usuarios de prueba: tu email.
     - **Publicar app** (botón "Publish"): así el refresh token no expira.
  2. APIs y Servicios → Credenciales → Crear credenciales →
     OAuth Client ID → Tipo: **Aplicación de escritorio** → nombre cualquiera
     → Crear → copiá el client_id y client_secret.
  3. Pegá en .env:
        GOOGLE_DRIVE_OAUTH_KEY=<client_id>
        GOOGLE_DRIVE_OAUTH_SECRET=<client_secret>
  4. Ejecutá este script. Se abre un login Google → autoriza →
     se guarda el refresh_token en .env automáticamente.
"""
from __future__ import annotations

import http.server
import secrets as _secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv  # noqa: E402
import os  # noqa: E402

ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)

KEY = os.getenv("GOOGLE_DRIVE_OAUTH_KEY", "")
SECRET = os.getenv("GOOGLE_DRIVE_OAUTH_SECRET", "")
PORT = int(os.getenv("GOOGLE_DRIVE_OAUTH_PORT", "8765"))
SCOPE = "https://www.googleapis.com/auth/drive.file"
REDIRECT_URI = f"http://localhost:{PORT}/callback"
STATE = _secrets.token_hex(16)

if not KEY or not SECRET:
    print("\n❌ Faltan GOOGLE_DRIVE_OAUTH_KEY / GOOGLE_DRIVE_OAUTH_SECRET "
          "en .env\n   (creá el OAuth Client ID en Google Cloud, pegalo "
          "en .env y reintentá).\n")
    sys.exit(1)

_result: dict[str, str] = {}


def _write_env_token(token: str) -> bool:
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
        out, found = [], False
        for ln in lines:
            if ln.strip().startswith("GOOGLE_DRIVE_REFRESH_TOKEN="):
                out.append(f"GOOGLE_DRIVE_REFRESH_TOKEN={token}")
                found = True
            else:
                out.append(ln)
        if not found:
            out.append(f"GOOGLE_DRIVE_REFRESH_TOKEN={token}")
        ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
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
            self._send(400, "<h2>State inválido (CSRF). Reintentá.</h2>")
            _result["error"] = "state mismatch"
            return
        if "error" in q:
            self._send(400, f"<h2>Error: {q['error'][0]}</h2>")
            _result["error"] = q["error"][0]
            return
        code = q.get("code", [""])[0]
        if not code:
            self._send(400, "<h2>No llegó 'code'.</h2>")
            _result["error"] = "no code"
            return
        body = urllib.parse.urlencode({
            "client_id": KEY, "client_secret": SECRET, "code": code,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        }).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token", data=body, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                import json
                data = json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            self._send(500, f"<h2>Error canjeando token: {e}</h2>")
            _result["error"] = str(e)
            return
        rt = data.get("refresh_token", "")
        if not rt:
            self._send(500, f"<h2>Respuesta sin refresh_token: "
                            f"{data.get('error_description', data)}</h2>")
            _result["error"] = "no refresh_token"
            return
        _result["refresh_token"] = rt
        ok = _write_env_token(rt)
        msg = ("✅ refresh_token obtenido y guardado en .env "
               "(GOOGLE_DRIVE_REFRESH_TOKEN)." if ok else
               "✅ refresh_token obtenido (revisá la consola).")
        self._send(200, f"<h2>{msg}</h2><p>Ya podés cerrar esta pestaña.</p>")


def main():
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urllib.parse.urlencode({
            "client_id": KEY, "redirect_uri": REDIRECT_URI,
            "response_type": "code", "scope": SCOPE,
            "access_type": "offline", "prompt": "consent", "state": STATE,
        })
    )
    srv = http.server.HTTPServer(("localhost", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print(f"\nRedirect URI (debe estar autorizado en el OAuth Client): "
          f"{REDIRECT_URI}")
    print("\nAbriendo el navegador para autorizar Drive. Si no abre, pegá:")
    print(auth_url + "\n")
    try:
        import webbrowser
        webbrowser.open(auth_url)
    except Exception:
        pass

    print("Esperando autorización (Ctrl+C para cancelar)…")
    for _ in range(600):
        if _result:
            break
        time.sleep(0.5)
    srv.shutdown()

    if _result.get("refresh_token"):
        if _write_env_token(_result["refresh_token"]):
            print("\n✅ Listo. GOOGLE_DRIVE_REFRESH_TOKEN escrito en .env.")
            print("   Reiniciá el server y verificá con 🩺 Diagnóstico.\n")
        else:
            print("\n⚠️  No pude escribir .env. Copiá esto a mano en "
                  "GOOGLE_DRIVE_REFRESH_TOKEN:\n")
            print(_result["refresh_token"] + "\n")
    else:
        print(f"\n❌ No se obtuvo refresh_token. Detalle: "
              f"{_result.get('error', 'timeout')}. Verificá que "
              f"{REDIRECT_URI} esté autorizado en el OAuth Client.\n")


if __name__ == "__main__":
    main()
