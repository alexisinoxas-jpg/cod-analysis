# cod-analysis — Análisis COD (Dropi · tienda Noxa Store)

App Flask **local** (Windows) que automatiza: scrapea Dropi → descarga el
reporte "Órdenes con Productos" del rango → lo procesa con el pipeline COD
(tasas REALES de ESTATUS) → cruza spend Meta + atribución Shopify → tabla por
producto con profit neto, márgenes, BEROAS y ROAS real.

## Correr

```powershell
cd C:\Users\alexi\cod-analysis
.\.venv\Scripts\python app.py
# abrir http://127.0.0.1:5057
```

> En VSCode, seleccioná el intérprete `.venv\Scripts\python.exe`
> (los avisos de "package not installed" son porque apunta al Python global).

## .env (ya creado, NO commitear)

| Var | Estado |
|---|---|
| `DROPI_EMAIL` / `DROPI_PASSWORD` | ✅ cargadas |
| `META_AD_ACCOUNT_ID_COD` | ✅ `act_1577542186081655` |
| `META_ACCESS_TOKEN_COD` | ⛔ **pendiente** — el token reusado de profit-calculator está caducado. Pegá un user-token Meta vivo con acceso a la cuenta COD. |
| `SHOPIFY_STORE_COD` / `SHOPIFY_ACCESS_TOKEN_COD` | ⛔ **pendiente** — sin esto la atribución FB/TikTok queda "sin atribuir". |
| `META_CAMPAIGN_PRODUCT_REGEX` | ✅ patrón `Nombre - id - dd/mm/aaaa` |

Verificá todo con el botón **🩺 Diagnóstico** o `GET /reports/cod/healthcheck`.

## Modelo COD

- **Tasas reales** desde `ESTATUS`: confirmación = (órdenes − CANCELADO)/total;
  entrega = ENTREGADO/confirmadas. Devoluciones y pendientes contados aparte.
- `revenue_real` / `cogs_real` = solo órdenes ENTREGADO. `shipping_total` =
  flete de salida en toda orden no cancelada + flete de devolución en devueltas.
- Sliders (panel manual) = **simulación**: destildá "Usar tasas REALES" para
  proyectar con las fórmulas del brief sobre los brutos.
- `BEROAS` = revenue_real / (margen COD pre-ads); `ROAS 10%` análogo a 10% de
  revenue; `ROAS real Meta` = revenue_real / spend_meta.

## Estado

✅ Scaffold, pipeline COD (validado vs sample real: 36 productos), scraper
Playwright (8 pasos + gotchas), Flask+SSE, UI, Chromium instalado.
⛔ Falta: token Meta vivo + creds Shopify COD + smoke test live del scraper
(apretar el botón con el server corriendo).
