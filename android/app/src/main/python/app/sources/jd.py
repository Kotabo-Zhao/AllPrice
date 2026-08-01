"""AllPrice — 京东数据源适配器（免费公开接口，无需登录）

接口：
1. 价格: https://p.3.cn/prices/mgets?skuIds=J_{sku}
   → [{ "id": "J_100012043978", "p": "5499.00", "m": "6499.00", "op": "5499.00" }]
   p=售价, m=原价/划线价, op=裸价
2. 详情: https://item-soa.jd.com/getItemDetail?skuId={sku}
   → 标题/品牌/参数/主图
3. 搜索: https://search.jd.com/Search?keyword={kw}&enc=utf-8 (HTML)
   → 商品ID列表（解析HTML）

稳定性：公开接口无鉴权，限频即可。反爬主要针对搜索页，需控制频率。
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Optional

import httpx

from ..models import Coupon, PlatformOffer

log = logging.getLogger(__name__)

PRICE_API = "https://p.3.cn/prices/mgets"
DETAIL_API = "https://item-soa.jd.com/getItemDetail"
SEARCH_API = "https://search.jd.com/Search"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.jd.com/",
    "Accept": "application/json,text/plain,*/*",
}

_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 60.0  # 秒


class JDSource:
    """京东数据源"""

    platform = "jd"
    platform_label = "京东"

    def __init__(self, timeout: float = 8.0):
        self.client = httpx.Client(
            headers=_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )

    # ── 价格 ──

    def get_price(self, sku: str) -> Optional[dict]:
        """单 SKU 价格"""
        return self.get_prices([sku]).get(sku)

    def get_prices(self, skus: list[str]) -> dict[str, dict]:
        """批量价格（一次最多 20 个 SKU）"""
        result: dict[str, dict] = {}
        for i in range(0, len(skus), 20):
            batch = skus[i:i + 20]
            query = ",".join(f"J_{s}" for s in batch)
            url = f"{PRICE_API}?skuIds={query}"
            try:
                resp = self.client.get(url)
                resp.raise_for_status()
                data = resp.json()
                for item in data:
                    sku = str(item.get("id", "")).replace("J_", "")
                    result[sku] = {
                        "price": float(item.get("p", 0) or 0),
                        "market_price": float(item.get("m", 0) or 0),
                        "op_price": float(item.get("op", 0) or 0),
                    }
                time.sleep(random.uniform(0.3, 0.8))  # 限频
            except Exception as e:
                log.warning(f"JD price fetch failed for {batch}: {e}")
        return result

    # ── 搜索 ──

    def search(self, keyword: str, limit: int = 10) -> list[PlatformOffer]:
        """搜索商品，返回平台报价列表（价格+基础信息）"""
        # 1. 搜索页拿商品ID
        skus = self._search_skus(keyword, limit)
        if not skus:
            log.warning(f"JD search returned no results for '{keyword}'")
            return []

        # 2. 批量拿价格
        prices = self.get_prices(skus)

        # 3. 详情页拿标题/参数（缓存 + 限频）
        offers: list[PlatformOffer] = []
        for sku in skus:
            price_info = prices.get(sku)
            if not price_info or price_info["price"] <= 0:
                continue
            detail = self._get_detail_cached(sku)
            offer = PlatformOffer(
                platform=self.platform,
                platform_label=self.platform_label,
                product_id=sku,
                url=f"https://item.jd.com/{sku}.html",
                title=detail.get("title") or f"京东商品{sku}",
                image_url=detail.get("image", ""),
                list_price=price_info["market_price"] or price_info["op_price"] or price_info["price"],
                sale_price=price_info["price"],
                final_price=price_info["price"],
                params=detail.get("params", {}),
                coupons=self._extract_coupons(detail, price_info["price"]),
                fetched_at=__import__("datetime").datetime.utcnow(),
            )
            offers.append(offer)
            if len(offers) >= limit:
                break
        return offers

    def _search_skus(self, keyword: str, limit: int) -> list[str]:
        """从搜索页 HTML 提取商品ID"""
        try:
            resp = self.client.get(
                SEARCH_API,
                params={"keyword": keyword, "enc": "utf-8"},
            )
            resp.raise_for_status()
            html = resp.text
            # 商品ID出现在 sku="12345" 或 data-sku="12345" 或 .../100012043978.html
            ids = re.findall(r'sku[=:]["\']?(\d{5,})', html)
            ids += re.findall(r'(\d{6,})\.html', html)
            # 去重保序
            seen: set[str] = set()
            unique = []
            for i in ids:
                if i not in seen:
                    seen.add(i)
                    unique.append(i)
                if len(unique) >= limit:
                    break
            time.sleep(random.uniform(0.8, 1.5))  # 搜索页反爬严格，降频
            return unique
        except Exception as e:
            log.warning(f"JD search page fetch failed: {e}")
            return []

    def _get_detail_cached(self, sku: str) -> dict:
        """详情接口（带 60s 缓存 + 限频）"""
        now = time.time()
        cached = _cache.get(sku)
        if cached and now - cached[0] < CACHE_TTL:
            return cached[1]
        try:
            resp = self.client.get(DETAIL_API, params={"skuId": sku})
            resp.raise_for_status()
            data = resp.json()
            item = data.get("data", {})
            title = item.get("skuName", "") or ""
            # 参数：从 saleAttr / 详情提取
            params = {}
            brand = item.get("brand", "")
            if brand:
                params["品牌"] = brand
            # 主图
            image = item.get("image", "")
            if not image and item.get("imageList"):
                image = item["imageList"][0]
            if isinstance(image, str) and image.startswith("//"):
                image = "https:" + image
            detail = {"title": title, "image": image, "params": params}
            _cache[sku] = (now, detail)
            time.sleep(random.uniform(0.2, 0.5))
            return detail
        except Exception as e:
            log.warning(f"JD detail fetch failed for {sku}: {e}")
            return {"title": "", "image": "", "params": {}}

    @staticmethod
    def _extract_coupons(detail: dict, price: float) -> list[Coupon]:
        """从详情提取优惠券（公开接口字段有限，标记为可扩展）"""
        # item-soa 返回的优惠信息有限；这里预留扩展位
        # 真实券数据可从商品页促销接口或联盟接口补充（后续迭代）
        coupons: list[Coupon] = []
        # TODO(v2): 接入促销接口获取 满减/店铺券
        return coupons
