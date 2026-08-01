"""商品归一化引擎测试"""
import pytest

from app.core.normalizer import ProductNormalizer
from app.models import PlatformOffer


def make_offer(platform, product_id, title, price, params=None, list_price=0):
    return PlatformOffer(
        platform=platform,
        platform_label={"jd": "京东", "taobao": "淘宝", "pdd": "拼多多"}.get(platform, platform),
        product_id=product_id,
        url=f"https://{platform}.com/{product_id}",
        title=title,
        list_price=list_price or price,
        sale_price=price,
        final_price=price,
        params=params or {},
    )


class TestNormalize:
    def test_same_product_across_platforms(self):
        """同一商品（同型号同规格）跨平台合并为一个 Product"""
        jd = make_offer("jd", "10001", "Apple iPhone 15 Pro 256G 原色钛金属",
                        6999, {"品牌": "Apple", "型号": "iPhone 15 Pro", "容量": "256G"})
        tb = make_offer("taobao", "20001", "苹果 iPhone15 Pro 256GB 原色",
                        6799, {"品牌": "苹果", "型号": "iPhone 15 Pro", "存储": "256GB"})
        pdd = make_offer("pdd", "30001", "Apple iPhone 15 Pro 256G",
                        6599, {"品牌": "Apple", "型号": "iPhone 15 Pro", "内存": "256G"})

        products = ProductNormalizer().normalize([jd, tb, pdd])
        assert len(products) == 1
        assert len(products[0].offers) == 3
        # 按最低价排序
        assert products[0].offers[0].platform == "pdd"

    def test_different_models_not_merged(self):
        """不同型号不合并"""
        p1 = make_offer("jd", "1", "iPhone 15 Pro 256G", 6999, {"品牌": "Apple", "型号": "iPhone 15 Pro"})
        p2 = make_offer("jd", "2", "iPhone 15 256G", 5999, {"品牌": "Apple", "型号": "iPhone 15"})
        products = ProductNormalizer().normalize([p1, p2])
        assert len(products) == 2

    def test_different_capacity_not_merged(self):
        """同型号不同容量不合并"""
        p1 = make_offer("jd", "1", "iPhone 15 Pro 128G", 6999,
                        {"品牌": "Apple", "型号": "iPhone 15 Pro", "容量": "128G"})
        p2 = make_offer("jd", "2", "iPhone 15 Pro 256G", 7999,
                        {"品牌": "Apple", "型号": "iPhone 15 Pro", "容量": "256G"})
        products = ProductNormalizer().normalize([p1, p2])
        assert len(products) == 2

    def test_spec_key_normalization(self):
        """'内存:256G' 与 '存储:256GB' 视为同一维度（键名归一化后合并）"""
        p1 = make_offer("jd", "1", "iPhone 15 256G", 5999, {"品牌": "Apple", "型号": "iPhone 15", "内存": "256G"})
        p2 = make_offer("tb", "2", "iPhone 15 256GB", 5899, {"品牌": "苹果", "型号": "iPhone 15", "存储": "256GB"})
        products = ProductNormalizer().normalize([p1, p2])
        assert len(products) == 1  # 键名归一化后合并为同一商品
        assert len(products[0].offers) == 2

    def test_best_offer(self):
        jd = make_offer("jd", "1", "iPhone 15 256G", 5999, {"品牌": "Apple", "型号": "iPhone 15"})
        pdd = make_offer("pdd", "2", "iPhone 15 256G", 5499, {"品牌": "Apple", "型号": "iPhone 15"})
        products = ProductNormalizer().normalize([jd, pdd])
        best = products[0].best_offer()
        assert best.platform == "pdd"
        assert best.final_price == 5499

    def test_title_clean(self):
        jd = make_offer("jd", "1", "【官方】Apple iPhone 15 Pro 256G 京东自营", 6999,
                        {"品牌": "Apple", "型号": "iPhone 15 Pro"})
        products = ProductNormalizer().normalize([jd])
        assert "【官方】" not in products[0].name
        assert "京东" not in products[0].name
