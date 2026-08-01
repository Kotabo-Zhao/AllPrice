"""AllPrice — 京东联盟官方 API 数据源（真实京东价格）

为什么用京东联盟：
- 京东搜索页/价格接口全部反爬（SPA+签名+风控），服务器端不可抓
- 京东联盟（union.jd.com）是官方开放平台：个人实名即可注册，
  基础权限免费（5 次/秒），返回全站商品真实数据：
  价格/到手价(lowPrice)/优惠券/销量/好评率/店铺/图片/佣金
- 这是比价 App 的"正路"数据源，合规稳定

接入方式：
1. union.jd.com 注册 → 实名认证 → 创建媒体应用 → 开通 jd.union.open.goods.query
2. 拿到 appKey / appSecret / accessToken 填入环境变量：
   JD_UNION_APP_KEY / JD_UNION_APP_SECRET / JD_UNION_ACCESS_TOKEN
3. 未配置密钥时本源返回空（不影响 zol/mock 流程），配置后自动启用

接口：https://api.jd.com/routerjson  method=jd.union.open.goods.query
签名：MD5(secret + 按 key 排序拼接的参数 + secret) 转大写
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Optional

import httpx

from ..models import Coupon, PlatformOffer
from .base import BaseSource

log = logging.getLogger(__name__)

JD_API = "https://api.jd.com/routerjson"
METHOD = "jd.union.open.goods.query"


class JdUnionSource(BaseSource):
    """京东联盟官方 API 数据源（需要用户注册并配置密钥）"""

    platform = "jd_union"
    platform_label = "京东"

    def __init__(self, app_key: str = "", app_secret: str = "", access_token: str = "",
                 timeout: float = 10.0):
        self._app_key = app_key or os.getenv("JD_UNION_APP_KEY", "")
        self._app_secret = app_secret or os.getenv("JD_UNION_APP_SECRET", "")
        self._access_token = access_token or os.getenv("JD_UNION_ACCESS_TOKEN", "")
        self._enabled = bool(self._app_key and self._app_secret and self._access_token)
        self._client = httpx.Client(timeout=timeout, trust_env=False)
        if not self._enabled:
            log.info("JdUnionSource disabled: 未配置 JD_UNION_* 密钥（注册 union.jd.com 获取）")

    def search(self, keyword: str, limit: int = 10) -> list[PlatformOffer]:
        if not self._enabled:
            return []
        try:
            goods = self._query_goods(keyword, page_size=min(20, max(5, limit * 2)))
        except Exception as e:
            log.warning(f"JD union query failed: {e}")
            return []
        return self._goods_to_offers(goods, limit)

    # ── API 调用 ──

    def _query_goods(self, keyword: str, page_size: int = 20) -> list[dict]:
        """jd.union.open.goods.query：关键词搜索商品（官方文档字段）"""
        body = json.dumps({
            "keyword": keyword,
            "pageIndex": 1,
            "pageSize": page_size,
            "fields": "skuId,skuName,price,lowPrice,imgUrl,brandName,shopName,"
                      "comments,goodComments,coupon,commissionInfo,inOrderCount30Days",
        }, ensure_ascii=False)
        params = self._sign_params({"method": METHOD, "body": body, "access_token": self._access_token})
        resp = self._client.post(JD_API, data=params)
        resp.raise_for_status()
        data = resp.json()
        # 错误检查
        if "error_response" in data:
            err = data["error_response"]
            log.warning(f"JD union error: {err.get('code')} {err.get('msg', '')[:80]}")
            return []
        result = (data.get("jd_union_open_goods_query_response", {})
                      .get("result", ""))
        if not result:
            return []
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return []
        return result.get("data", []) or []

    def _sign_params(self, params: dict) -> dict:
        """京东联盟签名：参数按 key 排序拼接，MD5(secret + 拼串 + secret) 大写"""
        base = {
            "app_key": self._app_key,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "format": "json",
            "v": "1.0",
            "sign_method": "md5",
        }
        base.update(params)
        to_sign = sorted(base.items())
        raw = "&".join(f"{k}{v}" for k, v in to_sign)
        sign = hashlib.md5((self._app_secret + raw + self._app_secret).encode()).hexdigest().upper()
        base["sign"] = sign
        return base

    # ── 数据映射 ──

    def _goods_to_offers(self, goods: list[dict], limit: int) -> list[PlatformOffer]:
        offers: list[PlatformOffer] = []
        for g in goods[:limit]:
            sku = g.get("skuId")
            name = g.get("skuName") or ""
            if not sku or not name:
                continue
            price = float(g.get("price") or 0)
            low = float(g.get("lowPrice") or price or 0)
            img = g.get("imgUrl") or ""
            if img.startswith("//"):
                img = "https:" + img
            # 优惠券 → Coupon
            coupons: list[Coupon] = []
            coupon_raw = g.get("coupon")
            if coupon_raw:
                # 京东联盟 coupon 为字符串："券面额:10元;券类型:商品券;使用门槛:满199可用..."
                parts = str(coupon_raw).split(";")
                for p in parts:
                    p = p.strip()
                    if p.startswith("券面额") or p.startswith("满") or "元" in p:
                        m = re.search(r"(\d+(?:\.\d+)?)\s*元", p)
                        if m:
                            coupons.append(Coupon(
                                kind="jd_coupon", label=p,
                                threshold=0, discount=float(m.group(1)),
                            ))
            sales = g.get("inOrderCount30Days")
            sales_text = f"{sales}单/月" if sales else None
            comments = g.get("comments")
            good_rate = g.get("goodComments")  # 好评率（如 "97%"）
            price_detail = f"京东到手价 {low:g}元"
            if coupons:
                price_detail += f"（含{coupons[0].label}）"
            offers.append(PlatformOffer(
                platform="jd_union", platform_label="京东",
                product_id=f"jd-{sku}",
                url=f"https://item.jd.com/{sku}.html",
                title=name,
                image_url=img,
                shop_name=g.get("shopName") or "京东自营",
                list_price=price, sale_price=low, final_price=low,
                price_detail=price_detail,
                coupons=coupons,
                params={"品牌": g.get("brandName") or "", "型号": name[:30]},
                sales=sales_text,
                stock=True, ship_fee=0.0,
            ))
        return offers
