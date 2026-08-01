"""AllPrice 前端 E2E 测试 — Playwright 三端视口（看板版）

覆盖：
- 搜索页渲染（桌面/平板/手机三视口）
- 搜索流程（mock 数据源）
- 看板元素（商品概览/KPI/图表/AI推荐/规格/报价表）
- 布局无溢出、关键元素可见

运行: python -m pytest tests/test_frontend_e2e.py -v
前置: 后端已在 8001 端口运行（uvicorn app.main:app --port 8001）
"""
import pytest
import time
import os
import re

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


def _do_search(page, kw="iPhone", timeout=30000):
    page.fill(".search-box input", kw)
    page.click(".search-btn")
    page.wait_for_selector(".hero-card, .error-panel", timeout=timeout)
    page.wait_for_timeout(1500)


class TestSearchPage:
    def test_loads_all_viewports(self, browser):
        """三端视口页面都能加载"""
        for name, vp in VIEWPORTS.items():
            page = _open_page(browser, vp)
            assert "全价比价" in page.title(), f"{name} 标题错误"
            assert page.is_visible(".search-box input"), f"{name} 搜索框不可见"
            assert page.is_visible(".search-btn"), f"{name} 搜索按钮不可见"
            assert page.is_visible(".hero-empty"), f"{name} 首页引导不可见"
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
        tags = page.query_selector_all(".tag")
        assert len(tags) > 0
        tags[0].click()
        page.wait_for_selector(".hero-card, .loading-panel", timeout=10000)
        page.close()


class TestSearchFlow:
    def test_search_shows_dashboard(self, browser):
        """搜索后展示数据看板（mock 数据）"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        _do_search(page)
        # 看板核心元素
        assert page.is_visible(".hero-card"), "商品概览卡不可见"
        assert page.is_visible(".hero-img"), "商品图片不可见"
        assert page.is_visible(".hero-price .num"), "最低价不可见"
        # KPI 指标条
        kpis = page.query_selector_all(".kpi")
        assert len(kpis) >= 4, f"KPI 指标应至少4个, 实际{len(kpis)}"
        # AI 推荐
        assert page.is_visible(".ai-panel"), "AI推荐卡不可见"
        page.close()

    def test_dashboard_charts(self, browser):
        """看板 4 个图表全部渲染"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        _do_search(page)
        svgs = page.query_selector_all(".chart-svg")
        assert len(svgs) >= 4, f"应渲染至少4个图表SVG, 实际{len(svgs)}"
        # 每个 SVG 有实际图形元素
        for i, svg in enumerate(svgs):
            shapes = svg.query_selector_all("rect, polyline, circle, line")
            assert len(shapes) > 0, f"图表{i} 无图形内容"
        # 无 NaN 文本
        nan = page.evaluate("""() => {
            let bad = 0;
            document.querySelectorAll('.chart-svg text').forEach(t => {
                if (t.getAttribute('y') === 'NaN' || t.textContent.includes('NaN')) bad++;
            });
            return bad;
        }""")
        assert nan == 0, f"存在 {nan} 个 NaN 文本"
        page.close()

    def test_platform_table(self, browser):
        """平台报价表展示多平台 + 最低价高亮"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        _do_search(page)
        rows = page.query_selector_all(".offer-table tbody tr")
        assert len(rows) >= 2, f"应展示至少2个平台报价, 实际{len(rows)}"
        # 最低价高亮行
        lowest = page.query_selector_all(".row-lowest")
        assert len(lowest) >= 1, "最低价行未高亮"
        # 最低标记
        flag = page.query_selector(".lowest-flag")
        assert flag is not None, "缺少'最低'标记"
        page.close()

    def test_spec_table(self, browser):
        """规格参数表展示商品参数"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        _do_search(page)
        rows = page.query_selector_all(".spec-table tr")
        assert len(rows) >= 3, f"规格参数应至少3项, 实际{len(rows)}"
        page.close()

    def test_kpi_values(self, browser):
        """KPI 数字格式正确（含小数点）"""
        page = _open_page(browser, VIEWPORTS["desktop"])
        _do_search(page)
        price_text = page.inner_text(".hero-price .num")
        assert re.search(r"\d+\.\d{2}", price_text), f"价格格式错误: {price_text}"
        kpi_text = page.inner_text(".kpi-row")
        assert "¥" in kpi_text, "KPI 应显示货币符号"
        page.close()


class TestMobileSpecific:
    def test_mobile_layout(self, browser):
        """手机端：看板正常无溢出"""
        page = _open_page(browser, VIEWPORTS["mobile"])
        _do_search(page, "小米")
        assert page.is_visible(".hero-card"), "手机端概览卡不可见"
        # 手机端报价表
        page.wait_for_selector(".offer-table", timeout=8000)
        overflow = page.evaluate("document.body.scrollWidth > window.innerWidth")
        assert not overflow, "手机端存在横向溢出"
        page.close()

    def test_mobile_nav_touchable(self, browser):
        """手机端按钮可点击（触控尺寸）"""
        page = _open_page(browser, VIEWPORTS["mobile"])
        btn = page.query_selector(".search-btn")
        box = btn.bounding_box()
        assert box["height"] >= 38, f"按钮过小不适合触控: {box['height']}px"
        page.close()
