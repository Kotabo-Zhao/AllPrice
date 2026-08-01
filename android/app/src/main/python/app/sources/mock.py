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
        ("Apple", "MacBook Air 13", 0, ["午夜色", "星光色"], 7999),
        ("华为", "Mate 60 Pro", 512, ["雅丹黑", "白沙银"], 6999),
        ("华为", "MatePad Pro", 0, ["曜金黑", "宣白"], 4699),
        ("华为", "FreeBuds Pro 3", 0, ["冰霜银", "雅川青"], 1499),
        ("小米", "14 Pro", 256, ["黑色", "白色", "岩石青"], 4999),
        ("小米", "Redmi K70", 256, ["墨羽", "晴雪", "竹月蓝"], 2499),
        ("小米", "Watch S3", 0, ["曜石黑", "象牙白"], 1099),
        ("索尼", "WH-1000XM5", 0, ["黑色", "银色"], 2899),
        ("索尼", "WF-1000XM5", 0, ["黑色", "铂金银"], 1999),
        ("戴森", "V15 Detect", 0, ["镍色"], 4990),
        ("耐克", "Air Force 1", 0, ["白色", "黑色", "小麦色"], 799),
        ("耐克", "Dunk Low", 0, ["黑白", "熊猫", "灰白"], 749),
        ("耐克", "Pegasus 41 跑鞋", 0, ["荧光绿", "黑武士", "白红"], 899),
        ("耐克", "Air Max 270", 0, ["黑武士", "白蓝", "灰红"], 1099),
        ("阿迪达斯", "Ultraboost 跑步鞋", 0, ["白黑", "碳灰", "荧光黄"], 999),
        ("阿迪达斯", "三叶草贝壳头", 0, ["白黑", "白蓝", "全白"], 899),
        ("李宁", "中国李宁 悟道", 0, ["白黑", "米白", "蓝灰"], 699),
        ("李宁", "赤兔 7 Pro 跑鞋", 0, ["荧光绿", "黑武士"], 549),
    ]

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)

    def search(self, keyword: str, limit: int = 10) -> list[PlatformOffer]:
        """按关键词匹配模板；模板未命中时生成泛化兜底商品（保证前端不空白）

        每个模板生成多个规格变体（不同颜色/容量 → 不同价格），
        同变体出京东/淘宝/拼多多 3 平台报价（指纹一致可合并）。
        """
        kw = keyword.strip().lower()
        offers: list[PlatformOffer] = []
        matched = False
        tmpl_count = 0
        # 品牌词（命中多个模板）应全出；具体词（命中1个模板）也保证出全
        max_templates = max(1, limit // 6 + (1 if limit % 6 else 0))
        max_templates = max(max_templates, 4)  # 品牌词至少展示 4 款单品
        for brand, model, cap, colors, base in self.TEMPLATES:
            if kw and kw not in (brand.lower() + model.lower()):
                continue
            matched = True
            # 生成 2 个规格变体（不同颜色，容量档位价格不同）
            variant_colors = self._rng.sample(colors, min(2, len(colors)))
            for vi, color in enumerate(variant_colors):
                # 容量档位：高配价差 +8%
                vcap = cap
                vbase = base * (1.08 if vi > 0 and cap else 1.0)
                title = f"{brand} {model}" + (f" {vcap}G" if vcap else "") + f" {color}"
                params = self._specs_for(brand, model, vcap, color)
                for platform, label, ratio in (
                    ("jd", "京东", 1.0),
                    ("taobao", "淘宝", 0.97),
                    ("pdd", "拼多多", 0.92),
                ):
                    price = round(vbase * ratio * self._rng.uniform(0.98, 1.02), 2)
                    list_price = round(price * 1.18, 2)
                    offers.append(self._make_offer(
                        platform, label, title, price, list_price, params, brand,
                    ))
            tmpl_count += 1
            if tmpl_count >= max_templates:
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
        """生成内嵌 SVG 商品图（零外部依赖，任意环境可显示）

        视觉：品牌色渐变场景 + 产品剪影（鞋/手机/耳机/手表/平板/笔记本）+ 展示台
        """
        import base64
        brand_l = brand.lower()
        color = ("#5B7FBE" if brand_l == "apple" else "#C0392B" if "华为" in brand
                 else "#3A7D44" if "小米" in brand
                 else "#1E4E8C" if brand in ("耐克", "阿迪达斯", "李宁")
                 else "#4A4A6A")
        m = (model or "").lower()
        # 品牌优先分类，避免 "Air"→笔记本、"Mate"→手机 的误命中
        if brand in ("耐克", "阿迪达斯", "李宁") or any(k in m for k in ("跑鞋", "鞋", "sneaker", "dunk", "air max")):
            # 运动鞋侧影：鞋底 + 鞋面 + Swoosh + 鞋带区
            device = (
                # 展示台阴影
                '<ellipse cx="300" cy="468" rx="205" ry="16" fill="rgba(0,0,0,0.30)"/>'
                # 鞋底
                '<path d="M138 398 q95 32 192 26 q108 -7 150 -32 l-4 -24 q-58 34 -152 32 q-100 -3 -182 -24 z" '
                'fill="rgba(255,255,255,0.94)"/>'
                '<path d="M138 398 q95 32 192 26 q108 -7 150 -32" fill="none" stroke="rgba(0,0,0,0.12)" stroke-width="3"/>'
                # 鞋身
                '<path d="M142 390 q-8 -78 58 -108 q64 -30 128 -12 q66 16 110 64 q32 36 34 78 q-4 28 -52 20 '
                'q-118 -26 -206 8 q-50 14 -58 -12 q-6 -16 -14 -38 z" fill="url(#shoe)"/>'
                # 鞋带区
                '<path d="M178 282 q22 -6 44 -4 l26 34 q-24 10 -52 6 q-14 -2 -20 -6 z" fill="rgba(255,255,255,0.35)"/>'
                # Swoosh 勾
                '<path d="M236 322 q92 -34 158 -40 q40 -4 56 6 q-36 2 -72 16 q-64 22 -128 26 q-12 1 -16 -4 z" '
                'fill="rgba(255,255,255,0.92)"/>'
                # 鞋领
                '<path d="M322 280 q30 -2 52 14 q24 16 34 42 l-14 4 q-12 -26 -36 -38 q-24 -12 -44 -8 z" '
                'fill="rgba(255,255,255,0.30)"/>'
            )
        elif brand == "Apple" and "macbook" in m:
            # 笔记本
            device = ('<rect x="140" y="140" width="320" height="210" rx="14" fill="rgba(255,255,255,0.9)"/>'
                      '<rect x="158" y="158" width="284" height="176" rx="8" fill="url(#screen)"/>'
                      '<path d="M150 350 q150 26 300 0 l-6 34 q-144 24 -288 0 z" fill="rgba(255,255,255,0.85)"/>')
        elif any(k in m for k in ("matepad", "pad", "平板")):
            # 平板
            device = ('<rect x="150" y="130" width="300" height="400" rx="18" fill="rgba(255,255,255,0.9)"/>'
                      '<rect x="170" y="150" width="260" height="340" rx="10" fill="url(#screen)"/>'
                      '<circle cx="300" cy="510" r="5" fill="rgba(255,255,255,0.5)"/>')
        elif any(k in m for k in ("watch", "手表", "s3")):
            # 智能手表
            device = ('<rect x="218" y="150" width="164" height="44" rx="14" fill="rgba(255,255,255,0.78)"/>'
                      '<rect x="218" y="406" width="164" height="44" rx="14" fill="rgba(255,255,255,0.78)"/>'
                      '<circle cx="300" cy="300" r="96" fill="url(#watchFace)"/>'
                      '<circle cx="300" cy="300" r="96" fill="none" stroke="rgba(255,255,255,0.9)" stroke-width="14"/>')
        elif any(k in m for k in ("iphone", "phone", "手机", "redmi", "14 pro")):
            # 手机
            device = ('<rect x="225" y="120" width="150" height="300" rx="26" fill="rgba(255,255,255,0.92)"/>'
                      '<rect x="245" y="140" width="110" height="240" rx="12" fill="url(#screen)"/>'
                      '<circle cx="300" cy="392" r="8" fill="rgba(0,0,0,0.35)"/>')
        elif any(k in m for k in ("wh", "xm", "freebuds", "耳机", "headphone")):
            # 头戴/入耳耳机
            device = ('<path d="M170 300 h60 a70 70 0 0 1 140 0 h60" stroke="rgba(255,255,255,0.92)" '
                      'stroke-width="20" fill="none" stroke-linecap="round"/>'
                      '<rect x="150" y="290" width="45" height="130" rx="18" fill="rgba(255,255,255,0.85)"/>'
                      '<rect x="405" y="290" width="45" height="130" rx="18" fill="rgba(255,255,255,0.85)"/>')
        elif any(k in m for k in ("v15", "dyson", "吸尘", "detect")):
            # 吸尘器
            device = ('<rect x="265" y="140" width="70" height="200" rx="12" fill="rgba(255,255,255,0.9)"/>'
                      '<rect x="255" y="320" width="90" height="30" rx="14" fill="rgba(255,255,255,0.9)"/>'
                      '<rect x="265" y="340" width="26" height="90" rx="10" fill="rgba(255,255,255,0.75)"/>'
                      '<rect x="309" y="340" width="26" height="90" rx="10" fill="rgba(255,255,255,0.75)"/>')
        else:
            # 华为 Mate 系列等 → 手机；其他 → 通用盒子
            if "mate" in m:
                device = ('<rect x="225" y="120" width="150" height="300" rx="26" fill="rgba(255,255,255,0.92)"/>'
                          '<rect x="245" y="140" width="110" height="240" rx="12" fill="url(#screen)"/>'
                          '<circle cx="300" cy="392" r="8" fill="rgba(0,0,0,0.35)"/>')
            else:
                device = ('<ellipse cx="300" cy="462" rx="180" ry="14" fill="rgba(0,0,0,0.28)"/>'
                          '<rect x="230" y="160" width="140" height="220" rx="18" fill="rgba(255,255,255,0.9)"/>')
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600">
<defs>
<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="{color}"/><stop offset="0.55" stop-color="{color}" stop-opacity="0.78"/>
<stop offset="1" stop-color="#141826"/>
</linearGradient>
<linearGradient id="screen" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#2c3852"/><stop offset="1" stop-color="#141826"/>
</linearGradient>
<linearGradient id="shoe" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="rgba(255,255,255,0.95)"/><stop offset="1" stop-color="rgba(255,255,255,0.55)"/>
</linearGradient>
<radialGradient id="watchFace" cx="0.5" cy="0.4" r="0.7">
<stop offset="0" stop-color="#1a2338"/><stop offset="1" stop-color="#0d1119"/>
</radialGradient>
<radialGradient id="glow" cx="0.35" cy="0.25" r="0.8">
<stop offset="0" stop-color="rgba(255,255,255,0.20)"/><stop offset="1" stop-color="rgba(255,255,255,0)"/>
</radialGradient>
</defs>
<rect width="600" height="600" rx="40" fill="url(#g)"/>
<rect width="600" height="600" rx="40" fill="url(#glow)"/>
{device}
<text x="300" y="522" font-size="30" text-anchor="middle" font-family="Arial, sans-serif" font-weight="bold" fill="rgba(255,255,255,0.95)" letter-spacing="4">{brand[:10]}</text>
<text x="300" y="556" font-size="19" text-anchor="middle" font-family="Arial, sans-serif" fill="rgba(255,255,255,0.65)">{model[:20]}</text>
</svg>'''
        return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode()

    @staticmethod
    def _specs_for(brand: str, model: str, cap: int, color: str) -> dict:
        specs = {"品牌": brand, "型号": model, "颜色": color}
        if cap:
            specs["存储容量"] = f"{cap}GB"
        if brand == "Apple" and "Pro" in model:
            specs.update({"屏幕": "6.1英寸 OLED 120Hz", "芯片": "A17 Pro", "摄像头": "4800万像素三摄", "电池": "3274mAh", "重量": "187g", "系统": "iOS 17"})
        elif brand == "Apple" and "iPhone" in model:
            specs.update({"屏幕": "6.1英寸 OLED", "芯片": "A16", "摄像头": "4800万像素双摄", "电池": "3349mAh", "重量": "171g", "系统": "iOS 17"})
        elif brand == "Apple" and "MacBook" in model:
            specs.update({"屏幕": "13.6英寸 Liquid Retina", "芯片": "Apple M3", "内存": "8GB 统一内存", "固态硬盘": "256GB", "续航": "18小时", "重量": "1.24kg", "接口": "雷雳3 ×2"})
        elif brand == "华为" and "MatePad" in model:
            specs.update({"屏幕": "11英寸 2.5K 120Hz", "芯片": "麒麟9000S", "电池": "8300mAh", "充电": "66W快充", "系统": "HarmonyOS 4", "重量": "508g"})
        elif brand == "华为" and "FreeBuds" in model:
            specs.update({"降噪": "智慧动态降噪3.0", "续航": "30小时(含充电盒)", "蓝牙": "5.3", "防水": "IP54", "延迟": "90ms低延迟"})
        elif brand == "华为":
            specs.update({"屏幕": "6.82英寸 OLED 120Hz", "芯片": "麒麟9000S", "摄像头": "5000万像素三摄", "电池": "5000mAh", "重量": "225g", "系统": "HarmonyOS 4"})
        elif brand == "小米" and "Watch" in model:
            specs.update({"屏幕": "1.43英寸 AMOLED", "表盘": "46mm", "续航": "15天", "防水": "5ATM", "心率监测": "支持", "GPS": "双频GPS", "蓝牙": "5.3"})
        elif brand == "小米":
            specs.update({"屏幕": "6.36英寸 OLED 120Hz", "芯片": "骁龙8 Gen3", "摄像头": "5000万像素徕卡三摄", "电池": "4610mAh", "重量": "193g", "系统": "HyperOS"})
        elif brand == "索尼" and "WH" in model:
            specs.update({"降噪": "HD降噪处理器QN1", "续航": "30小时", "蓝牙": "5.3", "驱动": "30mm动圈", "快充": "充电10分钟听5小时", "重量": "250g"})
        elif brand == "索尼":
            specs.update({"降噪": "降噪处理器V2", "续航": "24小时(含充电盒)", "蓝牙": "5.3", "防水": "IPX4", "编解码": "LDAC"})
        elif brand == "戴森":
            specs.update({"吸力": "260AW", "续航": "60分钟", "过滤": "HEPA全机过滤", "探测": "激光探测", "尘桶容量": "0.77L", "重量": "3.1kg"})
        elif brand in ("耐克", "阿迪达斯", "李宁"):
            tech = {
                "Air Force 1": ("Air 缓震气垫", "皮革鞋面", "低帮"),
                "Dunk Low": ("Zoom Air 缓震", "皮革拼接", "低帮"),
                "Pegasus": ("React 泡棉", "工程网面", "低帮"),
                "Air Max 270": ("Air Max 270 大气垫", "网眼鞋面", "低帮"),
                "Ultraboost": ("Boost 缓震中底", "Primeknit 针织", "低帮"),
                "贝壳头": ("贝壳头设计", "皮革鞋面", "低帮"),
                "悟道": ("云缓震科技", "织物鞋面", "低帮"),
                "赤兔": ("䨻轻弹科技", "MONO 纱线", "低帮"),
            }
            mid = next((t for k, t in tech.items() if k.lower() in model.lower()), ("EVA 缓震", "织物鞋面", "低帮"))
            specs.update({
                "鞋面材质": mid[1], "缓震科技": mid[0], "鞋帮高度": mid[2],
                "闭合方式": "系带", "尺码": "36-46（含半码）", "重量": "约320-420g",
                "适用场景": "通勤/日常穿搭", "上市年份": "2025",
            })
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
            image_url=MockSource._gen_image(brand, params.get("型号", "")),
            title=title,
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
