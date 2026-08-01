"""AllPrice — 商品归一化引擎

把不同平台的 PlatformOffer 合并成统一 Product：
1. 生成 SKU 指纹（品牌+型号+规格哈希）
2. 指纹相同 → 同一商品
3. 指纹不同但参数高度相似 → AI 辅助判定（预留）
4. 汇总所有平台的报价到 Product.offers
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Optional

from ..models import PlatformOffer, Product

log = logging.getLogger(__name__)

# 品牌常见映射（平台标题写法不同）
_BRAND_ALIASES = {
    "apple": "Apple", "苹果": "Apple",
    "huawei": "Huawei", "华为": "Huawei",
    "xiaomi": "Xiaomi", "小米": "Xiaomi",
    "oppo": "OPPO", "vivo": "vivo",
    "samsung": "Samsung", "三星": "Samsung",
    "sony": "Sony", "lenovo": "Lenovo", "联想": "Lenovo",
    "dell": "Dell", "hp": "HP", "huawei": "Huawei",
}


class ProductNormalizer:
    """商品归一化：跨平台匹配同一商品（系列聚合 + 规格变体）"""

    def normalize(self, offers: list[PlatformOffer], keyword: str = "") -> list[Product]:
        """输入各平台报价，输出合并后的商品列表

        同一"系列"（品牌+型号相同）合并为一个 Product：
        - Product.offers 聚合该系列所有平台的报价
        - Product.variants 按规格（颜色/容量）分组，每个变体含对应平台的报价
        """
        # 1. 按系列指纹分组（品牌+型号，不含规格）
        series_groups: dict[str, list[PlatformOffer]] = {}
        for offer in offers:
            sfp = self._series_fingerprint(offer)
            series_groups.setdefault(sfp, []).append(offer)

        products: list[Product] = []
        for sfp, group in series_groups.items():
            # 2. 系列内再按完整指纹（含规格）拆变体
            variant_groups: dict[str, list[PlatformOffer]] = {}
            for offer in group:
                vfp = self._fingerprint(offer)
                variant_groups.setdefault(vfp, []).append(offer)

            variants: list[Product] = []
            for vfp, vgroup in variant_groups.items():
                primary = max(vgroup, key=lambda o: len(o.title))
                specs = self._extract_specs(primary)
                variants.append(Product(
                    sku_fingerprint=vfp,
                    name=self._clean_name(primary.title),
                    brand=specs.get("品牌", ""),
                    model=specs.get("型号", ""),
                    specs=specs,
                    image_url=primary.image_url,
                    offers=sorted(vgroup, key=lambda o: o.final_price or float('inf')),
                ))
            if not variants:
                continue
            # 3. 系列主商品 = 最低价变体（其余进 variants 列表）
            variants.sort(key=lambda v: v.best_offer().final_price if v.best_offer() else float('inf'))
            main = variants[0]
            main.variants = variants[1:] if len(variants) > 1 else []
            products.append(main)

        # 按最低到手价排序
        products.sort(key=lambda p: p.best_offer().final_price if p.best_offer() else float('inf'))
        return products

    @staticmethod
    def _series_fingerprint(offer: PlatformOffer) -> str:
        """系列指纹：品牌 + 型号（不含规格）——同系列不同规格合并为一个商品"""
        specs = offer.params or {}
        brand = ProductNormalizer._norm_brand(specs.get("品牌", "") or ProductNormalizer._guess_brand(offer.title))
        model = ProductNormalizer._norm_model(specs.get("型号", "") or ProductNormalizer._guess_model(offer.title))
        raw = json.dumps([brand, model], ensure_ascii=False)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

    def _fingerprint(self, offer: PlatformOffer) -> str:
        """SKU 指纹：品牌 + 型号 + 关键规格（规格键归一化）"""
        specs = offer.params or {}
        brand = self._norm_brand(specs.get("品牌", "") or self._guess_brand(offer.title))
        model = self._norm_model(specs.get("型号", "") or self._guess_model(offer.title))
        # 规格维度：键名归一化（内存/存储/容量 → storage），版本/颜色保留原文
        dims = []
        spec_map = {
            "内存": "storage", "存储": "storage", "容量": "storage",
            "运行内存": "ram", "RAM": "ram",
            "颜色": "color", "配色": "color",
            "版本": "version", "款式": "version",
            "规格": "spec", "尺寸": "size",
        }
        for key, norm_key in spec_map.items():
            v = specs.get(key)
            if v:
                dims.append(f"{norm_key}:{self._norm_value(v)}")
        raw = json.dumps([brand, model, sorted(dims)], ensure_ascii=False)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _norm_brand(brand: str) -> str:
        b = brand.strip().lower()
        return _BRAND_ALIASES.get(b, brand.strip())

    @staticmethod
    def _norm_value(value: str) -> str:
        """规格值归一化：256GB/256G/256g → 256g；去空格与常见别名"""
        v = str(value).strip().lower().replace(" ", "")
        # 容量单位统一
        v = v.replace("gb", "g").replace("tb", "t").replace("mb", "m")
        return v

    @staticmethod
    def _norm_model(model: str) -> str:
        """型号归一化：去掉空格/大小写差异"""
        return re.sub(r"[\s\-_]", "", model.strip().lower())

    @staticmethod
    def _guess_brand(title: str) -> str:
        for alias in ("Apple", "苹果", "华为", "Huawei", "小米", "Xiaomi", "OPPO", "vivo", "三星", "Samsung", "联想", "索尼", "Sony"):
            if alias.lower() in title.lower():
                return alias
        return ""

    @staticmethod
    def _guess_model(title: str) -> str:
        """从标题猜型号：如 iPhone 15 Pro / 小米14 / Mate 60"""
        m = re.search(r"(iphone\s?\d+\s?\w*|mate\s?\d+|小米\s?\d+|redmi\s?\d+|poco\s?\w+|galaxy\s?\w+)", title, re.I)
        return m.group(1) if m else ""

    @staticmethod
    def _extract_specs(offer: PlatformOffer) -> dict:
        return dict(offer.params or {})

    @staticmethod
    def _clean_name(title: str) -> str:
        """清理标题：去括号备注、去平台后缀"""
        t = re.sub(r"【.*?】", "", title)
        t = re.sub(r"\[.*?\]", "", t)
        t = t.split("京东")[0].strip()
        return t[:60]
