"""淘宝/拼多多适配器测试 — mock 解析逻辑，不访问真实网络"""
import pytest
from unittest.mock import MagicMock, patch

from app.sources.taobao import TaobaoSource
from app.sources.pdd import PddSource


class TestTaobaoParsing:
    def test_parse_price(self):
        assert TaobaoSource._parse_price("¥5999.00") == 5999.0
        assert TaobaoSource._parse_price("1,299 元") == 1299.0
        assert TaobaoSource._parse_price("") == 0.0
        assert TaobaoSource._parse_price("无价格") == 0.0

    def test_build_offer(self):
        src = TaobaoSource()
        offer = src._build_offer("123456789", "Apple iPhone 15 Pro 256G", 6999.0, "iphone")
        assert offer.platform == "taobao"
        assert offer.product_id == "123456789"
        assert offer.url == "https://item.taobao.com/item.htm?id=123456789"
        assert offer.final_price == 6999.0

    def test_parse_initial_data(self):
        """从内嵌 JSON 提取商品"""
        src = TaobaoSource()
        raw = {
            "items": [
                {"itemId": "111", "title": "苹果 iPhone15 256G", "priceText": "¥5999"},
                {"itemId": "222", "title": "华为 Mate 60", "priceText": "¥6999"},
            ]
        }
        offers = src._parse_initial_data(raw, "iphone", 5)
        assert len(offers) == 2
        assert offers[0].product_id == "111"
        assert offers[0].final_price == 5999.0

    def test_search_degrades_gracefully(self):
        """playwright 不可用或网络失败时返回空（不崩溃）"""
        src = TaobaoSource()
        with patch.object(src, "_ensure_browser", side_effect=RuntimeError("no playwright")):
            offers = src.search("iphone")
        assert offers == []


class TestPddParsing:
    def test_parse_price(self):
        assert PddSource._parse_price("拼团价 ¥5499") == 5499.0
        assert PddSource._parse_price("") == 0.0

    def test_build_offer(self):
        src = PddSource()
        offer = src._build_offer("123456789012", "iPhone 15 Pro 256G 拼多多", 5499.0, "iphone", "已拼10万+")
        assert offer.platform == "pdd"
        assert offer.url == "https://mobile.yangkeduo.com/goods.html?goods_id=123456789012"
        assert offer.sales == "已拼10万+"

    def test_parse_raw_data_with_fen(self):
        """拼多多价格单位是分，需转元"""
        src = PddSource()
        raw = {
            "goodsList": [
                {"goods_id": "999", "goods_name": "iPhone 15 128G", "min_group_price": 549900},
            ]
        }
        offers = src._parse_raw_data(raw, "iphone", 5)
        assert len(offers) == 1
        assert offers[0].final_price == 5499.0  # 549900分 → 5499元

    def test_parse_raw_data_yuan(self):
        """已是元的场景不重复转换"""
        src = PddSource()
        raw = {"items": [{"goodsId": "888", "goodsName": "小米14", "price": 4999.0}]}
        offers = src._parse_raw_data(raw, "小米", 5)
        assert offers[0].final_price == 4999.0

    def test_search_degrades_gracefully(self):
        src = PddSource()
        with patch.object(src, "_ensure_browser", side_effect=RuntimeError("no playwright")):
            offers = src.search("iphone")
        assert offers == []
