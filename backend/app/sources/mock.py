"""AllPrice — 演示/测试数据源（Mock）

用途：
1. 前端开发时后端不依赖真实网络
2. 演示完整比价流程（多平台、优惠叠加、走势）
3. 测试环境注入（替代真实数据源）

Mock 数据源生成"看起来真实"的多平台商品数据，包含：
- 商品广告语/卖点/评分/评价数（看板头部信息）
- 多平台差异化店铺名/销量/参数
- 万能兜底：任意关键词都有结果（模板未命中按关键词生成）
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Optional

from ..models import Coupon, PlatformOffer, Product
from .base import BaseSource

# 品牌 → (品牌广告语风格, 卖点)
_BRAND_META = {
    "Apple": ("科技美学，极致体验", "A17 Pro 芯片 · 钛金属边框 · 4800万像素三摄"),
    "华为": ("遥遥领先，再创巅峰", "卫星通信 · 昆仑玻璃 · 影像旗舰"),
    "小米": ("感动人心，价格厚道", "骁龙旗舰 · 徕卡光学 · 120W秒充"),
    "索尼": ("为音质而生", "HD降噪处理器 · 30小时续航 · 双芯驱动"),
    "戴森": ("科技改变生活", "V15激光探测 · 260AW吸力 · 全机过滤"),
}

# 平台 → 店铺名模板（旗舰店/自营等）
_SHOP_TMPL = {
    "jd": ["京东自营旗舰店", "{brand}官方旗舰店", "{brand}京东自营"],
    "taobao": ["{brand}天猫官方旗舰店", "{brand}官方旗舰店", "天猫超市"],
    "pdd": ["{brand}品牌专卖店", "{brand}百亿补贴专营店", "{brand}官方补贴店"],
}


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
        """按关键词匹配模板；模板未命中时生成泛化兜底商品（保证前端不空白）

        limit 语义：控制匹配的商品模板数（每个模板固定出京东/淘宝/拼多多
        3 平台报价，保证比价看板有完整平台对比）。
        """
        kw = keyword.strip().lower()
        offers: list[PlatformOffer] = []
        matched = False
        tmpl_count = 0
        for brand, model, cap, colors, base in self.TEMPLATES:
            if kw and kw not in (brand.lower() + model.lower()):
                continue
            matched = True
            # 同一模板 = 同一个 SKU 变体（颜色固定），保证三平台指纹一致可合并
            color = self._rng.choice(colors)
            title = f"{brand} {model}" + (f" {cap}G" if cap else "") + f" {color}"
            params = self._specs_for(brand, model, cap, color)
            for platform, label, ratio in (
                ("jd", "京东", 1.0),
                ("taobao", "淘宝", 0.97),
                ("pdd", "拼多多", 0.92),
            ):
                price = round(base * ratio * self._rng.uniform(0.98, 1.02), 2)
                list_price = round(price * 1.18, 2)
                offers.append(self._make_offer(
                    platform, label, title, price, list_price, params, brand,
                ))
            tmpl_count += 1
            if tmpl_count >= max(1, limit // 3 + (1 if limit % 3 else 0)):
                break

        # 模板未命中 → 生成基于关键词的泛化商品（保证兜底，标注演示）
        if not matched or not offers:
            base = 3000 + self._rng.randint(0, 6000)
            title = f"{keyword.strip()} 官方旗舰版"
            params = {"品牌": "演示", "型号": keyword.strip()}
            for platform, label, ratio in (
                ("jd", "京东", 1.0),
                ("taobao", "淘宝", 0.97),
                ("pdd", "拼多多", 0.92),
            ):
                price = round(base * ratio * self._rng.uniform(0.98, 1.02), 2)
                list_price = round(price * 1.18, 2)
                offers.append(self._make_offer(
                    platform, label, title, price, list_price, params, "演示",
                ))
        return offers

    @staticmethod
    def enrich_product(product: Product) -> Product:
        """补充商品级信息（广告语/卖点/评分/评价数/主图）——看板头部数据"""
        brand = (product.brand or "演示").capitalize()
        slogan, desc = _BRAND_META.get(brand, ("品质之选，值得信赖", "旗舰配置 · 口碑之选 · 官方正品"))
        product.ad_slogan = slogan
        product.description = desc
        rng = random.Random(abs(hash(product.sku_fingerprint)) % 100000)
        product.rating = round(rng.uniform(4.2, 4.9), 1)
        product.review_count = rng.randint(2000, 98000)
        if not product.image_url:
            product.image_url = MockSource._gen_image(brand, product.model or product.name)
        return product

    @staticmethod
    def _gen_image(brand: str, model: str) -> str:
        """生成内嵌 SVG 商品图（零外部依赖，任意环境可显示）"""
        import base64
        color = "#5B7FBE" if brand.lower() == "apple" else "#C0392B" if brand.lower() == "华为" else "#3A7D44"
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="{color}" stop-opacity="0.85"/><stop offset="1" stop-color="#1a1a2e"/>
</linearGradient></defs>
<rect width="600" height="600" rx="40" fill="url(#g)"/>
<rect x="90" y="90" width="420" height="420" rx="30" fill="rgba(255,255,255,0.12)" stroke="rgba(255,255,255,0.35)" stroke-width="3"/>
<text x="300" y="330" font-size="90" text-anchor="middle" font-family="Arial, sans-serif" font-weight="bold" fill="white">{brand[:1]}</text>
<text x="300" y="400" font-size="28" text-anchor="middle" font-family="Arial, sans-serif" fill="rgba(255,255,255,0.9)">{model[:14]}</text>
</svg>'''
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode()

    @staticmethod
    def _specs_for(brand: str, model: str, cap: int, color: str) -> dict:
        specs = {"品牌": brand, "型号": model, "颜色": color}
        if cap:
            specs["存储容量"] = f"{cap}GB"
        if brand == "Apple" and "Pro" in model:
            specs.update({"屏幕": "6.1英寸 OLED 120Hz", "芯片": "A17 Pro", "摄像头": "4800万像素三摄", "电池": "3274mAh", "重量": "187g", "系统": "iOS 17"})
        elif brand == "Apple":
            specs.update({"屏幕": "6.1英寸 OLED", "芯片": "A16", "摄像头": "4800万像素双摄", "电池": "3349mAh", "重量": "171g", "系统": "iOS 17"})
        elif brand == "华为":
            specs.update({"屏幕": "6.82英寸 OLED 120Hz", "芯片": "麒麟9000S", "摄像头": "5000万像素三摄", "电池": "5000mAh", "重量": "225g", "系统": "HarmonyOS 4"})
        elif brand == "小米":
            specs.update({"屏幕": "6.36英寸 OLED 120Hz", "芯片": "骁龙8 Gen3", "摄像头": "5000万像素徕卡三摄", "电池": "4610mAh", "重量": "193g", "系统": "HyperOS"})
        elif brand == "索尼":
            specs.update({"降噪": "HD降噪处理器QN1", "续航": "30小时", "蓝牙": "5.3", "驱动": "30mm动圈"})
        elif brand == "戴森":
            specs.update({"吸力": "260AW", "续航": "60分钟", "过滤": "HEPA全机过滤", "探测": "激光探测"})
        return specs

    def _make_offer(self, platform, label, title, price, list_price, params, brand) -> PlatformOffer:
        coupons = self._coupons_for(platform, price)
        final, detail = self._finalize(price, coupons)
        shops = _SHOP_TMPL.get(platform, ["官方旗舰店"])
        shop = self._rng.choice(shops).format(brand=brand)
        return PlatformOffer(
            platform=platform, platform_label=label,
            product_id=f"{platform}-{title}-{int(price)}",
            url=f"https://example.com/{platform}/{title}",
            title=title, image_url="",
            shop_name=shop,
            list_price=list_price, sale_price=price,
            final_price=final, price_detail=detail,
            params=params, coupons=coupons,
            sales=f"{self._rng.randint(100, 99999)}+",
            stock=self._rng.random() > 0.1,
            ship_fee=0.0 if platform in ("pdd", "taobao") else self._rng.choice([0, 6, 8]),
            fetched_at=datetime.utcnow(),
        )

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

    @staticmethod
    def gen_history(best_price: float, days: int = 90, seed: int = 7) -> list[dict]:
        """生成模拟 90 天价格历史（真实数据不足时前端/接口用于走势图）

        形状：缓慢下行 + 双 11/618 式大促低谷 + 波动回升
        """
        rng = random.Random(seed)
        points = []
        now = datetime.utcnow()
        v = best_price * 1.08
        for i in range(days, -1, -1):
            d = now - timedelta(days=i)
            # 周期性大促（每 30 天一次低谷 -12%）
            promo = -0.12 if (i % 30) in (28, 29, 0) else 0
            # 缓步下行 + 噪声
            drift = -0.0003 * (days - i)
            noise = rng.uniform(-0.015, 0.015)
            v = best_price * (1.08 + drift + promo + noise)
            points.append({
                "date": d.strftime("%m-%d"),
                "price": round(max(best_price * 0.85, v), 2),
            })
        return points
