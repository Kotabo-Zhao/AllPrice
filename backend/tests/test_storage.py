"""SQLite 存储层测试 — 用临时数据库"""
import pytest
import os
import tempfile

from app.core.storage import Storage
from app.models import Product, PlatformOffer


@pytest.fixture()
def storage():
    """每个测试独立临时库"""
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "test.db")
    s = Storage(db)
    yield s
    # 清理
    try:
        os.remove(db)
    except Exception:
        pass


class TestPriceHistory:
    def test_save_and_query(self, storage):
        storage.save_price_snapshot("fp1", "jd", "京东", "iPhone 15", 6999, 6699, "满减")
        storage.save_price_snapshot("fp1", "pdd", "拼多多", "iPhone 15", 6599, 6099, "百亿补贴")
        rows = storage.get_price_history("fp1")
        assert len(rows) == 2
        assert rows[0]["platform"] == "jd"
        assert rows[0]["final_price"] == 6699.0

    def test_query_by_platform(self, storage):
        storage.save_price_snapshot("fp2", "jd", "京东", "iPhone 15", 6999, 6699, "")
        storage.save_price_snapshot("fp2", "pdd", "拼多多", "iPhone 15", 6599, 6099, "")
        rows = storage.get_price_history("fp2", platform="jd")
        assert len(rows) == 1
        assert rows[0]["platform"] == "jd"

    def test_query_unknown_fp_empty(self, storage):
        assert storage.get_price_history("nonexistent") == []

    def test_lowest_price(self, storage):
        storage.save_price_snapshot("fp3", "jd", "京东", "iPhone 15", 6999, 6699, "")
        storage.save_price_snapshot("fp3", "pdd", "拼多多", "iPhone 15", 6599, 6099, "")
        storage.save_price_snapshot("fp3", "taobao", "淘宝", "iPhone 15", 6899, 6799, "")
        assert storage.get_lowest_price("fp3") == 6099.0

    def test_lowest_none_for_unknown(self, storage):
        assert storage.get_lowest_price("unknown") is None

    def test_days_filter(self, storage):
        """超过 days 的历史不返回"""
        storage.save_price_snapshot("fp4", "jd", "京东", "iPhone", 6999, 6699, "")
        rows = storage.get_price_history("fp4", days=7)
        assert len(rows) == 1  # 刚写入的在7天内


class TestProductCache:
    def test_cache_and_get(self, storage):
        offer = PlatformOffer(platform="jd", platform_label="京东", product_id="1",
                              url="u", title="iPhone 15 Pro 256G", list_price=7999,
                              sale_price=6999, final_price=6699,
                              params={"品牌": "Apple", "型号": "iPhone 15 Pro"})
        p = Product(sku_fingerprint="fp5", name="iPhone 15 Pro 256G",
                    brand="Apple", model="iPhone 15 Pro", specs={"内存": "256G"}, offers=[offer])
        storage.cache_product(p)
        cached = storage.get_cached_product("fp5")
        assert cached is not None
        assert cached["name"] == "iPhone 15 Pro 256G"
        assert cached["specs"]["内存"] == "256G"

    def test_get_unknown(self, storage):
        assert storage.get_cached_product("nope") is None

    def test_overwrite(self, storage):
        offer = PlatformOffer(platform="jd", platform_label="京东", product_id="1",
                              url="u", title="旧名", list_price=1, sale_price=1, final_price=1)
        p1 = Product(sku_fingerprint="fp6", name="旧名", offers=[offer])
        storage.cache_product(p1)
        p2 = Product(sku_fingerprint="fp6", name="新名", offers=[offer])
        storage.cache_product(p2)
        assert storage.get_cached_product("fp6")["name"] == "新名"


class TestStats:
    def test_stats(self, storage):
        storage.save_price_snapshot("fp7", "jd", "京东", "iPhone", 6999, 6699, "")
        offer = PlatformOffer(platform="jd", platform_label="京东", product_id="1",
                              url="u", title="iPhone", list_price=1, sale_price=1, final_price=1)
        storage.cache_product(Product(sku_fingerprint="fp7", name="iPhone", offers=[offer]))
        stats = storage.stats()
        assert stats["snapshots"] >= 1
        assert stats["products"] >= 1
