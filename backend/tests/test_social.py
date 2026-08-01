"""AllPrice — 社交数据源测试"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.sources.social import SocialSource, SocialDealAnalyzer


class TestSocialSource:
    def test_fetch_returns_list(self):
        """抓取失败也返回列表（不抛异常）"""
        s = SocialSource(timeout=2.0)
        deals = s.fetch_deals("iPhone 15 Pro", limit=5)
        assert isinstance(deals, list)
        assert len(deals) <= 5

    def test_record_structure(self):
        """记录结构包含关键字段"""
        s = SocialSource(timeout=2.0)
        rec = s._make_record("测试标题", 6999.0, "http://link", "来源", "优惠信息")
        assert rec["title"] == "测试标题"
        assert rec["price"] == 6999.0
        assert rec["buy_platform"] is None
        assert "time" in rec


class TestSocialDealAnalyzer:
    def test_demo_fallback_structure(self):
        """无真实数据时生成演示记录，结构完整（显式空 key → 规则路径）"""
        analyzer = SocialDealAnalyzer(api_key="")
        result = analyzer.analyze(
            "iPhone 15 Pro", None, "Apple iPhone 15 Pro", 6999.0
        )
        assert result["source"] == "demo"
        assert result["deal_count"] >= 3
        assert result["price_range"]["low"] < result["price_range"]["high"]
        # 无 key → 规则总结
        assert "成交" in result["ai_summary"] or "¥" in result["ai_summary"]

    def test_demo_deals_have_platform_and_coupon(self):
        """演示记录包含购买平台和优惠方式"""
        analyzer = SocialDealAnalyzer()
        deals = analyzer._gen_demo_deals("耳机", "测试耳机", 2000.0)
        for d in deals:
            assert d["buy_platform"] in ("京东", "淘宝", "拼多多", "天猫")
            assert d["coupon_info"]
            assert d["price"] < 2000.0

    def test_analyze_with_mock_source(self):
        """注入 mock source 返回 2 条记录 → source=social"""
        class FakeSource:
            def fetch_deals(self, keyword, limit=8):
                return [
                    {"platform": "smzdm", "title": "晒单1", "price": 6100.0,
                     "buy_platform": "拼多多", "coupon_info": "百亿补贴",
                     "link": "", "source_text": "", "time": "2026-06-01"},
                    {"platform": "smzdm", "title": "晒单2", "price": 6500.0,
                     "buy_platform": "京东", "coupon_info": "满减",
                     "link": "", "source_text": "", "time": "2026-06-10"},
                ]

        analyzer = SocialDealAnalyzer(api_key="")
        result = analyzer.analyze("iPhone", FakeSource(), "", 6999.0)
        assert result["source"] == "social"
        assert result["deal_count"] == 2
        assert result["price_range"]["low"] == 6100.0
        # 常见平台在数据源平台集合内（规则路径=拼多多）
        assert result["common_platform"] in ("拼多多", "京东")
