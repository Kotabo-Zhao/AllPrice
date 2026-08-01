"""AllPrice — ZOL 中关村在线真实报价数据源

为什么选 ZOL：
- 京东/淘宝/拼多多/苏宁搜索接口全部 SPA + 签名 + 风控，服务器端无浏览器不可抓
- ZOL 是国内老牌 IT 报价站：搜索页/商品页/参数页/报价页全部服务端渲染（SSR），
  免费、无登录、无签名，直接 httpx 可抓
- 覆盖：手机/电脑/平板/耳机/手表/相机/家电等全数码品类（鞋服不在覆盖内）

数据真实度：
- 参考价 = 官方/主流渠道真实报价（如 iPhone 15 Pro ￥7999）
- 商家报价 = 全国经销商真实报价列表（￥7499/￥7999/￥9999…）
- 参数 = 官方规格（CPU/内存/重量/颜色…）
- 图片 = ZOL 商品实拍图（zol-img.com.cn，无防盗链）

降级策略：搜索无结果/失败 → 返回空，上层 registry 自动落到 mock 演示源
（鞋服等 ZOL 不覆盖品类会走 mock，前端以"演示"标签区分）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

import httpx

from ..models import PlatformOffer
from .base import BaseSource

log = logging.getLogger(__name__)

ZOL_SEARCH = "https://search.zol.com.cn/s/all.php"
ZOL_DETAIL = "https://detail.zol.com.cn"

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.zol.com.cn/",
}

# 品牌名归一化（ZOL 中文名 → 产品线统一名，与 mock/normalizer 对齐）
_BRAND_MAP = {
    "苹果": "Apple", "华为": "华为", "小米": "小米", "红米": "小米", "索尼": "索尼",
    "戴森": "戴森", "三星": "三星", "荣耀": "荣耀", "OPPO": "OPPO", "vivo": "vivo",
    "联想": "联想", "惠普": "惠普", "华硕": "华硕", "机械革命": "机械革命",
    "一加": "一加", "真我": "真我", "努比亚": "努比亚", "魅族": "魅族",
    "苹果Mac": "Apple",
}

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "zol_cache")
_SEARCH_TTL = 30 * 60       # 搜索列表 30 分钟
_DETAIL_TTL = 6 * 3600      # 详情/参数/报价 6 小时


class ZolSource(BaseSource):
    """ZOL 真实报价数据源"""

    platform = "zol"
    platform_label = "ZOL真实报价"

    def __init__(self, timeout: float = 10.0, max_items: int = 4):
        self._timeout = timeout
        self._max_items = max(1, min(max_items, 6))
        self._client = httpx.Client(
            headers=_HEADERS, timeout=timeout,
            follow_redirects=True, trust_env=False,
        )
        self._lock = threading.Lock()
        os.makedirs(_CACHE_DIR, exist_ok=True)

    # ── 主入口 ──

    def search(self, keyword: str, limit: int = 10) -> list[PlatformOffer]:
        """ZOL 搜索 → 商品列表 → 详情/参数/报价 → PlatformOffer 列表"""
        items = self._search_list(keyword)
        if not items:
            log.info(f"ZOL no result for '{keyword}'")
            return []
        n = min(self._max_items, len(items))
        # 并发抓详情（每商品：详情页 + 参数页 + 报价页）
        with ThreadPoolExecutor(max_workers=min(4, n)) as pool:
            details = list(pool.map(self._fetch_item, items[:n]))
        offers: list[PlatformOffer] = []
        for d in details:
            if not d:
                continue
            offers.extend(self._item_to_offers(d))
        return offers

    # ── 搜索列表 ──

    def _search_list(self, keyword: str) -> list[dict]:
        cache = self._cache_get("s:" + keyword, _SEARCH_TTL)
        if cache is not None:
            return cache
        try:
            resp = self._client.get(ZOL_SEARCH, params={"kword": keyword})
            resp.raise_for_status()
            text = resp.text
        except Exception as e:
            log.warning(f"ZOL search failed: {e}")
            return []
        items: list[dict] = []
        # <li> <span class="price">7999元起</span> <a href="//...index123.shtml">名称</a> </li>
        for m in re.finditer(
            r'<li>\s*<span class="price">([\d.]+)元起</span>\s*'
            r'<a[^>]*href="(//detail\.zol\.com\.cn/[^"]*?index(\d+)\.shtml)"[^>]*>([^<]+)</a>',
            text,
        ):
            price, href, pid, name = m.group(1), m.group(2), m.group(3), m.group(4).strip()
            if not pid or not name:
                continue
            items.append({
                "id": pid,
                "name": name,
                "price": float(price),
                "url": "https:" + href if href.startswith("//") else href,
            })
        # 去重（同名同价只留一个）
        seen = set()
        uniq = []
        for it in items:
            k = it["id"]
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        self._cache_put("s:" + keyword, uniq)
        return uniq

    # ── 详情页 ──

    def _fetch_item(self, item: dict) -> Optional[dict]:
        url = item["url"]
        cache = self._cache_get("d:" + url, _DETAIL_TTL)
        if cache is not None:
            return cache
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
            text = resp.text
        except Exception as e:
            log.warning(f"ZOL detail failed {url}: {e}")
            return None
        d = self._parse_detail(item, url, text)
        if d:
            self._cache_put("d:" + url, d)
        return d

    def _parse_detail(self, item: dict, url: str, text: str) -> Optional[dict]:
        # 参考价
        ref = re.search(r'<b class="price-type">([\d,]+)</b>', text)
        ref_price = float(ref.group(1).replace(",", "")) if ref else item.get("price")
        # 主图（zol-img 无防盗链；转原图：去掉 _WxH 后缀）
        img = ""
        m = re.search(r'https?://[^"\']*zol-img\.com\.cn/[^"\']+?\.(?:jpg|jpeg|png)', text)
        if m:
            img = re.sub(r'_\d+x\d+', "", m.group(0))  # /268_280x210/106/x.jpg → /268/106/x.jpg 原图
        # 参数页 + 报价页链接
        param_url = ""
        pm = re.search(r'href="(/[\d/]+param\.shtml)"', text)
        if pm:
            param_url = "https://detail.zol.com.cn" + pm.group(1)
        price_url = ""
        sm = re.search(r'href="(/[\d/]+price\.shtml)"', text)
        if sm:
            price_url = "https://detail.zol.com.cn" + sm.group(1)
        # 品牌/型号（从名称：品牌 型号（版本））
        name = item["name"]
        brand_raw = name.split(" ")[0] if " " in name else ""
        brand = _BRAND_MAP.get(brand_raw, brand_raw)
        model = name[len(brand_raw):].strip() if brand_raw else name
        # 参数（参数页）
        specs = {"品牌": brand or "ZOL", "型号": model or name}
        if param_url:
            specs.update(self._fetch_params(param_url))
        # 商家报价（报价页）
        shop_prices: list[float] = []
        if price_url:
            shop_prices = self._fetch_shop_prices(price_url)
        return {
            "name": name, "brand": brand, "model": model,
            "ref_price": ref_price, "image_url": img,
            "specs": specs, "shop_prices": shop_prices,
            "url": url,
        }

    def _fetch_params(self, param_url: str) -> dict:
        cache = self._cache_get("p:" + param_url, _DETAIL_TTL)
        if cache is not None:
            return cache
        specs: dict = {}
        try:
            resp = self._client.get(param_url)
            resp.raise_for_status()
            text = resp.text
            pairs = re.findall(
                r'<th[^>]*>(.*?)</th>\s*<td[^>]*>\s*<span[^>]*>([^<]{1,100})</span>',
                text, re.S,
            )
            for th, v in pairs:
                k = re.sub(r"<[^>]+>", "", th).strip()
                if k and k not in specs:
                    specs[k] = v.strip()
        except Exception as e:
            log.warning(f"ZOL params failed {param_url}: {e}")
        if specs:
            self._cache_put("p:" + param_url, specs)
        return specs

    def _fetch_shop_prices(self, price_url: str) -> list[float]:
        cache = self._cache_get("c:" + price_url, _DETAIL_TTL)
        if cache is not None:
            return cache
        prices: list[float] = []
        try:
            resp = self._client.get(price_url)
            resp.raise_for_status()
            text = resp.text
            # 报价页所有 ￥数字（去重）
            for p in re.findall(r'￥\s?([\d,]+(?:\.\d+)?)', text):
                try:
                    v = float(p.replace(",", ""))
                    if 10 <= v <= 1_000_000:
                        prices.append(v)
                except ValueError:
                    continue
            prices = sorted(set(prices))
        except Exception as e:
            log.warning(f"ZOL price page failed {price_url}: {e}")
        if prices:
            self._cache_put("c:" + price_url, prices)
        return prices

    # ── offers 生成 ──

    def _item_to_offers(self, d: dict) -> list[PlatformOffer]:
        ref = d["ref_price"]
        name = d["name"]
        offers = []
        # 1. ZOL 参考价
        offers.append(PlatformOffer(
            platform="zol", platform_label="ZOL参考",
            product_id=f"zol-{name}-ref",
            url=d["url"], title=name,
            image_url=d["image_url"],
            shop_name="ZOL 中关村在线",
            list_price=ref, sale_price=ref, final_price=ref,
            price_detail=f"ZOL 参考报价 {ref:g}元",
            params=d["specs"], sales="真实报价",
            stock=True, ship_fee=0.0,
        ))
        # 2. 商家报价（过滤偏离值：低于参考价 65% 的可能是二手/配件价，
        #    高于 150% 的是虚标/套装价；取最低 3 个合理报价）
        lo, hi = ref * 0.65, ref * 1.5
        shops = [p for p in d.get("shop_prices", [])
                 if lo <= p <= hi and abs(p - ref) > 0.01][:3]
        for i, p in enumerate(shops):
            offers.append(PlatformOffer(
                platform="zol_shop", platform_label="ZOL商家",
                product_id=f"zol-{name}-shop{i}",
                url=d["url"], title=name,
                image_url=d["image_url"],
                shop_name=f"ZOL 经销商报价 #{i+1}",
                list_price=ref, sale_price=p, final_price=p,
                price_detail=f"经销商报价 {p:g}元（参考价 {ref:g}元）",
                params=d["specs"], sales="真实报价",
                stock=True, ship_fee=0.0,
            ))
        return offers

    # ── 磁盘缓存 ──

    def _cache_path(self, key: str) -> str:
        h = hashlib.md5(key.encode("utf-8")).hexdigest()
        return os.path.join(_CACHE_DIR, f"{h}.json")

    def _cache_get(self, key: str, ttl: float) -> Optional[list]:
        try:
            path = self._cache_path(key)
            with self._lock:
                if not os.path.exists(path):
                    return None
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            if time.time() - data.get("ts", 0) < ttl:
                return data.get("data")
            return None
        except Exception:
            return None

    def _cache_put(self, key: str, data) -> None:
        try:
            path = self._cache_path(key)
            with self._lock:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"ts": time.time(), "data": data}, f, ensure_ascii=False)
        except Exception as e:
            log.warning(f"ZOL cache write failed: {e}")
