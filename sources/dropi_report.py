"""
Scraper de Dropi (instancia Chile) con Playwright async.

API pública:
    download_orders_report(date_from, date_to, progress_cb) -> Path

Flujo (8 pasos):
    0. Login fresco (sin reusar sesión) — soporta iframe y login multi-paso
    1. Navegar a "Mis Pedidos" (/dashboard/orders) — quita overlays, multilenguaje
    2-3. Abrir modal de filtros + setear rango de fechas (PrimeNG p-calendar)
    4. Dropdown Acciones → "Órdenes con Productos"
    5. Modal de éxito → "Ver reportes"
    6. Polling /reports hasta "Listo"
    7. Click ícono de descarga + capturar el .xlsx

Lecciones aplicadas (ver gotchas #1..#11 del brief).
"""
from __future__ import annotations

import asyncio
import random
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from playwright.async_api import async_playwright

import config

ProgressCb = Callable[[str], None] | None

# ── Polling del reporte ──────────────────────────────────────
POLL_INTERVAL_S = 20
POLL_MAX_ATTEMPTS = 12

# Meses inglés + español (headless puede renderizar en EN — gotcha #2/#3)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12,
}

_VIEWPORTS = [
    {"width": 1366, "height": 768}, {"width": 1440, "height": 900},
    {"width": 1536, "height": 864}, {"width": 1920, "height": 1080},
]
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def random_viewport() -> dict:
    return random.choice(_VIEWPORTS)


def random_user_agent() -> str:
    return random.choice(_USER_AGENTS)


async def delay(a_ms: int, b_ms: int) -> None:
    await asyncio.sleep(random.uniform(a_ms / 1000, b_ms / 1000))


async def _save_debug(page, name: str) -> None:
    """Screenshot + HTML antes de cada raise crítico (gotcha #11)."""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        png = config.DEBUG_DIR / f"{ts}_{name}.png"
        await page.screenshot(path=str(png), full_page=True)
        html = config.DEBUG_DIR / f"{ts}_{name}.html"
        html.write_text(await page.content(), encoding="utf-8", errors="ignore")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# API pública (sync wrapper)
# ─────────────────────────────────────────────────────────────
def download_orders_report(
    date_from: date,
    date_to: date,
    progress_cb: ProgressCb = None,
) -> Path:
    """Sync wrapper. Devuelve Path al .xlsx en ~/Downloads. Raises si falla."""
    return asyncio.run(_download_async(date_from, date_to, progress_cb))


async def _download_async(date_from, date_to, progress_cb) -> Path:
    def n(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    n(f"Iniciando scraper Dropi · rango {date_from} → {date_to}")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            accept_downloads=True,
            viewport=random_viewport(),
            user_agent=random_user_agent(),
            locale="es-CL",
        )
        page = await ctx.new_page()
        # Stealth ligero: ocultar webdriver
        await page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        try:
            await _login_fresh(page, n)
            await _navigate_to_orders(page, n)
            await _open_and_apply_filters(page, date_from, date_to, n)
            await _trigger_product_report(page, n)
            await _click_ver_reportes(page, n)
            await _wait_for_report_ready(page, n)
            return await _download_first_report(page, n)
        except Exception as e:
            await _save_debug(page, "fatal")
            n(f"ERROR: {e}")
            n(traceback.format_exc().splitlines()[-1])
            raise
        finally:
            await browser.close()


# ─────────────────────────────────────────────────────────────
# PASO 0 — Login fresco
# ─────────────────────────────────────────────────────────────
async def _login_fresh(page, n):
    email = config.require("DROPI_EMAIL")
    password = config.require("DROPI_PASSWORD")
    n("PASO 0 · login en Dropi…")

    await page.goto(config.LOGIN_URL, wait_until="networkidle", timeout=25000)
    await delay(2500, 4000)

    async def find_target():
        for frame in page.frames[1:]:
            if not frame.url or frame.url == "about:blank":
                continue
            try:
                if await frame.locator("input").count() > 0:
                    return frame
            except Exception:
                continue
        return page

    target = await find_target()

    # — Email — múltiples fallbacks de selector
    for sel in [
        "input[type='email']", "input[name='email']",
        "input[placeholder*='mail' i]", "input[placeholder*='correo' i]",
        "input[autocomplete='email']", "input[autocomplete='username']",
        "input:not([type='password']):not([type='hidden'])"
        ":not([type='checkbox']):not([type='radio'])",
    ]:
        try:
            el = target.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await el.fill("")
                await el.press_sequentially(email, delay=60)
                break
        except Exception:
            continue

    # — ¿Multi-paso? si no hay password visible → clickear Continuar —
    pass_visible = False
    for sel in ["input[type='password']", "input[name='password']"]:
        try:
            el = target.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                pass_visible = True
                break
        except Exception:
            continue
    if not pass_visible:
        for sel in [
            "button[type='submit']", "button:has-text('Continuar')",
            "button:has-text('Continue')", "button:has-text('Next')",
        ]:
            try:
                el = target.locator(sel).first
                if await el.count() > 0 and await el.is_visible():
                    await el.click()
                    break
            except Exception:
                continue
        try:
            await page.wait_for_selector("input[type='password']", timeout=8000)
        except Exception:
            pass
        target = await find_target()

    # — Password —
    for sel in ["input[type='password']", "input[name='password']"]:
        try:
            el = target.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                await el.fill("")
                await el.press_sequentially(password, delay=70)
                break
        except Exception:
            continue

    # — Submit — fallback final: Enter en el password —
    clicked = False
    for sel in [
        "button[type='submit']", "button:has-text('Ingresar')",
        "button:has-text('Iniciar sesión')", "button:has-text('Login')",
        "button:has-text('Entrar')", "button:has-text('Sign in')",
        "input[type='submit']",
    ]:
        try:
            el = target.locator(sel).first
            if await el.count() > 0 and await el.is_visible():
                await el.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        try:
            await target.locator("input[type='password']").first.press("Enter")
        except Exception:
            pass

    # Esperar salir de /login o /auth
    try:
        await page.wait_for_url(
            lambda url: "login" not in url.lower() and "auth" not in url.lower(),
            timeout=20000,
        )
    except Exception:
        pass

    if "login" in page.url.lower() or "auth" in page.url.lower():
        await _save_debug(page, "login_failed")
        raise RuntimeError(
            f"Login falló — sigo en {page.url}. Revisá credenciales/captcha "
            f"(screenshot en debug/)."
        )
    n(f"  ✓ login OK · {page.url}")


# ─────────────────────────────────────────────────────────────
# PASO 1 — Navegar a "Mis Pedidos" (/dashboard/orders)
# ─────────────────────────────────────────────────────────────
async def _navigate_to_orders(page, n):
    n("PASO 1 · navegando a Mis Pedidos…")

    # 1) Remover overlays flotantes que interceptan clicks (gotcha #2/#3)
    await page.evaluate(
        """() => {
        document.querySelectorAll('[class*="Dropiapper" i]')
            .forEach(el => el.remove());
        document.querySelectorAll('*').forEach(el => {
            const s = getComputedStyle(el);
            if (s.position === 'fixed') {
                const r = el.getBoundingClientRect();
                if (r.width > 250 && r.height > 250 &&
                    r.right > window.innerWidth - 100) {
                    el.remove();
                }
            }
        });
    }"""
    )
    await delay(400, 800)

    # 2) Click directo en el <a> que termine en /dashboard/orders (gotcha #1/#4)
    result = await page.evaluate(
        """() => {
        const candidates = [];
        document.querySelectorAll('a, [routerlink]').forEach(el => {
            const href = el.getAttribute('href') ||
                         el.getAttribute('routerlink') ||
                         el.getAttribute('ng-reflect-router-link') || '';
            if (href.endsWith('/dashboard/orders') ||
                href === '/dashboard/orders') {
                const r = el.getBoundingClientRect();
                if (r.left < 300) candidates.push(el);
            }
        });
        if (candidates.length === 0) return null;
        candidates[0].click();
        return true;
    }"""
    )

    # 3) Si no hay link directo, expandir el padre primero (multilenguaje)
    if not result:
        for name in ["Mis Pedidos", "My Orders"]:
            ok = await page.evaluate(
                """(name) => {
                for (const el of document.querySelectorAll('*')) {
                    if ((el.textContent || '').trim() === name) {
                        const r = el.getBoundingClientRect();
                        if (r.left < 300 && r.height < 80) {
                            el.click(); return true;
                        }
                    }
                }
                return false;
            }""",
                name,
            )
            if ok:
                break
        await delay(1200, 2000)
        await page.evaluate(
            """() => {
            for (const el of document.querySelectorAll('a, [routerlink]')) {
                const href = el.getAttribute('href') ||
                             el.getAttribute('routerlink') || '';
                if (href.endsWith('/dashboard/orders')) { el.click(); return; }
            }
        }"""
        )

    # 4) Validar URL — endsWith('/dashboard/orders') (gotcha #1)
    def is_orders(url: str) -> bool:
        return url.lower().split("?")[0].rstrip("/").endswith("/dashboard/orders")

    try:
        await page.wait_for_url(lambda u: is_orders(u), timeout=12000)
    except Exception:
        pass
    if not is_orders(page.url):
        await _save_debug(page, "no_orders_page")
        raise RuntimeError(f"No llegué a /dashboard/orders. URL: {page.url}")
    n(f"  ✓ en {page.url}")
    await delay(1500, 2500)


# ─────────────────────────────────────────────────────────────
# PASO 2-3 — Modal de filtros + rango de fechas
# ─────────────────────────────────────────────────────────────
async def _open_and_apply_filters(page, date_from: date, date_to: date, n):
    n("PASO 2 · abriendo modal de filtros…")

    # Quitar overlays otra vez por si reaparecieron
    await page.evaluate(
        """() => document.querySelectorAll('[class*="Dropiapper" i]')
            .forEach(el => el.remove())"""
    )

    filter_selectors = [
        "button[title*='iltro' i]",
        "button[aria-label*='iltro' i]",
        "button:has(i.fa-filter)",
        "button:has-text('Filtros')",
        "button:has-text('Filters')",
        "button[class*='success']",
    ]
    opened = False
    for sel in filter_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(force=True)
                opened = True
                break
        except Exception:
            continue
    if not opened:
        await _save_debug(page, "no_filter_button")
        raise RuntimeError("No encontré el botón de filtros (icon-only verde).")
    await delay(1000, 1800)

    # Gotcha #6: "FECHA DE CREADO" es un radio, no un label
    n("PASO 3 · seteando FECHA DE CREADO + rango…")
    for sel in ["#radio_date_created", "input[id*='date_created' i]"]:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                await loc.click(force=True)
                break
        except Exception:
            continue
    await delay(500, 900)

    # Gotcha #7: p-calendar — abrir widget y navegar, no .fill()
    from_input = page.locator(
        "input[placeholder='From'], input[placeholder='Desde'], "
        ".p-calendar input"
    ).first
    until_input = page.locator(
        "input[placeholder='Until'], input[placeholder='Hasta'], "
        ".p-calendar input"
    ).nth(1)

    await _select_date_via_calendar(page, from_input, date_from, n)
    await delay(400, 800)
    await _select_date_via_calendar(page, until_input, date_to, n)
    await delay(400, 800)

    # OK / Aplicar (probar varios textos)
    for sel in [
        "button:has-text('Aplicar')", "button:has-text('Apply')",
        "button:has-text('Aceptar')", "button:has-text('Ok')",
        "button:has-text('OK')", "button:has-text('Filtrar')",
        "button:has-text('Buscar')", "button[type='submit']",
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=3000)
                break
        except Exception:
            continue
    n("  ✓ filtros aplicados")
    await delay(2000, 3500)


async def _select_date_via_calendar(page, input_locator, target_date: date, n):
    """Click input → p-calendar visible → navegar mes con ←/→ → click día."""
    await input_locator.click(timeout=5000)
    await delay(400, 700)
    await page.wait_for_selector(".p-datepicker:visible", timeout=5000)

    for _ in range(120):  # máx ~10 años de navegación
        month_text = (
            await page.locator(
                ".p-datepicker:visible .p-datepicker-month"
            ).first.inner_text()
        ).strip()
        year_text = (
            await page.locator(
                ".p-datepicker:visible .p-datepicker-year"
            ).first.inner_text()
        ).strip()

        cur_month = _MONTHS.get(month_text.lower(), 0)
        try:
            cur_year = int("".join(c for c in year_text if c.isdigit()))
        except ValueError:
            cur_year = target_date.year

        if cur_month == target_date.month and cur_year == target_date.year:
            break

        cur_idx = cur_year * 12 + cur_month
        tgt_idx = target_date.year * 12 + target_date.month
        arrow = ".p-datepicker-next" if cur_idx < tgt_idx else ".p-datepicker-prev"
        await page.locator(f".p-datepicker:visible {arrow}").first.click()
        await delay(120, 220)

    # Gotcha #7: texto EXACTO para no matchear "10" cuando buscás "1"
    day_str = str(target_date.day)
    loc = page.locator(
        ".p-datepicker:visible td:not(.p-datepicker-other-month) > span"
    )
    for i in range(await loc.count()):
        if (await loc.nth(i).inner_text()).strip() == day_str:
            await loc.nth(i).click()
            return
    raise RuntimeError(
        f"No pude clickear el día {day_str} en el calendario "
        f"({target_date.isoformat()})."
    )


# ─────────────────────────────────────────────────────────────
# PASO 4 — Acciones → "Órdenes con Productos"
# ─────────────────────────────────────────────────────────────
async def _trigger_product_report(page, n):
    n("PASO 4 · Acciones → Órdenes con Productos…")
    for txt in ["Acciones", "Actions"]:
        loc = page.locator(
            f"button:has-text('{txt}'), a:has-text('{txt}')"
        ).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(force=True)
                break
        except Exception:
            continue
    await delay(800, 1400)

    clicked = False
    for tgt in ["Órdenes con Productos", "Orders with Products",
                "Ordenes con Productos"]:
        loc = page.locator(f"text={tgt}").first
        try:
            if await loc.count() > 0:
                await loc.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        await _save_debug(page, "no_product_report_option")
        raise RuntimeError("No encontré 'Órdenes con Productos' en Acciones.")
    n("  ✓ generación de reporte disparada")
    await delay(1500, 2500)


# ─────────────────────────────────────────────────────────────
# PASO 5 — Modal de éxito → "Ver reportes"
# ─────────────────────────────────────────────────────────────
async def _click_ver_reportes(page, n):
    n("PASO 5 · Ver reportes…")
    for txt in ["Ver reportes", "View reports", "See reports", "Ver Reportes"]:
        ver = page.locator(f"button:has-text('{txt}'), a:has-text('{txt}')").first
        try:
            if await ver.count() > 0:
                await ver.wait_for(state="visible", timeout=8000)
                await ver.click()
                n("  ✓ navegando a /reports")
                await delay(2000, 3000)
                return
        except Exception:
            continue
    # Fallback: ir directo a la URL de reportes
    n("  (sin botón 'Ver reportes' — voy directo a /reports)")
    await page.goto(config.REPORTS_URL, wait_until="networkidle", timeout=20000)
    await delay(1500, 2500)


# ─────────────────────────────────────────────────────────────
# PASO 6 — Polling hasta "Listo"
# ─────────────────────────────────────────────────────────────
async def _wait_for_report_ready(page, n):
    n(f"PASO 6 · esperando que el reporte esté listo "
      f"(cada {POLL_INTERVAL_S}s, máx {POLL_MAX_ATTEMPTS} intentos)…")
    for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
        try:
            first_row = page.locator("table tbody tr").first
            await first_row.wait_for(state="visible", timeout=10000)
            row_text = (await first_row.inner_text()).lower()
        except Exception:
            row_text = ""

        if any(k in row_text for k in
               ["listo", "ready", "completed", "done", "finalizado"]):
            n(f"  ✓ reporte listo (intento {attempt})")
            return
        if any(k in row_text for k in
               ["error", "failed", "sin coincidencias", "no encontrado"]):
            await _save_debug(page, "report_error_row")
            raise RuntimeError(f"Reporte en error: {row_text[:200]}")

        n(f"  · intento {attempt}/{POLL_MAX_ATTEMPTS} — aún procesando…")
        await asyncio.sleep(POLL_INTERVAL_S)
        try:
            await page.reload(wait_until="networkidle")
        except Exception:
            pass

    await _save_debug(page, "report_timeout")
    raise RuntimeError(
        f"El reporte no quedó listo tras "
        f"{POLL_INTERVAL_S * POLL_MAX_ATTEMPTS}s."
    )


# ─────────────────────────────────────────────────────────────
# PASO 7 — Click ícono de descarga + capturar el archivo
# ─────────────────────────────────────────────────────────────
async def _download_first_report(page, n) -> Path:
    n("PASO 7 · descargando el reporte…")
    first_row = page.locator("table tbody tr").first
    await first_row.wait_for(state="visible", timeout=10000)

    async with page.expect_download(timeout=30000) as info:
        clickables = await first_row.evaluate(
            """(row) => {
            const tds = row.querySelectorAll('td');
            const lastTd = tds[tds.length - 1];
            const out = [];
            for (const el of lastTd.querySelectorAll('*')) {
                const style = getComputedStyle(el);
                const isClickable =
                    style.cursor === 'pointer' ||
                    ['BUTTON','A','SVG','I'].includes(el.tagName) ||
                    /pointer|click|icon/i.test(el.className || '');
                if (isClickable) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        out.push({x:Math.round(r.left), y:Math.round(r.top),
                                  w:Math.round(r.width), h:Math.round(r.height)});
                    }
                }
            }
            return out;
        }"""
        )
        if not clickables:
            await _save_debug(page, "no_download_icon")
            raise RuntimeError("No encontré íconos clickeables en la última celda.")

        # Gotcha #8: ordenar por X, preferir íconos chicos (<100px),
        # clickear el más a la izquierda por coordenadas.
        sorted_c = sorted(clickables, key=lambda c: c["x"])
        small = [c for c in sorted_c if c["w"] < 100 and c["h"] < 100]
        target = (small or sorted_c)[0]
        cx = target["x"] + target["w"] // 2
        cy = target["y"] + target["h"] // 2
        await page.mouse.click(cx, cy)

    download = await info.value
    dest = config.DOWNLOADS_DIR / (
        download.suggested_filename or "dropi_report.xlsx"
    )
    await download.save_as(str(dest))
    n(f"  ✓ archivo descargado: {dest}")
    return dest
