"""AllPrice — API 路由

端点：
- GET /api/health            健康检查
- GET /api/search?keyword=    全网搜索比价（核心）
- GET /api/platforms         可用平台列表
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..core.normalizer import ProductNormalizer
from ..sources.base import SourceRegistry

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def get_registry(request: Request) -> SourceRegistry:
    """从 app.state 取数据源注册表（main.py 注入）"""
    registry: Optional[SourceRegistry] = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(503, "数据源未初始化")
    return registry


@router.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0", "name": "AllPrice"}


@router.get("/platforms")
async def platforms(registry: SourceRegistry = Depends(get_registry)):
    """当前可用的平台列表（含健康状态）"""
    return {
        "platforms": [
            {"key": p, "label": src.platform_label}
            for p, src in registry._sources.items()
        ],
        "healthy": registry.available_platforms(),
    }


@router.get("/history/{sku_fingerprint}")
async def history(
    sku_fingerprint: str,
    days: int = Query(90, ge=7, le=365),
    request: Request = None,
):
    """商品历史价格（走势图数据源）"""
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        from ..core.storage import Storage
        storage = Storage()
        request.app.state.storage = storage
    rows = storage.get_price_history(sku_fingerprint, days=days)
    # 按平台分组
    by_platform: dict[str, list[dict]] = {}
    for r in rows:
        by_platform.setdefault(r["platform_label"], []).append({
            "price": r["final_price"], "time": r["created_at"],
        })
    return {
        "sku_fingerprint": sku_fingerprint,
        "days": days,
        "series": by_platform,
        "lowest": storage.get_lowest_price(sku_fingerprint, days=days),
    }


@router.get("/storage/stats")
async def storage_stats(request: Request):
    """存储统计（健康检查）"""
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        from ..core.storage import Storage
        storage = Storage()
        request.app.state.storage = storage
    return storage.stats()


@router.get("/search")
async def search(
    keyword: str = Query(..., min_length=1, max_length=60, description="搜索关键词"),
    limit: int = Query(6, ge=1, le=15),
    registry: SourceRegistry = Depends(get_registry),
    request: Request = None,
):
    """全网搜索商品 → 各平台报价 → 归一化 → 按最低到手价排序

    返回:
    {
      "keyword": str,
      "products": [
        {
          "sku_fingerprint": str,
          "name": str, "brand": str, "model": str,
          "specs": {}, "image_url": str,
          "best_price": float, "best_platform": str,
          "offers": [ {platform, title, price, final_price, url, detail...} ]
        }
      ],
      "source_status": {platform: healthy}
    }
    """
    # 并行搜索所有健康数据源（每源限时；mock 兜底秒回，真实源超时只降级）
    results = await registry.search_all(keyword, limit=limit, timeout=6.0)

    # 汇总所有报价
    all_offers = []
    for platform, offers in results.items():
        all_offers.extend(offers)

    if not all_offers:
        return {
            "keyword": keyword,
            "products": [],
            "source_status": {p: True for p in registry.available_platforms()},
            "message": "未找到该商品，试试更精确的关键词（如品牌+型号）",
        }

    # 归一化合并
    normalizer = ProductNormalizer()
    products = normalizer.normalize(all_offers, keyword)

    # AI 推荐（无 key/失败时降级规则推荐，不影响主流程）
    ai_service = getattr(request.app.state, "ai_service", None)
    if ai_service is None:
        from ..core.ai_recommend import AIRecommendService
        ai_service = AIRecommendService()
        request.app.state.ai_service = ai_service

    # 存储（价格快照落库，走势图数据源）
    storage = getattr(request.app.state, "storage", None)
    if storage is None:
        from ..core.storage import Storage
        storage = Storage()
        request.app.state.storage = storage

    # 序列化
    product_list = []
    for p in products:
        best = p.best_offer()
        # 补充商品级信息（广告语/评分/主图）——演示源生成，真实源跳过
        try:
            from ..sources.mock import MockSource
            MockSource.enrich_product(p)
        except Exception:
            pass
        # AI 推荐走线程池，不阻塞事件循环（DeepSeek 网络慢时不拖慢搜索响应）
        rec = await asyncio.to_thread(ai_service.recommend, p)
        # 价格快照落库
        for o in p.offers:
            storage.save_price_snapshot(
                p.sku_fingerprint, o.platform, o.platform_label, p.name,
                o.sale_price, o.final_price, o.price_detail,
            )
        storage.cache_product(p)
        # 历史走势（真实数据不足时用模拟曲线补全，保证看板走势图完整）
        history = storage.get_price_history(p.sku_fingerprint, days=90)
        lowest_90 = storage.get_lowest_price(p.sku_fingerprint, days=90)
        history_series = [
            {"date": r["created_at"][:10], "price": r["final_price"]}
            for r in history
        ]
        if len(history_series) < 30 and best:
            from ..sources.mock import MockSource
            mock_pts = MockSource.gen_history(best.final_price)
            merged: dict[str, float] = {pt["date"]: pt["price"] for pt in mock_pts}
            for pt in history_series:
                merged[pt["date"]] = pt["price"]  # 真实点覆盖模拟
            history_series = [
                {"date": d, "price": v} for d, v in sorted(merged.items())
            ]
        # 价格统计（看板 KPI）
        prices = sorted(o.final_price for o in p.offers if o.final_price)
        avg = sum(prices) / len(prices) if prices else 0
        high = prices[-1] if prices else 0
        low = prices[0] if prices else 0
        savings = round((best.list_price - best.final_price), 2) if best and best.list_price else 0
        product_list.append({
            "sku_fingerprint": p.sku_fingerprint,
            "name": p.name,
            "brand": p.brand,
            "model": p.model,
            "specs": p.specs,
            "image_url": p.image_url,
            "ad_slogan": p.ad_slogan,
            "description": p.description,
            "rating": p.rating,
            "review_count": p.review_count,
            "best_price": round(best.final_price, 2) if best else None,
            "best_platform": best.platform if best else None,
            "best_platform_label": best.platform_label if best else None,
            "offer_count": len(p.offers),
            "recommendation": rec.get("recommendation", ""),
            "recommend_source": rec.get("source", "rule"),
            "lowest_90d": round(lowest_90, 2) if lowest_90 else None,
            "history_points": len(history_series),
            "price_stats": {
                "avg": round(avg, 2), "high": round(high, 2),
                "low": round(low, 2), "savings": round(savings, 2),
            },
            "history": history_series,
            "offers": [
                {
                    "platform": o.platform,
                    "platform_label": o.platform_label,
                    "product_id": o.product_id,
                    "url": o.url,
                    "title": o.title,
                    "image_url": o.image_url,
                    "shop_name": o.shop_name,
                    "list_price": round(o.list_price, 2),
                    "sale_price": round(o.sale_price, 2),
                    "final_price": round(o.final_price, 2),
                    "price_detail": o.price_detail,
                    "sales": o.sales,
                    "stock": o.stock,
                    "ship_fee": o.ship_fee,
                    "coupons": [{"kind": c.kind, "label": c.label} for c in o.coupons],
                    "params": o.params,
                }
                for o in p.offers
            ],
        })

    return {
        "keyword": keyword,
        "products": product_list,
        "source_status": {p: True for p in registry.available_platforms()},
        "total": len(product_list),
    }
