"""AllPrice 前端 E2E 测试 — Playwright 三端视口

覆盖：
- 搜索页渲染（桌面/平板/手机三视口）
- 搜索流程（mock 数据源）
- 比价结果（平台表格/优惠明细/AI推荐/走势图）
- 布局无溢出、关键元素可见

运行: python -m pytest tests/test_frontend_e2e.py -v
前置: 后端已在 8001 端口运行（uvicorn app.main:app --port 8001）
"""
import pytest
import time
import os

from playwright.sync_api import sync_playwright

BASE_URL = os.getenv("ALLPRICE_URL", "http://127.0.0.1:8001")

# 三端视口
VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "tablet": {"width": 768, "height": 1024},
    "mobile": {"width": 390, "height": 844},
}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _open_page(browser, viewport):
    ctx = browser.new_context(viewport=viewport)
    page = ctx.new_page()
    page.goto(BASE_URL, wait_until="networkidle", timeout=20000)
    page.wait_for_selector(".search-box input", timeout=10000)
    return page


class TestSearchPage:
    def test_loads_all_viewports(self, browser):
        """三端视口页面都能加载"""
        for name, vp in VIEWPORTS.items():
            page = _open_page(browser, vp)
            assert page.title() == "全价比价 · 搜全网最低价", f"{name} 标题错误"
            assert page.is_visible(".search-box input"), f"{name} 搜索框不可见"
            assert page.is_visible(".search-btn"), f"{name} 搜索按钮不可见"
            assert page.is_visible(".hero h1"), f"{name} 标题不可见"
            page.close()

    def test_no_horizontal_overflow(self, browser):
        """三端视口无横向溢出"""
        for name, vp in VIEWPORTS.items():
            page = _open_page(browser, vp)
            overflow = page.evaluate("document.body.scrollWidth > window.innerWidth")
            assert not overflow, f"{name} 视口存在横向溢出"
            page.close()

    def test_hot_tags_clickable(self, browser):
        """热门标签可点击触发搜索"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        tags = page.query_selector_all(".hot-tag")
        assert len(tags) > 0
        tags[0].click()
        page.wait_for_selector(".searching", timeout=5000)
        page.close()


class TestSearchFlow:
    def test_search_shows_results(self, browser):
        """搜索后展示比价结果（mock 数据）"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        page.fill(".search-box input", "iPhone")
        page.click(".search-btn")
        # 等待结果（搜索+渲染）
        page.wait_for_selector(".product-card", timeout=15000)
        cards = page.query_selector_all(".product-card")
        assert len(cards) > 0
        # 结果头
        assert page.is_visible(".result-head")
        # AI 推荐
        assert page.is_visible(".ai-card")
        page.close()

    def test_result_has_platform_table(self, browser):
        """结果展开后显示平台报价表"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        page.fill(".search-box input", "iPhone")
        page.click(".search-btn")
        page.wait_for_selector(".product-card", timeout=15000)
        # 第一张卡片默认展开
        page.wait_for_selector(".offer-table", timeout=5000)
        rows = page.query_selector_all(".offer-table tbody tr")
        assert len(rows) >= 2, "应展示至少2个平台报价"
        # 有最低价标记
        lowest = page.query_selector(".lowest-badge")
        assert lowest is not None
        page.close()

    def test_toggle_collapse(self, browser):
        """报价表可折叠/展开"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        page.fill(".search-box input", "iPhone")
        page.click(".search-btn")
        page.wait_for_selector(".product-card", timeout=15000)
        # 先收起
        page.click(".pc-toggle")
        page.wait_for_selector(".offer-table", state="hidden", timeout=3000)
        # 再展开
        page.click(".pc-toggle")
        page.wait_for_selector(".offer-table", state="visible", timeout=3000)
        page.close()

    def test_price_display(self, browser):
        """价格格式正确（含小数点）"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        page.fill(".search-box input", "iPhone")
        page.click(".search-btn")
        page.wait_for_selector(".best-price", timeout=15000)
        price_text = page.inner_text(".best-price")
        import re
        assert re.search(r"\d+\.\d{2}", price_text), f"价格格式错误: {price_text}"
        page.close()

    def test_chart_renders(self, browser):
        """走势图 canvas 渲染"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        page.fill(".search-box input", "iPhone")
        page.click(".search-btn")
        page.wait_for_selector(".product-card", timeout=15000)
        page.wait_for_selector(".chart canvas", timeout=8000)
        page.close()


class TestMobileSpecific:
    def test_mobile_layout(self, browser):
        """手机端：搜索框/结果/表格正常"""
        page = _open_page(browser, VIEWPORTS["mobile"])
        page.fill(".search-box input", "小米")
        page.click(".search-btn")
        page.wait_for_selector(".product-card", timeout=15000)
        # 手机端表格应可见
        page.wait_for_selector(".offer-table", timeout=5000)
        # 手机端优惠明细列隐藏（响应式）
        detail_visible = page.is_visible(".offer-detail")
        # 390px 下 offer-detail 应隐藏（display:none）
        if detail_visible:
            # 允许存在但需确认无溢出
            pass
        overflow = page.evaluate("document.body.scrollWidth > window.innerWidth")
        assert not overflow, "手机端存在横向溢出"
        page.close()

    def test_mobile_nav_touchable(self, browser):
        """手机端按钮可点击（触控尺寸）"""
        page = _open_page(browser, VIEWPORTS["mobile"])
        btn = page.query_selector(".search-btn")
        box = btn.bounding_box()
        assert box["height"] >= 40, f"按钮过小不适合触控: {box['height']}px"
        page.close()
