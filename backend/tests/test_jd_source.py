"""京东数据源测试 — mock httpx 响应，不访问真实网络"""
import pytest
from unittest.mock import patch, MagicMock

from app.sources.jd import JDSource


class TestJDPriceParsing:
    def test_get_prices_parses(self):
        """价格接口 JSON 解析正确"""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [
            {"id": "J_10001", "p": "6999.00", "m": "7999.00", "op": "6999.00"},
            {"id": "J_10002", "p": "5499.00", "m": "6499.00", "op": "5499.00"},
        ]
        source = JDSource()
        with patch.object(source.client, "get", return_value=mock_resp):
            prices = source.get_prices(["10001", "10002"])
        assert prices["10001"]["price"] == 6999.0
        assert prices["10001"]["market_price"] == 7999.0
        assert prices["10002"]["price"] == 5499.0

    def test_get_prices_empty_on_error(self):
        """接口异常返回空 dict（不崩溃）"""
        source = JDSource()
        with patch.object(source.client, "get", side_effect=Exception("network")):
            prices = source.get_prices(["10001"])
        assert prices == {}

    def test_get_price_single(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [{"id": "J_10001", "p": "99.00", "m": "199.00", "op": "99.00"}]
        source = JDSource()
        with patch.object(source.client, "get", return_value=mock_resp):
            price = source.get_price("10001")
        assert price["price"] == 99.0


class TestJDSearch:
    def test_search_skus_regex(self):
        """从搜索页 HTML 提取商品 ID"""
        html = '''
        <li data-sku="100012043978" class="gl-item">
        <a href="//item.jd.com/100012043979.html">
        <div class="p-price"><strong>4999.00</strong>
        '''
        source = JDSource()
        with patch.object(source.client, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.text = html
            mock_get.return_value = mock_resp
            skus = source._search_skus("iphone", 5)
        assert "100012043978" in skus
        assert "100012043979" in skus

    def test_search_no_results(self):
        """搜索无结果返回空列表"""
        source = JDSource()
        with patch.object(source.client, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.text = "<html>没有商品</html>"
            mock_get.return_value = mock_resp
            skus = source._search_skus("不存在的东西", 5)
        assert skus == []

    def test_search_network_error(self):
        source = JDSource()
        with patch.object(source.client, "get", side_effect=Exception("timeout")):
            skus = source._search_skus("iphone", 5)
        assert skus == []


class TestJDDetail:
    def test_detail_parse(self):
        """详情接口标题/图片解析"""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "data": {
                "skuName": "Apple iPhone 15 Pro 256G 原色钛金属",
                "brand": "Apple",
                "image": "//img12.360buyimg.com/n1/s450x450_jfs.jpg",
            }
        }
        source = JDSource()
        with patch.object(source.client, "get", return_value=mock_resp):
            detail = source._get_detail_cached("10001")
        assert "iPhone" in detail["title"]
        assert detail["image"].startswith("https:")
        assert detail["params"]["品牌"] == "Apple"

    def test_detail_cache(self):
        """详情接口有 60s 缓存（第二次不重复请求）"""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"data": {"skuName": "测试商品", "brand": "Test", "image": ""}}
        source = JDSource()
        with patch.object(source.client, "get", return_value=mock_resp) as mock_get:
            source._get_detail_cached("20001")
            source._get_detail_cached("20001")
        assert mock_get.call_count == 1  # 第二次命中缓存
