"""AllPrice — 社交平台数据源（真实成交价）

从社交/晒价平台抓取"用户实际买到多少钱、怎么买的"：
- 什么值得买（smzdm）：晒价文章，含价格/平台/优惠方式
- 微博/贴吧：用户晒单帖（备用通道）

策略：
1. 主通道 httpx 抓 smzdm 搜索页（免费，无登录）
2. 抓取失败（网络受限/反爬）→ 返回空，由上层 AI 生成演示数据兜底
3. 严格遵守低频、只取公开页面
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import random
import re
from datetime import datetime
from typing import Optional

import httpx

log = logging.getLogger(__name__)

SMZDM_SEARCH = "https://search.smzdm.com/"

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Referer": "https://www.smzdm.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


class SocialSource:
    """社交真实成交价数据源"""

    platform = "social"
    platform_label = "全网晒价"

    def __init__(self, timeout: float = 8.0):
        self._client = httpx.Client(
            headers=_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            trust_env=False,  # 绕过系统代理直连
        )

    def fetch_deals(self, keyword: str, limit: int = 8) -> list[dict]:
        """抓取社交平台晒价记录

        Returns:
            [{"platform": "smzdm", "title": str, "price": float|None,
              "buy_platform": str|None, "coupon_info": str|None,
              "link": str, "source_text": str, "time": str}]
        """
        records: list[dict] = []
        # 1. 什么值得买
        try:
            records.extend(self._fetch_smzdm(keyword, limit))
        except Exception as e:
            log.warning(f"smzdm fetch failed: {e}")
        # 2. 兜底通道（可选实现）
        if not records:
            log.info("social sources unavailable, return empty (AI demo fallback)")
        return records[:limit]

    def _fetch_smzdm(self, keyword: str, limit: int) -> list[dict]:
        resp = self._client.get(
            SMZDM_SEARCH,
            params={"c": "home", "s": keyword, "order": "score", "v": "b"},
        )
        resp.raise_for_status()
        text = resp.text
        records: list[dict] = []
        # 页面内嵌 JSON 数据（feed 列表）
        # 标题/价格出现在 <h5> 或 JSON 里；尝试多种提取
        # 方式1: 内嵌 JSON
        items = re.findall(
            r'\{[^{}]*?"title"\s*:\s*"([^"]{8,100})"[^{}]*?"price"\s*:\s*"([\d.]+)"',
            text,
        )
        for title, price in items[:limit]:
            records.append(self._make_record(
                title=html_lib.unescape(title), price=float(price),
                link="", source_text="", coupon_info="",
            ))
        if len(records) >= limit:
            return records
        # 方式2: HTML 结构
        blocks = re.findall(
            r'<h5[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>([^<]{8,100})</a>',
            text,
        )
        for link, title in blocks[: limit - len(records)]:
            records.append(self._make_record(
                title=html_lib.unescape(title.strip()),
                price=None, link=link, source_text="", coupon_info="",
            ))
        return records

    def _make_record(self, title, price, link, source_text, coupon_info) -> dict:
        return {
            "platform": "smzdm",
            "title": title,
            "price": price,
            "buy_platform": None,
            "coupon_info": coupon_info,
            "link": link,
            "source_text": source_text,
            "time": datetime.utcnow().strftime("%Y-%m-%d"),
        }


class SocialDealAnalyzer:
    """社交成交数据 → AI 分析 → 结构化报告

    输入：社交晒价原始记录（可能为空）
    输出：成交价区间 / 案例列表 / AI 总结
    网络可用时抓真实数据；不可用时生成演示数据（标注来源=演示），
    AI 分析管道始终真实调用 DeepSeek。
    """

    def __init__(self, api_key: str = ""):
        # 确保 .env 已加载（与 ai_recommend 一致；幂等：setdefault 不覆盖已有值）
        try:
            from ..core.ai_recommend import _load_env
            _load_env()
        except Exception:
            pass
        import os
        self._api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self._client = httpx.Client(timeout=25.0, trust_env=False)

    def analyze(
        self,
        keyword: str,
        source: SocialSource | None = None,
        product_name: str = "",
        reference_price: float | None = None,
    ) -> dict:
        """分析某商品的社交真实成交价

        Returns:
            {
              "source": "social"|"demo",
              "deal_count": int,
              "price_range": {"low": float, "high": float, "avg": float},
              "common_platform": str,
              "deals": [{"title", "price", "buy_platform", "coupon_info", "link", "platform"}],
              "ai_summary": str,   # DeepSeek 生成的购买攻略
            }
        """
        # 1. 抓真实社交数据
        records = []
        if source is not None:
            try:
                records = source.fetch_deals(keyword, limit=8)
            except Exception as e:
                log.warning(f"social fetch failed: {e}")
        is_demo = not records

        # 2. 演示兜底：生成"看起来真实"的晒价记录（仅当真实抓取为空）
        if is_demo:
            records = self._gen_demo_deals(keyword, product_name, reference_price)

        # 3. 统计
        prices = [r["price"] for r in records if r.get("price")]
        price_range = {}
        if prices:
            price_range = {
                "low": round(min(prices), 2),
                "high": round(max(prices), 2),
                "avg": round(sum(prices) / len(prices), 2),
            }
        platforms = [r.get("buy_platform") for r in records if r.get("buy_platform")]
        common = max(set(platforms), key=platforms.count) if platforms else ""

        # 4. AI 分析（真实调用 DeepSeek）
        ai_summary = self._ai_analyze(keyword, records, product_name, reference_price)

        return {
            "source": "demo" if is_demo else "social",
            "deal_count": len(records),
            "price_range": price_range,
            "common_platform": common,
            "deals": records,
            "ai_summary": ai_summary,
        }

    def _gen_demo_deals(self, keyword: str, product_name: str, ref_price: float | None) -> list[dict]:
        """生成演示晒价记录（真实抓取不可用时兜底，AI 分析仍真实）"""
        rng = random.Random(abs(hash(keyword)) % 99999)
        base = ref_price or 6000.0
        name = product_name or keyword
        platforms = ["京东", "淘宝", "拼多多", "天猫"]
        coupons = [
            "叠加满3000减400券 + 店铺券",
            "百亿补贴直降 + 3期免息",
            "88VIP 95折 + 品牌券",
            "plus会员价 + 以旧换新补贴",
            "直播间领券 + 返现50",
            "预售定金翻倍 + 跨店满减",
        ]
        deals = []
        for i in range(6):
            ratio = rng.uniform(0.88, 0.97)
            price = round(base * ratio, 2)
            deals.append({
                "platform": "demo",
                "title": f"晒单：{name} 到手价",
                "price": price,
                "buy_platform": rng.choice(platforms),
                "coupon_info": rng.choice(coupons),
                "link": "",
                "source_text": "",
                "time": f"2026-0{rng.randint(1, 7)}-{rng.randint(1, 28):02d}",
            })
        return deals

    def _ai_analyze(self, keyword: str, deals: list[dict], product_name: str,
                    ref_price: float | None) -> str:
        """调 DeepSeek 分析晒价记录，生成购买攻略"""
        if not self._api_key:
            # 无 key：规则总结
            prices = [d["price"] for d in deals if d.get("price")]
            if not prices:
                return "暂无足够社交数据，无法生成成交价分析。"
            return (f"社交平台晒价 {len(deals)} 条，成交价集中在 "
                    f"¥{min(prices):.0f}~¥{max(prices):.0f}（均价 ¥{sum(prices)/len(prices):.0f}），"
                    f"最常见平台：{deals[0].get('buy_platform','')}。")
        try:
            import os
            payload = {
                "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                "thinking": {"type": "disabled"},
                "messages": [
                    {"role": "system", "content": (
                        "你是电商价格分析师。用户给你某商品在社交平台的晒价记录（JSON），"
                        "请分析：1.真实成交价区间和常见入手价；2.最常见的优惠组合方式；"
                        "3.给出 3-4 条具体购买建议（什么平台/什么时机/怎么凑优惠）。"
                        "硬性要求：只引用数据中出现的信息，禁止编造价格；"
                        "输出 150 字以内，用简洁中文，分点列出。"
                    )},
                    {"role": "user", "content": json.dumps({
                        "keyword": keyword,
                        "product": product_name,
                        "reference_price": ref_price,
                        "deals": deals,
                    }, ensure_ascii=False)},
                ],
                "max_tokens": 400,
            }
            resp = self._client.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return content.strip()
        except Exception as e:
            log.warning(f"social AI analyze failed: {e}")
            prices = [d["price"] for d in deals if d.get("price")]
            if prices:
                return (f"成交价参考：¥{min(prices):.0f}~¥{max(prices):.0f}，"
                        f"常见入手价 ¥{sum(prices)/len(prices):.0f}。")
            return "暂无社交成交数据。"
