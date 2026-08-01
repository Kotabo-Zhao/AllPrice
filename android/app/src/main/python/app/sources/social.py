"""AllPrice — 社交平台数据源（真实成交价）

从社交/晒价平台抓取"用户实际买到多少钱、怎么买的"：
- 什么值得买（smzdm）：晒价文章，含价格/平台/优惠方式
- 小红书（xhs）：通过搜索摘要抓笔记标题（小红书无公开 API，SPA 加密，
  使用搜索引擎摘要通道 best-effort 抓取；抓不到则由 AI 演示兜底）

策略：
1. 主通道 httpx 抓 smzdm 搜索页（免费，无登录）
2. 小红书通道：搜索引擎抓笔记标题/摘要（含"到手价"信息）
3. 抓取失败（网络受限/反爬）→ 返回空，由上层 AI 生成演示数据兜底
4. 严格遵守低频、只取公开页面
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
BING_SEARCH = "https://www.bing.com/search"

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
        """抓取社交平台晒价记录（多源合并：什么值得买 + 小红书）

        Returns:
            [{"platform": "smzdm"|"xhs", "title": str, "price": float|None,
              "buy_platform": str|None, "coupon_info": str|None,
              "link": str, "source_text": str, "time": str}]
        """
        records: list[dict] = []
        # 1. 什么值得买
        try:
            records.extend(self._fetch_smzdm(keyword, limit))
        except Exception as e:
            log.warning(f"smzdm fetch failed: {e}")
        # 2. 小红书（搜索引擎通道）
        try:
            records.extend(self._fetch_xhs(keyword, max(2, limit - len(records))))
        except Exception as e:
            log.warning(f"xhs fetch failed: {e}")
        # 3. 兜底通道（可选实现）
        if not records:
            log.info("social sources unavailable, return empty (AI demo fallback)")
        return records[:limit]

    def _fetch_xhs(self, keyword: str, limit: int) -> list[dict]:
        """小红书通道：搜索引擎摘要抓取（best-effort）

        小红书网页版为 SPA + 签名加密，无法直接抓取正文；
        通过搜索引擎结果摘要获取笔记标题/价格信息。
        """
        resp = self._client.get(
            BING_SEARCH,
            params={"q": f"小红书 {keyword} 到手价", "count": str(limit + 4)},
        )
        resp.raise_for_status()
        text = resp.text
        records: list[dict] = []
        blocks = re.findall(r'<li class="b_algo">(.*?)</li>', text, re.S)
        for it in blocks:
            if len(records) >= limit:
                break
            h = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', it, re.S)
            if not h:
                continue
            url, title = h.group(1), re.sub(r"<[^>]+>", "", h.group(2))
            title = html_lib.unescape(title.strip())
            if not title:
                continue
            # 只保留小红书相关结果
            is_xhs = "xiaohongshu" in url or "小红书" in title
            if not is_xhs and "笔记" not in title and "晒" not in title:
                continue
            # 摘要中提取价格（xx 到手/实付 xx 元）
            cap = ""
            c = re.search(r"<p[^>]*>(.*?)</p>", it, re.S)
            if c:
                cap = re.sub(r"<[^>]+>", "", c.group(1))
            price = self._extract_price(title + " " + cap)
            records.append({
                "platform": "xhs",
                "title": title[:60],
                "price": price,
                "buy_platform": "小红书",
                "coupon_info": self._extract_coupon(title + " " + cap),
                "link": url,
                "source_text": cap[:120],
                "time": datetime.utcnow().strftime("%Y-%m-%d"),
            })
        return records

    @staticmethod
    def _extract_price(text: str) -> Optional[float]:
        """从标题/摘要提取价格（到手价 xx 元 / ¥xx / 实付 xx）"""
        patterns = [
            r"到手(?:仅|才)?\s*[¥￥]?\s*(\d{2,5}(?:\.\d+)?)",
            r"实付\s*[¥￥]?\s*(\d{2,5}(?:\.\d+)?)",
            r"[¥￥]\s*(\d{2,5}(?:\.\d+)?)",
            r"(\d{2,5}(?:\.\d+)?)\s*元",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    continue
        return None

    @staticmethod
    def _extract_coupon(text: str) -> str:
        """提取优惠方式关键词"""
        for kw in ("百亿补贴", "满减", "券", "补贴", "88VIP", "会员价", "免息", "返现", "直播间"):
            if kw in text:
                return f"含{kw}优惠"
        return ""

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
        """生成演示晒价记录（真实抓取不可用时兜底，AI 分析仍真实）

        小红书风格笔记 + 什么值得买晒单混合，来源平台标注 xhs/smzdm。
        """
        rng = random.Random(abs(hash(keyword)) % 99999)
        base = ref_price or 6000.0
        name = product_name or keyword
        # 小红书笔记标题模板
        xhs_titles = [
            f"谁懂啊！{name}到手价被我砍下来了！",
            f"蹲了两个月，{name}终于降到这个价",
            f"姐妹们冲！{name}这个价格真的可以了",
            f"{name}抄作业，照着买不踩雷",
            f"实测{name}，这个价格全网难找",
            f"刚入手{name}，附上我的省钱攻略",
        ]
        smzdm_titles = [
            f"【神价格】{name} 历史低价实测",
            f"好价：{name} 百亿补贴到手价",
            f"{name} 近期好价，叠加优惠后真香",
            f"历史低价：{name} 券后实付",
        ]
        platforms = ["京东", "淘宝", "拼多多", "天猫"]
        coupons = [
            "叠加满3000减400券 + 店铺券",
            "百亿补贴直降 + 3期免息",
            "88VIP 95折 + 品牌券",
            "plus会员价 + 以旧换新补贴",
            "直播间领券 + 返现50",
            "预售定金翻倍 + 跨店满减",
        ]
        xhs_coupons = [
            "凑单满减 + 88VIP 到手",
            "直播间券 + 品牌会员价",
            "拼单成功，人均再省 60",
            "叠加平台券 + 店铺券双重优惠",
            "比价三平台后选的最优解",
        ]
        deals = []
        for i in range(6):
            ratio = rng.uniform(0.88, 0.97)
            price = round(base * ratio, 2)
            if i % 3 == 0:
                # 小红书笔记
                deals.append({
                    "platform": "xhs",
                    "title": rng.choice(xhs_titles),
                    "price": price,
                    "buy_platform": rng.choice(platforms),
                    "coupon_info": rng.choice(xhs_coupons),
                    "link": "",
                    "source_text": "",
                    "time": f"2026-0{rng.randint(1, 7)}-{rng.randint(1, 28):02d}",
                })
            else:
                # 什么值得买晒单
                deals.append({
                    "platform": "smzdm",
                    "title": rng.choice(smzdm_titles),
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
