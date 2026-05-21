"""Dataclasses del análisis COD."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field


@dataclass
class CodConfig:
    """Parámetros editables vía sliders en la UI."""
    tasa_confirmacion: float = 0.75   # % de órdenes que se confirman
    tasa_entrega: float = 0.70        # % de confirmadas que se entregan
    costo_envio: float = 3.0          # costo de envío por orden confirmada
    gastos_operativos: float = 0.0    # gasto operativo total a prorratear

    @property
    def tasa_efectiva(self) -> float:
        return self.tasa_confirmacion * self.tasa_entrega


@dataclass
class ProductReportRow:
    product_id: str
    product_title: str
    price: float
    cost_per_item: float

    units_sold: int
    revenue_gross: float
    cogs_gross: float
    orders_count: int

    sku: str = ""

    # Outcomes reales (de ESTATUS Dropi)
    orders_delivered: int = 0
    orders_cancelled: int = 0
    orders_returned: int = 0
    orders_pending: int = 0
    orders_pending_stale: int = 0   # pendientes con guía generada hace >3d
    tasa_confirmacion_real: float = 0.0
    tasa_entrega_real: float = 0.0

    # Atribución por plataforma (regla first-line-item del pedido)
    orders_facebook: int = 0
    orders_tiktok: int = 0
    orders_unattrib: int = 0
    revenue_facebook: float = 0.0
    revenue_tiktok: float = 0.0
    revenue_unattrib: float = 0.0
    units_facebook: int = 0
    units_tiktok: int = 0
    units_unattrib: int = 0

    spend_meta: float = 0.0
    spend_tiktok: float = 0.0

    # Meta-reportado (del CSV): si el reporte trae columnas Compras /
    # Valor de conversión, lo agregamos por producto.
    meta_purchases: int = 0
    meta_conv_value: float = 0.0
    roas_meta_reported: float = 0.0    # conv_value / spend (lo que muestra Ads Mgr)

    # Funnel desde Shopify (órdenes que pasaron por Shopify, hayan entrado o
    # no a Dropi). Útil para ver dónde se cae la venta:
    # Meta compras (píxel) → Shopify órdenes → Dropi creadas → Dropi entregadas.
    shopify_orders: int = 0
    shopify_units: int = 0
    shopify_revenue: float = 0.0

    # Benchmarks
    # beroas / roas_10pct = COD-adjusted (sobre revenue REAL entregado)
    # beroas_meta / roas_gross_meta = headline pixel-side (sobre revenue bruto,
    #   asumiendo 100% entrega y sin envío); útil como target Ads Manager.
    beroas: float = 0.0
    roas_10pct: float = 0.0
    beroas_meta: float = 0.0
    roas_gross_meta: float = 0.0

    # Post-COD
    revenue_real: float = 0.0
    cogs_real: float = 0.0
    shipping_total: float = 0.0
    profit_neto: float = 0.0
    margin_pct: float = 0.0

    roas_real_meta: float = 0.0
    roas_real_tiktok: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CodAnalysisResult:
    rows: list[ProductReportRow] = field(default_factory=list)
    config: CodConfig = field(default_factory=CodConfig)
    warnings: list[str] = field(default_factory=list)
    source_filename: str = ""

    # Totales agregados
    total_revenue_real: float = 0.0
    total_cogs_real: float = 0.0
    total_spend: float = 0.0
    total_shipping: float = 0.0
    total_profit_neto: float = 0.0
    total_pending_stale: int = 0

    # Funnel totals (Meta → Shopify → Dropi creadas → Dropi entregadas)
    total_meta_purchases: int = 0
    total_shopify_orders: int = 0
    total_shopify_revenue: float = 0.0
    total_dropi_created: int = 0
    total_dropi_delivered: int = 0
    # Órdenes Shopify cuyos line items no matchearon ningún producto Dropi
    # (la venta vivió en Shopify pero NO se sincronizó a Dropi para fulfillment).
    shopify_no_dropi: dict = field(default_factory=lambda:
                                   {"orders": 0, "units": 0, "revenue": 0.0})

    # Diagnóstico de atribución campaña Meta → producto Dropi
    attribution_log: list[dict] = field(default_factory=list)

    # Detalle de pedidos pendientes con guía generada >3d (para export Excel)
    stale_pending_orders: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rows": [r.to_dict() for r in self.rows],
            "config": asdict(self.config),
            "warnings": self.warnings,
            "source_filename": self.source_filename,
            "totals": {
                "revenue_real": self.total_revenue_real,
                "cogs_real": self.total_cogs_real,
                "spend": self.total_spend,
                "shipping": self.total_shipping,
                "profit_neto": self.total_profit_neto,
                "pending_stale": self.total_pending_stale,
                "meta_purchases": self.total_meta_purchases,
                "shopify_orders": self.total_shopify_orders,
                "shopify_revenue": self.total_shopify_revenue,
                "dropi_created": self.total_dropi_created,
                "dropi_delivered": self.total_dropi_delivered,
            },
            "shopify_no_dropi": self.shopify_no_dropi,
            "attribution_log": self.attribution_log,
            "stale_pending_orders": self.stale_pending_orders,
        }
