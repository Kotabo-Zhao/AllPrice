"""AllPrice — 数据模型定义

核心领域模型：
- Product: 归一化后的商品（跨平台同一商品合并）
- PlatformOffer: 单平台在售商品（含该平台的价格/优惠）
- PriceSnapshot: 价格历史快照（走势图数据源）
- Coupon/Discount: 优惠券与满减（优惠计算引擎输入）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Coupon:
    """优惠券/满减 — 抽象为统一结构供优惠计算引擎使用"""
    kind: str          # platform_coupon | shop_coupon | full_reduction | member_price | flash_sale | subsidy | cashback
    label: str         # 展示名，如"满300减50"
    threshold: float   # 满 X 元可用（0 = 无门槛）
    discount: float    # 减 Y 元（0 = 不是直接减）
    percent: Optional[float] = None   # 折后比例（0.85 = 85折）
    max_discount: Optional[float] = None  # 百分比折扣上限
    exclusive_group: Optional[str] = None # 互斥组：同组优惠不能同时用（如 秒杀 vs 平台券）
    source: str = ""   # 数据来源（哪个接口/页面）

    def __post_init__(self):
        if self.percent is None and self.discount == 0 and self.threshold == 0:
            raise ValueError(f"Coupon must have discount or percent: {self.label}")


@dataclass
class PlatformOffer:
    """单平台在售商品（比价结果的一行）"""
    platform: str                       # jd | taobao | pdd | xianyu | ...
    platform_label: str                 # 京东 | 淘宝 | 拼多多 | ...
    product_id: str                     # 平台内商品ID
    url: str                            # 跳转链接
    title: str                          # 平台标题
    image_url: str = ""
    list_price: float = 0.0             # 标价/划线价
    sale_price: float = 0.0             # 当前售价（未叠加优惠）
    final_price: float = 0.0            # 最低到手价（优惠计算后）
    price_detail: str = ""              # 优惠叠加明细文本
    coupons: list[Coupon] = field(default_factory=list)
    params: dict = field(default_factory=dict)   # 规格参数（型号/内存/颜色…）
    sales: Optional[str] = None         # 销量文案
    stock: Optional[bool] = None        # 有货?
    ship_fee: float = 0.0               # 运费
    fetched_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Product:
    """归一化商品（跨平台合并后的唯一实体）"""
    sku_fingerprint: str        # 品牌+型号+规格 哈希
    name: str                   # 归一化名称
    brand: str = ""
    model: str = ""
    specs: dict = field(default_factory=dict)  # 规格：内存/容量/颜色…
    image_url: str = ""
    offers: list[PlatformOffer] = field(default_factory=list)

    def best_offer(self) -> Optional[PlatformOffer]:
        """最低到手价的平台"""
        if not self.offers:
            return None
        return min(self.offers, key=lambda o: o.final_price or float('inf'))


@dataclass
class PriceSnapshot:
    """价格历史快照（走势图）"""
    product_id: str
    platform: str
    price: float
    final_price: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
