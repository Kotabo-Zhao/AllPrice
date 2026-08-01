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
    """商品归一化：跨平台匹配同一商品"""

    def normalize(self, offers: list[PlatformOffer], keyword: str = "") -> list[Product]:
        """输入各平台报价，输出合并后的商品列表"""
        groups: dict[str, list[PlatformOffer]] = {}
        for offer in offers:
            fp = self._fingerprint(offer)
            groups.setdefault(fp, []).append(offer)

        products: list[Product] = []
        for fp, group in groups.items():
            # 取标题最长的作为主标题
            primary = max(group, key=lambda o: len(o.title))
            specs = self._extract_specs(primary)
            products.append(Product(
                sku_fingerprint=fp,
                name=self._clean_name(primary.title),
                brand=specs.get("品牌", ""),
                model=specs.get("型号", ""),
                specs=specs,
                image_url=primary.image_url,
                offers=sorted(group, key=lambda o: o.final_price or float('inf')),
            ))
        # 按最低到手价排序
        products.sort(key=lambda p: p.best_offer().final_price if p.best_offer() else float('inf'))
        return products

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
