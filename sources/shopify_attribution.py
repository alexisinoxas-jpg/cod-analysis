"""
Enriquecimiento de atribución vía Shopify (tienda COD · Noxa Store).

Dado un set de `ID DE ORDEN DE TIENDA` (= order id de Shopify) del reporte
Dropi, trae de la Admin API: landing_site / referring_site / source_name /
note_attributes / tags, y clasifica cada orden en:
    'facebook' | 'tiktok' | 'unattributed'
leyendo utm_source (y señales equivalentes: fbclid/ttclid, dominios, etc.).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import parse_qs, urlparse

import requests

import config

_FB_TOKENS = ("facebook", "fb", "ig", "instagram", "meta", "fbclid", "facebook.com",
              "instagram.com", "an")  # 'an' = audience network
_TT_TOKENS = ("tiktok", "tt", "ttclid", "tiktok.com", "bytedance")


@dataclass
class ShopifyAttribution:
    by_order: dict[str, str] = field(default_factory=dict)   # shop_order_id → plat
    utm_by_order: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fetched: int = 0

    def platform_for(self, shop_order_id) -> str:
        return self.by_order.get(str(shop_order_id or "").strip(), "unattributed")


def _classify(landing: str, referring: str, source_name: str,
              note_attrs: list, tags: str) -> tuple[str, str]:
    """Devuelve (plataforma, utm_source_detectado)."""
    utm_source = ""
    for url in (landing, referring):
        if not url:
            continue
        try:
            q = parse_qs(urlparse(url).query)
        except Exception:
            q = {}
        for k in ("utm_source", "utm_medium", "utm_campaign"):
            if k in q and q[k]:
                if k == "utm_source":
                    utm_source = q[k][0].lower()

    na_blob = ""
    for na in note_attrs or []:
        try:
            name = str(na.get("name", "")).lower()
            val = str(na.get("value", "")).lower()
        except AttributeError:
            continue
        na_blob += f" {name}={val}"
        if name in ("utm_source", "utm-source") and not utm_source:
            utm_source = val

    blob = " ".join(filter(None, [
        (landing or "").lower(), (referring or "").lower(),
        (source_name or "").lower(), na_blob, (tags or "").lower(),
        f"utm_source={utm_source}" if utm_source else "",
    ]))

    if utm_source:
        if any(t == utm_source or t in utm_source for t in _TT_TOKENS):
            return "tiktok", utm_source
        if any(t == utm_source or t in utm_source for t in _FB_TOKENS):
            return "facebook", utm_source

    if re.search(r"ttclid|tiktok", blob):
        return "tiktok", utm_source or "tiktok?"
    if re.search(r"fbclid|facebook|instagram|\bmeta\b", blob):
        return "facebook", utm_source or "facebook?"
    return "unattributed", utm_source


def fetch_attribution(shop_order_ids: list[str]) -> ShopifyAttribution:
    out = ShopifyAttribution()
    store = config.SHOPIFY_STORE_COD
    token = config.SHOPIFY_ACCESS_TOKEN_COD
    ids = sorted({str(i).strip() for i in shop_order_ids if str(i or "").strip()})
    if not store or not token:
        out.warnings.append(
            "Sin SHOPIFY_STORE_COD / SHOPIFY_ACCESS_TOKEN_COD — "
            "todas las órdenes quedan 'sin atribuir'."
        )
        return out
    if not ids:
        return out
    if not store.startswith("http"):
        store = f"https://{store}"

    base = f"{store}/admin/api/{config.SHOPIFY_API_VERSION}/orders.json"
    headers = {"X-Shopify-Access-Token": token}
    fields = ("id,name,landing_site,referring_site,source_name,"
              "note_attributes,tags")

    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        try:
            r = requests.get(
                base,
                params={"ids": ",".join(chunk), "status": "any",
                        "limit": 250, "fields": fields},
                headers=headers, timeout=60,
            )
            if r.status_code == 401:
                out.warnings.append(
                    "Shopify 401: token inválido o sin scope read_orders.")
                return out
            data = r.json()
            if "errors" in data:
                out.warnings.append(f"Shopify error: {data['errors']}")
                return out
            for o in data.get("orders", []):
                oid = str(o.get("id", ""))
                plat, utm = _classify(
                    o.get("landing_site", ""), o.get("referring_site", ""),
                    o.get("source_name", ""), o.get("note_attributes", []),
                    o.get("tags", ""),
                )
                out.by_order[oid] = plat
                out.utm_by_order[oid] = utm
                out.fetched += 1
        except requests.RequestException as e:
            out.warnings.append(f"Fallo de red Shopify: {e}")
            return out

    missing = len(ids) - out.fetched
    if missing > 0:
        out.warnings.append(
            f"{missing}/{len(ids)} órdenes Shopify no encontradas "
            f"(quedan sin atribuir)."
        )
    return out


# ─────────────────────────────────────────────────────────────────────────
# Funnel: traer TODAS las órdenes Shopify del rango con line items.
# Permite ver Meta → Shopify → Dropi creadas → Dropi entregadas.
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class ShopifyLineItem:
    product_id: str
    title: str
    quantity: int
    price: float


@dataclass
class ShopifyOrderFull:
    id: str
    total_price: float = 0.0
    landing_site: str = ""
    referring_site: str = ""
    source_name: str = ""
    tags: str = ""
    note_attributes: list = field(default_factory=list)
    line_items: list[ShopifyLineItem] = field(default_factory=list)


@dataclass
class ShopifyOrdersResult:
    orders: dict[str, ShopifyOrderFull] = field(default_factory=dict)
    attribution: dict[str, str] = field(default_factory=dict)
    utm_by_order: dict[str, str] = field(default_factory=dict)
    fetched: int = 0
    warnings: list[str] = field(default_factory=list)

    def platform_for(self, shop_order_id) -> str:
        return self.attribution.get(str(shop_order_id or "").strip(),
                                    "unattributed")


def fetch_orders_in_range(date_from: date, date_to: date,
                          progress_cb=None) -> ShopifyOrdersResult:
    """Trae TODAS las órdenes Shopify creadas en el rango, con line_items."""
    out = ShopifyOrdersResult()
    store = config.SHOPIFY_STORE_COD
    token = config.SHOPIFY_ACCESS_TOKEN_COD
    if not store or not token:
        out.warnings.append(
            "Sin SHOPIFY_STORE_COD / SHOPIFY_ACCESS_TOKEN_COD — funnel "
            "Shopify deshabilitado.")
        return out
    if not store.startswith("http"):
        store = f"https://{store}"
    base = f"{store}/admin/api/{config.SHOPIFY_API_VERSION}/orders.json"
    headers = {"X-Shopify-Access-Token": token}
    fields = ("id,name,landing_site,referring_site,source_name,"
              "note_attributes,tags,total_price,line_items")
    params = {
        "status": "any",
        "created_at_min": f"{date_from.isoformat()}T00:00:00-04:00",
        "created_at_max": f"{date_to.isoformat()}T23:59:59-04:00",
        "limit": 250,
        "fields": fields,
    }
    url = base
    page = 0
    while url:
        page += 1
        try:
            r = requests.get(url, params=params if page == 1 else None,
                             headers=headers, timeout=60)
        except requests.RequestException as e:
            out.warnings.append(f"Fallo de red Shopify (range): {e}")
            return out
        if r.status_code == 401:
            out.warnings.append(
                "Shopify 401: token inválido o sin scope read_orders.")
            return out
        try:
            data = r.json()
        except ValueError:
            out.warnings.append("Respuesta no JSON de Shopify (range).")
            return out
        if "errors" in data:
            out.warnings.append(f"Shopify error: {data['errors']}")
            return out
        for o in data.get("orders", []):
            oid = str(o.get("id", ""))
            order = ShopifyOrderFull(
                id=oid,
                total_price=float(o.get("total_price") or 0),
                landing_site=o.get("landing_site", "") or "",
                referring_site=o.get("referring_site", "") or "",
                source_name=o.get("source_name", "") or "",
                tags=o.get("tags", "") or "",
                note_attributes=o.get("note_attributes", []) or [],
                line_items=[ShopifyLineItem(
                    product_id=str(li.get("product_id") or ""),
                    title=li.get("title") or "",
                    quantity=int(li.get("quantity") or 0),
                    price=float(li.get("price") or 0),
                ) for li in (o.get("line_items") or [])],
            )
            out.orders[oid] = order
            plat, utm = _classify(order.landing_site, order.referring_site,
                                  order.source_name, order.note_attributes,
                                  order.tags)
            out.attribution[oid] = plat
            out.utm_by_order[oid] = utm
            out.fetched += 1
        if progress_cb:
            progress_cb(f"  Shopify (range) página {page}: {out.fetched} órdenes")
        # Paginación vía Link header
        link = r.headers.get("Link") or r.headers.get("link") or ""
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                m = re.search(r"<([^>]+)>", part)
                if m:
                    next_url = m.group(1)
                    break
        url = next_url
    return out


def check_shopify_access() -> tuple[bool, str]:
    """Smoke test: ¿las creds Shopify de la tienda COD funcionan?"""
    store = config.SHOPIFY_STORE_COD
    token = config.SHOPIFY_ACCESS_TOKEN_COD
    if not store or not token:
        return False, "Faltan SHOPIFY_STORE_COD / SHOPIFY_ACCESS_TOKEN_COD."
    if not store.startswith("http"):
        store = f"https://{store}"
    try:
        r = requests.get(
            f"{store}/admin/api/{config.SHOPIFY_API_VERSION}/shop.json",
            headers={"X-Shopify-Access-Token": token}, timeout=30,
        )
        if r.status_code == 401:
            return False, "Shopify 401 — token inválido o sin scope read_orders."
        d = r.json()
        if "shop" in d:
            return True, f"OK · tienda «{d['shop'].get('name')}»"
        return False, f"Respuesta inesperada: {str(d)[:160]}"
    except requests.RequestException as e:
        return False, f"Fallo de red: {e}"
