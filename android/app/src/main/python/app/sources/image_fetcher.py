"""AllPrice — 真实商品图获取（Bing 图片搜索）

方案背景：SVG 剪影只是占位，用户要真实商品图。
- 小红书/京东等平台图片接口均需登录/签名，无法直接用
- Bing 图片搜索（images/search）免费、无登录、返回真实商品图原图 URL
- murl = 原图（可能有防盗链，前端 referrerpolicy=no-referrer + turl 兜底）
- turl = Bing CDN 缩略图（无防盗链，稳定可加载）

策略：
1. 按 品牌+型号+颜色 搜索，取首条合法结果（过滤图标/剪影站）
2. 磁盘缓存（7 天）；失败记录 1 小时不重试（避免反复打 Bing）
3. fetch_many 并发抓取，供前端一次性补图
4. 全部失败 → 返回空，前端保持 SVG 占位
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import httpx

log = logging.getLogger(__name__)

BING_IMAGES = "https://www.bing.com/images/search"

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 图标/剪影/素材站（不是商品实拍图，直接排除）
_BAD_DOMAINS = (
    "pngimg.com", "iconfont", "wikimedia", "svgsilh", "freepngimg", "pngwing",
    "cleanpng", "nicepng", "kindpng", "pngfind", "favpng", "vipng",
    "stickpng", "pngrepo", "flaticon", "icons8", "fontawesome", "gstatic",
    "pexels.com", "unsplash.com", "freepik.com", "shutterstock", "gettyimages",
    "dreamstime", "123rf.com", "istockphoto", "adobe.com/stock",
)
_GOOD_EXT = (".jpg", ".jpeg", ".png", ".webp")

# 缓存目录（与后端数据目录同级）
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "image_cache")

_CACHE_TTL = 7 * 86400        # 成功结果缓存 7 天
_FAIL_TTL = 3600              # 失败记录 1 小时不重试


class RealImageFetcher:
    """真实商品图获取器（Bing 图片搜索）"""

    def __init__(self, cache_dir: str = _CACHE_DIR, timeout: float = 8.0):
        self._cache_dir = cache_dir
        self._client = httpx.Client(
            headers=_HEADERS, timeout=timeout,
            follow_redirects=True, trust_env=False,
        )
        self._lock = threading.Lock()
        os.makedirs(self._cache_dir, exist_ok=True)

    # ── 对外接口 ──

    def fetch(self, key: str) -> dict:
        """获取单个商品的真实图；失败返回 {}（前端保留 SVG 占位）"""
        if not key:
            return {}
        hit = self._cache_get(key)
        if hit is not None:
            return hit
        brand, model, color = self._parse_key(key)
        result = self._search(brand, model, color)
        self._cache_put(key, result)
        return result

    def fetch_many(self, keys: list[str]) -> dict:
        """并发获取多个商品真实图；返回 {key: {murl, turl}}"""
        keys = [k for k in (keys or []) if k]
        if not keys:
            return {}
        out: dict = {}
        pending = []
        for k in keys:
            hit = self._cache_get(k)
            if hit is not None:
                out[k] = hit
            else:
                pending.append(k)
        if not pending:
            return out
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(self._fetch_uncached, pending))
        for k, r in zip(pending, results):
            if r:
                out[k] = r
        return out

    # ── 内部 ──

    def _fetch_uncached(self, key: str) -> dict:
        brand, model, color = self._parse_key(key)
        result = self._search(brand, model, color)
        self._cache_put(key, result)
        return result

    def _search(self, brand: str, model: str, color: str) -> dict:
        """Bing 图片搜索，返回 {murl, turl} 或 {}"""
        try:
            q = " ".join(x for x in (brand, model, color) if x) + " 实拍"
            resp = self._client.get(BING_IMAGES, params={"q": q, "first": "1"})
            resp.raise_for_status()
            text = resp.text
            blocks = re.findall(r'class="iusc"[^>]*m="([^"]+)"', text)
            for raw in blocks[:10]:
                try:
                    d = json.loads(html_lib.unescape(raw))
                except Exception:
                    continue
                murl = (d.get("murl") or "").strip()
                turl = (d.get("turl") or "").strip()
                if not murl or not murl.lower().endswith(_GOOD_EXT):
                    continue
                if any(bad in murl.lower() for bad in _BAD_DOMAINS):
                    continue
                if not murl.startswith("http"):
                    continue
                return {"murl": murl, "turl": turl}
        except Exception as e:
            log.warning(f"bing image search failed for {brand} {model}: {e}")
        return {}

    # ── key / 缓存 ──

    @staticmethod
    def make_key(brand: str, model: str, color: str = "") -> str:
        """商品图片缓存键：品牌|型号|颜色"""
        return "|".join(x.strip() for x in (brand or "", model or "", color or "") if x.strip())

    @staticmethod
    def _parse_key(key: str) -> tuple:
        parts = key.split("|")
        brand = parts[0] if len(parts) > 0 else ""
        model = parts[1] if len(parts) > 1 else ""
        color = parts[2] if len(parts) > 2 else ""
        return brand, model, color

    def _cache_path(self, key: str) -> str:
        import hashlib
        h = hashlib.md5(key.encode("utf-8")).hexdigest()
        return os.path.join(self._cache_dir, f"{h}.json")

    def _cache_get(self, key: str) -> Optional[dict]:
        try:
            path = self._cache_path(key)
            with self._lock:
                if not os.path.exists(path):
                    return None
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            age = time.time() - data.get("ts", 0)
            if data.get("ok") and age < _CACHE_TTL:
                return {"murl": data["murl"], "turl": data.get("turl", "")}
            if not data.get("ok") and age < _FAIL_TTL:
                return {}  # 失败缓存期内不再重试
            return None
        except Exception:
            return None

    def _cache_put(self, key: str, result: dict) -> None:
        try:
            path = self._cache_path(key)
            entry = {"ts": time.time(), "ok": bool(result), "murl": result.get("murl", ""), "turl": result.get("turl", "")}
            with self._lock:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(entry, f, ensure_ascii=False)
        except Exception as e:
            log.warning(f"image cache write failed: {e}")
