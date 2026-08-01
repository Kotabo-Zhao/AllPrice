"""AllPrice — SQLite 持久化存储

职责：
- 保存每次搜索的价格快照（走势图数据源）
- 查询商品历史价格（90天趋势）
- 缓存商品/报价（减少重复抓取）

表结构：
- price_history: 价格历史快照（商品指纹 + 平台 + 价格 + 时间）
- product_cache: 商品归一化缓存（指纹 → 商品名/规格）

用 SQLite（零配置，单文件），后续量大再迁 PostgreSQL。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from ..models import Product

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_fingerprint TEXT NOT NULL,
    platform TEXT NOT NULL,
    platform_label TEXT,
    product_name TEXT,
    price REAL NOT NULL,
    final_price REAL NOT NULL,
    price_detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ph_sku_time ON price_history(sku_fingerprint, created_at);
CREATE INDEX IF NOT EXISTS idx_ph_time ON price_history(created_at);

CREATE TABLE IF NOT EXISTS product_cache (
    sku_fingerprint TEXT PRIMARY KEY,
    name TEXT,
    brand TEXT,
    model TEXT,
    specs TEXT,
    image_url TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pc_name ON product_cache(name);
"""


class Storage:
    """SQLite 存储层"""

    def __init__(self, db_path: str = ""):
        if not db_path:
            db_path = os.path.join(os.path.dirname(__file__), "..", "allprice.db")
        self._path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(SCHEMA)
                conn.commit()
            finally:
                conn.close()

    # ── 价格历史 ──

    def save_price_snapshot(
        self,
        sku_fingerprint: str,
        platform: str,
        platform_label: str,
        product_name: str,
        price: float,
        final_price: float,
        price_detail: str = "",
    ):
        """保存一次价格快照（每次搜索落一条）"""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(
                        """INSERT INTO price_history
                           (sku_fingerprint, platform, platform_label, product_name,
                            price, final_price, price_detail, created_at)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            sku_fingerprint, platform, platform_label, product_name,
                            price, final_price, price_detail,
                            datetime.utcnow().isoformat(),
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            log.warning(f"save_price_snapshot failed: {e}")

    def get_price_history(
        self,
        sku_fingerprint: str,
        days: int = 90,
        platform: str = "",
    ) -> list[dict]:
        """查询商品历史价格（按时间升序，用于走势图）"""
        try:
            since = (datetime.utcnow() - timedelta(days=days)).isoformat()
            sql = (
                "SELECT platform, platform_label, final_price, price_detail, created_at "
                "FROM price_history WHERE sku_fingerprint=? AND created_at>=?"
            )
            params: list = [sku_fingerprint, since]
            if platform:
                sql += " AND platform=?"
                params.append(platform)
            sql += " ORDER BY created_at ASC"
            with self._lock:
                conn = self._connect()
                try:
                    rows = conn.execute(sql, params).fetchall()
                    return [dict(r) for r in rows]
                finally:
                    conn.close()
        except Exception as e:
            log.warning(f"get_price_history failed: {e}")
            return []

    def get_lowest_price(self, sku_fingerprint: str, days: int = 90) -> Optional[float]:
        """查询历史最低到手价"""
        try:
            since = (datetime.utcnow() - timedelta(days=days)).isoformat()
            with self._lock:
                conn = self._connect()
                try:
                    row = conn.execute(
                        "SELECT MIN(final_price) FROM price_history "
                        "WHERE sku_fingerprint=? AND created_at>=?",
                        (sku_fingerprint, since),
                    ).fetchone()
                    return row[0] if row and row[0] is not None else None
                finally:
                    conn.close()
        except Exception as e:
            log.warning(f"get_lowest_price failed: {e}")
            return None

    # ── 商品缓存 ──

    def cache_product(self, product: Product):
        """缓存商品归一化结果（按指纹）"""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO product_cache
                           (sku_fingerprint, name, brand, model, specs, image_url, updated_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (
                            product.sku_fingerprint, product.name, product.brand,
                            product.model, json.dumps(product.specs, ensure_ascii=False),
                            product.image_url, datetime.utcnow().isoformat(),
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            log.warning(f"cache_product failed: {e}")

    def get_cached_product(self, sku_fingerprint: str) -> Optional[dict]:
        try:
            with self._lock:
                conn = self._connect()
                try:
                    row = conn.execute(
                        "SELECT * FROM product_cache WHERE sku_fingerprint=?",
                        (sku_fingerprint,),
                    ).fetchone()
                    if not row:
                        return None
                    d = dict(row)
                    d["specs"] = json.loads(d.get("specs") or "{}")
                    return d
                finally:
                    conn.close()
        except Exception as e:
            log.warning(f"get_cached_product failed: {e}")
            return None

    def stats(self) -> dict:
        """存储统计（健康检查用）"""
        try:
            with self._lock:
                conn = self._connect()
                try:
                    snapshots = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
                    products = conn.execute("SELECT COUNT(*) FROM product_cache").fetchone()[0]
                    return {"snapshots": snapshots, "products": products}
                finally:
                    conn.close()
        except Exception as e:
            return {"error": str(e)}
