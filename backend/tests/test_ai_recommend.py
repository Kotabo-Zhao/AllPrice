"""AI 推荐服务测试 — 覆盖规则降级 + mock AI 响应"""
import pytest
from unittest.mock import MagicMock, patch

from app.core.ai_recommend import AIRecommendService
from app.models import PlatformOffer, Product


def make_product():
    offers = [
        PlatformOffer(platform="jd", platform_label="京东", product_id="1",
                      url="u", title="iPhone 15 Pro 256G", list_price=7999,
                      sale_price=6999, final_price=6699,
                      price_detail="满300减50 → 到手 6699元",
                      coupons=[], params={"品牌": "Apple", "型号": "iPhone 15 Pro"}),
        PlatformOffer(platform="pdd", platform_label="拼多多", product_id="2",
                      url="u", title="iPhone 15 Pro 256G", list_price=6999,
                      sale_price=6599, final_price=6099,
                      price_detail="百亿补贴直降 → 到手 6099元",
                      coupons=[], params={"品牌": "Apple", "型号": "iPhone 15 Pro"}),
        PlatformOffer(platform="taobao", platform_label="淘宝", product_id="3",
                      url="u", title="iPhone 15 Pro 256G", list_price=7899,
                      sale_price=6899, final_price=6799,
                      price_detail="当前价", coupons=[],
                      params={"品牌": "Apple", "型号": "iPhone 15 Pro"}),
    ]
    return Product(sku_fingerprint="abc", name="iPhone 15 Pro 256G",
                   brand="Apple", model="iPhone 15 Pro", offers=offers)


class TestRuleFallback:
    @pytest.fixture(autouse=True)
    def _no_key(self, monkeypatch):
        """模拟无 key 环境（真实 .env 可能已配 key）"""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def test_no_key_uses_rule(self, _no_key):
        """无 API key → 规则推荐"""
        svc = AIRecommendService(api_key="")
        assert not svc.available
        result = svc.recommend(make_product())
        assert result["source"] == "rule"
        assert "拼多多" in result["recommendation"]  # 拼多多最低价 6099

    def test_rule_mentions_best_price(self, _no_key):
        svc = AIRecommendService(api_key="")
        result = svc.recommend(make_product())
        assert "6099" in result["recommendation"]

    def test_empty_product(self, _no_key):
        svc = AIRecommendService(api_key="")
        p = Product(sku_fingerprint="x", name="空商品", offers=[])
        result = svc.recommend(p)
        assert result["source"] == "rule"


class TestAIWithKey:
    def test_ai_success(self):
        """有 key 且 AI 返回 → ai 来源"""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "推荐拼多多 ¥6099，百亿补贴最划算"}}]
        }
        svc = AIRecommendService(api_key="sk-test")
        assert svc.available
        with patch.object(svc._client, "post", return_value=mock_resp) as mock_post:
            result = svc.recommend(make_product())
        assert result["source"] == "ai"
        assert "拼多多" in result["recommendation"]
        # 验证请求体包含真实价格数据
        sent = mock_post.call_args[1]["json"]
        assert "6099" in sent["messages"][1]["content"]

    def test_ai_failure_falls_back(self):
        """AI 调用失败 → 静默降级规则推荐（不影响主流程）"""
        svc = AIRecommendService(api_key="sk-test")
        with patch.object(svc._client, "post", side_effect=Exception("timeout")):
            result = svc.recommend(make_product())
        assert result["source"] == "rule"
        assert "拼多多" in result["recommendation"]

    def test_ai_http_error_falls_back(self):
        svc = AIRecommendService(api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 429")
        with patch.object(svc._client, "post", return_value=mock_resp):
            result = svc.recommend(make_product())
        assert result["source"] == "rule"


class TestAIContext:
    def test_context_has_real_prices_only(self):
        """AI 上下文只含真实报价字段"""
        svc = AIRecommendService(api_key="")
        ctx = svc._build_ai_context(make_product())
        assert ctx["商品"] == "iPhone 15 Pro 256G"
        assert len(ctx["各平台报价"]) == 3
        prices = [o["优惠后到手价"] for o in ctx["各平台报价"]]
        assert 6099 in prices and 6699 in prices and 6799 in prices
