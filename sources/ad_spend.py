"""
Cliente Meta Ads API (cuenta de la tienda COD) + atribución de gasto a
producto por convención de nombres de campaña.

Convención (confirmada por el usuario):
    "Guantes Impermeables Invierno - 56858- 13/05/2026"
     └── name ──────────────────┘   └key┘  └── fecha ──┘
`key` (id numérico) es el join principal contra product_id del reporte Dropi;
si no matchea por id, se cae a fuzzy match de `name` vs product_title.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

import requests

import config

FUZZY_THRESHOLD = 0.72


def _appsecret_proof(token: str) -> str | None:
    """SHA256 HMAC(token, app_secret) — protege el token si se filtra."""
    secret = config.META_APP_SECRET_COD
    if not secret or not token:
        return None
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def _meta_api_blocked_msg() -> str:
    return ("Meta API deshabilitada por seguridad (cuenta dev suspendida / "
            "protocolo anti-ban). Usá el gasto MANUAL pegado del Ads Manager.")


@dataclass
class CampaignSpend:
    campaign_id: str
    campaign_name: str
    spend: float
    product_key: str = ""    # id numérico parseado del nombre
    product_name: str = ""   # nombre parseado del nombre
    matched: bool = False
    purchases: int = 0       # Meta "Compras" (count del píxel)
    conv_value: float = 0.0  # Meta "Valor de conversión de compras"


@dataclass
class MetaSpendResult:
    by_key: dict[str, float] = field(default_factory=dict)   # id → spend
    by_name: dict[str, float] = field(default_factory=dict)   # norm(name) → spend
    campaigns: list[CampaignSpend] = field(default_factory=list)
    total_spend: float = 0.0
    warnings: list[str] = field(default_factory=list)


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return s.strip()


def _compiled_regex() -> re.Pattern | None:
    pat = config.META_CAMPAIGN_PRODUCT_REGEX
    if not pat:
        return None
    try:
        return re.compile(pat)
    except re.error:
        return None


def _parse_campaign_name(name: str, rx: re.Pattern | None) -> tuple[str, str]:
    """Devuelve (product_key, product_name) o ('','')."""
    if not rx:
        return "", ""
    m = rx.search(name or "")
    if not m:
        return "", ""
    gd = m.groupdict()
    return (gd.get("key", "") or "").strip(), (gd.get("name", "") or "").strip()


def fetch_meta_spend(date_from: date, date_to: date) -> MetaSpendResult:
    """Trae spend a nivel campaña del rango y lo atribuye a producto."""
    res = MetaSpendResult()

    # Gate anti-ban: ninguna llamada viva a Meta salvo opt-in explícito.
    if not config.META_API_ENABLED:
        res.warnings.append(_meta_api_blocked_msg())
        return res

    token = config.META_ACCESS_TOKEN_COD
    acct = config.META_AD_ACCOUNT_ID_COD
    if not token:
        res.warnings.append(
            "Sin META_ACCESS_TOKEN_COD ni fallback en profit-calculator/.env "
            "— spend Meta = 0."
        )
        return res
    if not acct:
        res.warnings.append("Falta META_AD_ACCOUNT_ID_COD — spend Meta = 0.")
        return res
    if not acct.startswith("act_"):
        acct = f"act_{acct}"

    rx = _compiled_regex()
    if rx is None:
        res.warnings.append(
            "META_CAMPAIGN_PRODUCT_REGEX vacío/ inválido — no se puede atribuir "
            "spend por nombre de campaña."
        )

    url = (
        f"https://graph.facebook.com/{config.META_API_VERSION}/{acct}/insights"
    )
    params = {
        "level": "campaign",
        "fields": "campaign_id,campaign_name,spend",
        "time_range": (
            f'{{"since":"{date_from.isoformat()}",'
            f'"until":"{date_to.isoformat()}"}}'
        ),
        "limit": 500,
        "access_token": token,
    }
    proof = _appsecret_proof(token)
    if proof:
        params["appsecret_proof"] = proof

    try:
        while url:
            r = requests.get(url, params=params, timeout=60)
            params = {}  # paging.next ya trae query embebido
            data = r.json()
            if "error" in data:
                err = data["error"]
                res.warnings.append(
                    f"Meta API error ({err.get('code')}): "
                    f"{err.get('message')}"
                )
                return res
            for row in data.get("data", []):
                spend = float(row.get("spend", 0) or 0)
                cname = row.get("campaign_name", "")
                key, pname = _parse_campaign_name(cname, rx)
                cs = CampaignSpend(
                    campaign_id=row.get("campaign_id", ""),
                    campaign_name=cname,
                    spend=spend,
                    product_key=key,
                    product_name=pname,
                    matched=bool(key or pname),
                )
                res.campaigns.append(cs)
                res.total_spend += spend
                if key:
                    res.by_key[key] = res.by_key.get(key, 0.0) + spend
                if pname:
                    nk = normalize(pname)
                    res.by_name[nk] = res.by_name.get(nk, 0.0) + spend
                if not cs.matched:
                    res.warnings.append(
                        f"Campaña sin patrón reconocible: «{cname}» "
                        f"(${spend:.2f} sin atribuir)"
                    )
            url = data.get("paging", {}).get("next")
    except requests.RequestException as e:
        res.warnings.append(f"Fallo de red al consultar Meta API: {e}")

    return res


def attribute_campaigns_to_products(
    meta: MetaSpendResult, products: list[tuple[str, str]]
) -> tuple[dict[str, dict], list[dict]]:
    """
    Push desde el lado de la campaña: para cada campaña, busca el producto
    Dropi al que pertenece. Prioridad:
      0. Override manual (campaign_overrides.json) ← lo que vos forzaste.
      1. ID exacto (key parseado del nombre vs PRODUCTO ID Dropi).
      2. Fuzzy por nombre (campaign_name parseado vs PRODUCTO Dropi).
    Devuelve (spend_por_producto, log_diagnóstico).
    """
    from sources import campaign_overrides
    overrides = campaign_overrides.load_overrides()

    by_product: dict[str, dict] = {}
    log: list[dict] = []
    products_by_id = {str(pid): title for pid, title in products}
    products_by_norm = {normalize(title): (str(pid), title)
                        for pid, title in products}

    for c in meta.campaigns:
        chosen_pid = ""
        chosen_title = ""
        via = "unmatched"
        score = 0.0

        # 0. Override manual del usuario
        ov_key = (c.campaign_name or "").strip().lower()
        if ov_key in overrides:
            ov_pid = str(overrides[ov_key])
            if ov_pid in products_by_id:
                chosen_pid = ov_pid
                chosen_title = products_by_id[ov_pid]
                via = "manual"
                score = 1.0

        if not chosen_pid and c.product_key and c.product_key in products_by_id:
            chosen_pid = c.product_key
            chosen_title = products_by_id[c.product_key]
            via = "id"
            score = 1.0
        elif not chosen_pid and c.product_name:
            target = normalize(c.product_name)
            best = (0.0, "", "")
            for nk, (pid, title) in products_by_norm.items():
                ratio = SequenceMatcher(None, target, nk).ratio()
                if ratio > best[0]:
                    best = (ratio, pid, title)
            if best[0] >= FUZZY_THRESHOLD:
                chosen_pid, chosen_title = best[1], best[2]
                via = "name"
                score = best[0]

        if chosen_pid:
            d = by_product.setdefault(chosen_pid,
                {"spend": 0.0, "purchases": 0, "conv_value": 0.0})
            d["spend"] += c.spend
            d["purchases"] += c.purchases
            d["conv_value"] += c.conv_value

        log.append({
            "campaign": c.campaign_name,
            "spend": round(c.spend, 2),
            "purchases": int(c.purchases),
            "conv_value": round(c.conv_value, 2),
            "campaign_key": c.product_key,
            "campaign_name_parsed": c.product_name,
            "matched_product_id": chosen_pid,
            "matched_product_title": chosen_title,
            "via": via,
            "score": round(score, 3),
        })
    return by_product, log


def attribute_spend_to_product(
    product_id: str, product_title: str, meta: MetaSpendResult
) -> float:
    """spend Meta para un producto: 1) match por id, 2) fuzzy por título."""
    pid = str(product_id or "").strip()
    if pid and pid in meta.by_key:
        return meta.by_key[pid]

    target = normalize(product_title)
    if not target:
        return 0.0
    if target in meta.by_name:
        return meta.by_name[target]

    best_spend, best_ratio = 0.0, 0.0
    for nk, spend in meta.by_name.items():
        ratio = SequenceMatcher(None, target, nk).ratio()
        if ratio > best_ratio:
            best_ratio, best_spend = ratio, spend
    return best_spend if best_ratio >= FUZZY_THRESHOLD else 0.0


def parse_manual_spend(text: str) -> MetaSpendResult:
    """
    Plan B sin API: el usuario pega del Ads Manager líneas
        <nombre de campaña> <tab|;|,|2+ espacios> <gasto>
    p.ej.  "Guantes Impermeables Invierno - 56858 - 13/05/2026   123456"
    Reusa el mismo regex de convención de nombres → atribución por producto.
    """
    res = MetaSpendResult()
    rx = _compiled_regex()
    if rx is None:
        res.warnings.append(
            "META_CAMPAIGN_PRODUCT_REGEX vacío/inválido — el gasto manual "
            "no se puede atribuir a producto.")
    num_re = re.compile(r"[-+]?\d[\d.,]*")
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        # separar nombre | gasto: probar tab / ; / , / 2+ espacios
        parts = re.split(r"\t|;|\s{2,}|,(?=\s*[-+]?\d)", line)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) < 2:
            m = list(num_re.finditer(line))
            if not m:
                res.warnings.append(f"Línea sin gasto, ignorada: «{line[:60]}»")
                continue
            last = m[-1]
            cname, raw_spend = line[:last.start()].strip(), last.group()
        else:
            cname, raw_spend = " ".join(parts[:-1]).strip(), parts[-1]
        s = raw_spend.replace("$", "").replace(" ", "")
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            spend = float(s)
        except ValueError:
            res.warnings.append(f"Gasto no numérico, ignorado: «{line[:60]}»")
            continue
        key, pname = _parse_campaign_name(cname, rx)
        res.campaigns.append(CampaignSpend(
            campaign_id="", campaign_name=cname, spend=spend,
            product_key=key, product_name=pname,
            matched=bool(key or pname)))
        res.total_spend += spend
        if key:
            res.by_key[key] = res.by_key.get(key, 0.0) + spend
        if pname:
            nk = normalize(pname)
            res.by_name[nk] = res.by_name.get(nk, 0.0) + spend
        if not (key or pname):
            res.warnings.append(
                f"Campaña sin patrón reconocible: «{cname}» "
                f"(${spend:,.0f} sin atribuir)")
    if res.campaigns:
        res.warnings.append(
            f"Modo gasto MANUAL: {len(res.campaigns)} campañas, "
            f"${res.total_spend:,.0f} total (sin API Meta).")
    return res


def check_meta_access() -> tuple[bool, str]:
    """Diagnóstico Meta. NO toca la red salvo opt-in explícito (anti-ban)."""
    # Gate: cuenta suspendida / protocolo post-ban → cero requests/probing.
    if not config.META_API_ENABLED:
        return False, ("Modo MANUAL activo — API Meta deshabilitada por "
                       "seguridad (cuenta suspendida). El ROAS sale del gasto "
                       "pegado del Ads Manager. Esto es lo esperado.")

    token = config.META_ACCESS_TOKEN_COD
    acct = config.META_AD_ACCOUNT_ID_COD
    if not token:
        return False, "Sin token Meta."
    if not acct:
        return False, "Falta META_AD_ACCOUNT_ID_COD."
    if not acct.startswith("act_"):
        acct = f"act_{acct}"

    common = {"access_token": token}
    proof = _appsecret_proof(token)
    if proof:
        common["appsecret_proof"] = proof
    try:
        # Guard de scopes: si el token puede MODIFICAR anuncios, abortar.
        perms = requests.get(
            f"https://graph.facebook.com/{config.META_API_VERSION}"
            f"/me/permissions", params=common, timeout=30).json()
        granted = {p["permission"] for p in perms.get("data", [])
                   if p.get("status") == "granted"}
        if "ads_management" in granted:
            return False, ("El token tiene permisos para MODIFICAR anuncios. "
                           "Por seguridad no se usa — generá uno solo de "
                           "lectura (ads_read).")

        r = requests.get(
            f"https://graph.facebook.com/{config.META_API_VERSION}/{acct}",
            params={**common, "fields": "name,account_status"}, timeout=30)
        d = r.json()
        if "error" in d:
            return False, (
                f"El token NO accede a {acct}: {d['error'].get('message')}.")
        return True, (f"OK · cuenta «{d.get('name')}» "
                      f"(status {d.get('account_status')})")
    except requests.RequestException as e:
        return False, f"Fallo de red: {e}"
