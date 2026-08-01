"""AllPrice — AI 推荐服务

职责：
- 综合比价数据 → AI 生成购买推荐（最优平台 + 理由 + 购买时机建议）
- 核心原则：**AI 只做分析，价格数据永远来自真实数据源（AI 不编价格）**

实现：
- 有 DEEPSEEK_API_KEY → 调 DeepSeek API（免费档），注入结构化比价数据
- 无 key / 调用失败 → 降级到规则推荐（基于价格+优惠+平台数量打分）

规则：
1. AI 的输入是结构化比价数据（JSON），输出是自然语言建议
2. 输出强制约束：只能引用输入中的价格，不得虚构
3. 超时 8s，失败静默降级，绝不影响比价主流程
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx

from ..models import Product

log = logging.getLogger(__name__)

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
# v2: 接入 DeepSeek V4 Flash（免费档，思考模式显式关闭保证低延迟）
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def _load_env():
    """加载 backend/.env（轻量实现，避免额外依赖 python-dotenv）"""
    try:
        env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
        env_path = os.path.abspath(env_path)
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception as e:
        log.warning(f".env load failed: {e}")


_load_env()

# 规则推荐的平台权重（价格优先，平台信誉/发货为次要因子）
_PLATFORM_WEIGHT = {"jd": 1.0, "taobao": 0.97, "pdd": 0.94, "mock": 0.8}


class AIRecommendService:
    """AI 购买推荐服务"""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self._client = httpx.Client(timeout=10.0)

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def recommend(self, product: Product) -> dict:
        """对单个商品生成购买推荐

        Returns:
            {"recommendation": str, "source": "ai"|"rule", "confidence": float}
        """
        if self.available and product.offers:
            ai_result = self._try_ai(product)
            if ai_result:
                return ai_result
        return self._rule_recommend(product)

    # ── AI 路径 ──

    def _try_ai(self, product: Product) -> Optional[dict]:
        """调 DeepSeek 生成推荐（失败返回 None 降级）"""
        try:
            payload = {
                "model": DEEPSEEK_MODEL,
                "thinking": {"type": "disabled"},  # V4 Flash 思考模式默认开，显式关闭
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是购物比价助手。用户会给你一个商品在多个平台的报价数据（JSON），"
                            "请推荐最划算的购买方案。硬性要求："
                            "1. 只能引用数据中出现的价格，绝对禁止编造价格；"
                            "2. 从价格、优惠力度、平台差异、购买时机四个角度分析；"
                            "3. 输出 100 字以内，直接给出结论：推荐哪个平台、为什么、多少钱。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(self._build_ai_context(product), ensure_ascii=False),
                    },
                ],
                "temperature": 0.3,
                "max_tokens": 300,
            }
            resp = self._client.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            return {
                "recommendation": text,
                "source": "ai",
                "confidence": 0.85,
            }
        except Exception as e:
            log.warning(f"AI recommend failed (fallback to rule): {e}")
            return None

    @staticmethod
    def _build_ai_context(product: Product) -> dict:
        """构造给 AI 的结构化比价数据（只含真实字段）"""
        offers = []
        for o in product.offers:
            offers.append({
                "平台": o.platform_label,
                "售价": o.sale_price,
                "优惠后到手价": o.final_price,
                "优惠明细": o.price_detail or "无",
                "销量": o.sales,
            })
        return {
            "商品": product.name,
            "规格": product.specs,
            "各平台报价": offers,
            "历史最低价": None,  # 预留：接数据库后填真实历史最低
        }

    # ── 规则降级路径 ──

    def _rule_recommend(self, product: Product) -> dict:
        """无 AI 时的规则推荐：价格 + 优惠 + 平台权重打分"""
        if not product.offers:
            return {"recommendation": "暂无可用的报价数据", "source": "rule", "confidence": 0.3}

        best = min(product.offers, key=lambda o: o.final_price or float('inf'))
        # 打分：价格占 70%，优惠覆盖占 20%，平台信誉 10%
        scored = []
        for o in product.offers:
            price_score = 100 - (o.final_price / best.final_price - 1) * 200 if best.final_price else 0
            coupon_score = 80 if o.coupons else 50
            platform_score = _PLATFORM_WEIGHT.get(o.platform, 0.9) * 100
            total = price_score * 0.7 + coupon_score * 0.2 + platform_score * 0.1
            scored.append((o, total))
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[0][0]

        save = best.final_price
        others = [o for o in product.offers if o is not best]
        diff = (others[0].final_price - best.final_price) if others else 0
        text = (
            f"推荐在 {best.platform_label} 购买：到手 ¥{best.final_price:g}"
            + (f"，比其他平台最低可省 ¥{diff:g}" if diff > 0 else "，为全网最低价")
            + f"。该价格已含{best.price_detail or '当前优惠'}。"
            + "建议关注大促节点，历史低位可入手。"
        )
        return {"recommendation": text, "source": "rule", "confidence": 0.7, "best_platform": top.platform}
