"""API 集成测试 — 用 mock 数据源，不依赖真实网络"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import PlatformOffer
from app.sources.base import SourceRegistry


class MockJDSource:
    """模拟京东数据源（返回固定数据）"""
    platform = "jd"
    platform_label = "京东"

    def search(self, keyword: str, limit: int = 10) -> list[PlatformOffer]:
        return [
            PlatformOffer(
                platform="jd", platform_label="京东", product_id="10001",
                url="https://item.jd.com/10001.html",
                title="Apple iPhone 15 Pro 256G 原色钛金属",
                list_price=7999, sale_price=6999, final_price=6699,
                params={"品牌": "Apple", "型号": "iPhone 15 Pro", "容量": "256G"},
            )
        ]


@pytest.fixture(scope="module")
def client():
    # 注入 mock 数据源
    registry = SourceRegistry()
    registry.register(MockJDSource())
    app.state.registry = registry
    with TestClient(app) as c:
        yield c


class TestHealth:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_platforms(self, client):
        r = client.get("/api/platforms")
        assert r.status_code == 200
        data = r.json()
        assert "jd" in data["platforms"][0]["key"]
        assert data["healthy"] == ["jd"]


class TestSearch:
    def test_search_returns_product(self, client):
        r = client.get("/api/search", params={"keyword": "iPhone 15 Pro"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 1
        product = data["products"][0]
        assert product["name"]
        assert product["offers"][0]["platform"] == "jd"
        assert product["best_price"] == 6699

    def test_search_validation(self, client):
        """空关键词返回 422"""
        r = client.get("/api/search")
        assert r.status_code == 422

    def test_search_long_keyword(self, client):
        """超长关键词 422"""
        r = client.get("/api/search", params={"keyword": "x" * 100})
        assert r.status_code == 422

    def test_search_limit(self, client):
        r = client.get("/api/search", params={"keyword": "iphone", "limit": 1})
        assert r.status_code == 200

    def test_unknown_route_404(self, client):
        r = client.get("/api/nonexistent")
        assert r.status_code == 404
