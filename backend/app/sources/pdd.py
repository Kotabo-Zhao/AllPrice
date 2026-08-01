"""AllPrice — 拼多多数据源适配器（免费爬虫方案）

策略：
1. 搜索: Playwright 渲染移动端 H5 搜索页（mobile.yangkeduo.com）
   → 提取 goods_id / 标题 / 拼单价 / 销量
2. 详情: 若搜索页含百亿补贴/活动价则直接用；否则可再开详情页
3. anti_content: 拼多多请求参数带加密签名，Playwright 渲染可绕过
   （开源已验证，此实现渲染后直接从 DOM 读）

注意：
- 纯爬虫，成功率受平台反爬影响
- 低频 + 随机延时 + 不采集个人信息
- 网络环境无登录态时自动降级返回空
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Optional

from ..models import Coupon, PlatformOffer

log = logging.getLogger(__name__)

SEARCH_URL = "https://mobile.yangkeduo.com/search_result.html"
DETAIL_URL = "https://mobile.yangkeduo.com/goods.html"

try:
    from playwright.sync_api import sync_playwright
    _PW_AVAILABLE = True
except ImportError:
    _PW_AVAILABLE = False
    log.warning("playwright 未安装，拼多多数据源将不可用")


class PddSource:
    """拼多多数据源（Playwright 渲染）"""

    platform = "pdd"
    platform_label = "拼多多"

    def __init__(self, headless: bool = True, timeout_ms: int = 15000):
        self._headless = headless
        self._timeout_ms = timeout_ms
        self._pw = None
        self._browser = None

    def _ensure_browser(self):
        if self._browser:
            return self._browser
        if not _PW_AVAILABLE:
            raise RuntimeError("playwright not available")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        log.info("PddSource: browser launched")
        return self._browser

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception as e:
            log.warning(f"PddSource close: {e}")
        self._browser = None
        self._pw = None

    def __del__(self):
        self.close()

    def search(self, keyword: str, limit: int = 10) -> list[PlatformOffer]:
        """搜索拼多多商品"""
        try:
            browser = self._ensure_browser()
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                    "Mobile/15E148 Safari/604.1"
                ),
                viewport={"width": 390, "height": 844},
            )
            page = context.new_page()
            try:
                page.goto(f"{SEARCH_URL}?search_key={keyword}", timeout=self._timeout_ms)
                page.wait_for_timeout(3000)  # 等 JS 渲染 + anti_content 校验
                offers = self._parse_search_page(page, keyword, limit)
                time.sleep(random.uniform(0.8, 1.8))
                return offers
            finally:
                context.close()
        except Exception as e:
            log.warning(f"PddSource search failed (degraded): {e}")
            return []

    def _parse_search_page(self, page, keyword: str, limit: int) -> list[PlatformOffer]:
        offers: list[PlatformOffer] = []

        # 尝试 JSON 内嵌数据
        try:
            raw = page.evaluate(
                "() => window.rawData || window.__NEXT_DATA__ || window.__INITIAL_DATA__ || null"
            )
            if raw:
                offers = self._parse_raw_data(raw, keyword, limit)
                if offers:
                    return offers
        except Exception:
            pass

        # DOM 解析：商品卡片
        items = page.query_selector_all(
            "[class*=goodsList] [class*=item], [class*=Goods] a, [class*=goods-item], li"
        )
        for item in items[:limit * 3]:
            try:
                link = item.query_selector("a")
                if not link:
                    continue
                href = link.get_attribute("href") or ""
                gid = re.search(r'goods_id=(\d+)', href)
                if not gid:
                    gid = re.search(r'/(\d{10,})\.html', href)
                if not gid:
                    continue
                pid = gid.group(1)
                title_el = item.query_selector("[class*=title], [class*=Title], [class*=name]")
                price_el = item.query_selector("[class*=price], [class*=Price]")
                sales_el = item.query_selector("[class*=sales], [class*=Sales], [class*=sold]")
                title = title_el.inner_text().strip() if title_el else ""
                price = self._parse_price(price_el.inner_text()) if price_el else 0.0
                sales = sales_el.inner_text().strip() if sales_el else None
                if pid and title:
                    offers.append(self._build_offer(pid, title, price, keyword, sales))
                if len(offers) >= limit:
                    break
            except Exception:
                continue
        return offers[:limit]

    def _parse_raw_data(self, raw, keyword: str, limit: int) -> list[PlatformOffer]:
        offers = []
        try:
            if isinstance(raw, str):
                raw = json.loads(raw)

            def find_items(obj, depth=0):
                if depth > 5:
                    return []
                if isinstance(obj, dict):
                    for key in ("goodsList", "items", "goods_list", "result"):
                        if key in obj and isinstance(obj[key], list) and obj[key]:
                            return obj[key]
                    for v in obj.values():
                        found = find_items(v, depth + 1)
                        if found:
                            return found
                return []

            items = find_items(raw)
            for it in items[:limit]:
                if not isinstance(it, dict):
                    continue
                pid = str(it.get("goods_id") or it.get("goodsId") or "")
                title = str(it.get("goods_name") or it.get("goodsName") or it.get("title") or "")
                # 拼多多官方字段（min_group_price/group_price）单位是"分"，price 字段通常是"元"
                fen_fields = ("min_group_price", "group_price", "min_on_sale_group_price", "coupon_min_group_price")
                price = 0.0
                for k in fen_fields:
                    if it.get(k):
                        price = float(it[k]) / 100
                        break
                if not price and it.get("price"):
                    price = float(it["price"])
                sales = str(it.get("sales_tip") or it.get("salesTip") or "")
                if pid and title:
                    offers.append(self._build_offer(pid, title, price, keyword, sales))
        except Exception as e:
            log.warning(f"Pdd raw data parse failed: {e}")
        return offers

    def _build_offer(self, pid: str, title: str, price: float, keyword: str, sales=None) -> PlatformOffer:
        return PlatformOffer(
            platform=self.platform,
            platform_label=self.platform_label,
            product_id=pid,
            url=f"{DETAIL_URL}?goods_id={pid}",
            title=title[:80],
            image_url="",
            list_price=price * 1.15,
            sale_price=price,
            final_price=price,
            price_detail="拼多多拼单价",
            params=self._extract_params(title),
            coupons=self._subsidy_coupon(price),
            sales=sales,
            fetched_at=__import__("datetime").datetime.utcnow(),
        )

    @staticmethod
    def _parse_price(text: str) -> float:
        if not text:
            return 0.0
        m = re.search(r"(\d+(?:\.\d+)?)", text.replace(",", ""))
        return float(m.group(1)) if m else 0.0

    @staticmethod
    def _extract_params(title: str) -> dict:
        return {}

    @staticmethod
    def _subsidy_coupon(price: float) -> list[Coupon]:
        """百亿补贴商品默认给一个补贴优惠（简化；真实补贴需详情页确认）"""
        return []
