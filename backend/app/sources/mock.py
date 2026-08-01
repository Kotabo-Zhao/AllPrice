"""AllPrice — 演示/测试数据源（Mock）

用途：
1. 前端开发时后端不依赖真实网络
2. 演示完整比价流程（多平台、优惠叠加、走势）
3. 测试环境注入（替代真实数据源）

Mock 数据源生成"看起来真实"的多平台商品数据。
"""
from __future__ import annotations

import random
from typing import Optional

from ..models import Coupon, PlatformOffer
from .base import BaseSource


class MockSource(BaseSource):
    """模拟数据源：生成京东/淘宝/拼多多三平台的价格+优惠数据"""

    platform = "mock"
    platform_label = "演示数据"

    # 常见商品模板（品牌+型号 → 不同平台不同价）
    TEMPLATES = [
        ("Apple", "iPhone 15 Pro", 256, ["原色钛金属", "蓝色钛金属"], 7999),
        ("Apple", "iPhone 15", 128, ["黑色", "蓝色", "粉色"], 5999),
        ("华为", "Mate 60 Pro", 512, ["雅丹黑", "白沙银"], 6999),
        ("小米", "14 Pro", 256, ["黑色", "白色", "岩石青"], 4999),
        ("索尼", "WH-1000XM5", 0, ["黑色", "银色"], 2899),
        ("戴森", "V15 Detect", 0, ["镍色"], 4990),
    ]

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)

    def search(self, keyword: str, limit: int = 10) -> list[PlatformOffer]:
        """按关键词匹配模板，返回模拟报价（含优惠券）"""
        kw = keyword.lower()
        offers: list[PlatformOffer] = []
        for brand, model, cap, colors, base in self.TEMPLATES:
            if kw and kw not in (brand.lower() + model.lower()):
                continue
            for platform, label, ratio in (
                ("jd", "京东", 1.0),
                ("taobao", "淘宝", 0.97),
                ("pdd", "拼多多", 0.92),
            ):
                price = round(base * ratio * self._rng.uniform(0.98, 1.02), 2)
                list_price = round(price * 1.15, 2)
                color = self._rng.choice(colors)
                title = f"{brand} {model}" + (f" {cap}G" if cap else "") + f" {color}"
                params = {"品牌": brand, "型号": model}
                if cap:
                    params["内存"] = f"{cap}G"
                params["颜色"] = color
                coupons = self._coupons_for(platform, price)
                final, detail = self._finalize(price, coupons)
                offers.append(PlatformOffer(
                    platform=platform, platform_label=label,
                    product_id=f"{platform}-{brand}-{model}-{color}-{int(price)}",
                    url=f"https://example.com/{platform}/{brand}/{model}",
                    title=title, image_url="",
                    list_price=list_price, sale_price=price,
                    final_price=final, price_detail=detail,
                    params=params, coupons=coupons,
                    sales=f"{self._rng.randint(100, 99999)}+",
                    fetched_at=__import__("datetime").datetime.utcnow(),
                ))
                if len(offers) >= limit:
                    break
            if len(offers) >= limit:
                break
        return offers

    @staticmethod
    def _coupons_for(platform: str, price: float) -> list[Coupon]:
        """按平台生成模拟优惠券"""
        coupons = []
        if platform == "jd":
            coupons.append(Coupon(kind="platform_coupon", label="满1000减100",
                                  threshold=1000, discount=100))
            coupons.append(Coupon(kind="shop_coupon", label="店铺满500减30",
                                  threshold=500, discount=30))
        elif platform == "taobao":
            coupons.append(Coupon(kind="platform_coupon", label="满500减50",
                                  threshold=500, discount=50))
        elif platform == "pdd":
            coupons.append(Coupon(kind="subsidy", label="百亿补贴直降",
                                  threshold=0, discount=price * 0.05))
        return coupons

    @staticmethod
    def _finalize(price: float, coupons: list[Coupon]) -> tuple[float, str]:
        """模拟优惠计算：满减券直接减（简化）"""
        total = price
        parts = [f"标价{price:g}元"]
        for c in coupons:
            if total >= c.threshold:
                total -= c.discount
                parts.append(c.label)
        parts.append(f"到手 {total:g}元")
        return round(max(0, total), 2), " → ".join(parts)
