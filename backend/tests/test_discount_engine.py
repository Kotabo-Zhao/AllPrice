"""优惠计算引擎单元测试

覆盖：
- 单券/多券叠加
- 门槛判断（不满足满减不生效）
- 互斥组（秒杀 vs 平台券）
- 会员价作为独立基础价
- 返现/运费
- 百分比折扣
"""
import pytest

from app.core.discount_engine import DiscountEngine
from app.models import Coupon


@pytest.fixture
def engine():
    return DiscountEngine()


class TestBasicCoupons:
    def test_no_coupon(self, engine):
        r = engine.calculate(100, [])
        assert r.final_price == 100

    def test_flat_discount(self, engine):
        c = Coupon(kind="platform_coupon", label="满100减20", threshold=100, discount=20)
        r = engine.calculate(100, [c])
        assert r.final_price == 80
        assert "满100减20" in r.detail_text

    def test_threshold_not_met(self, engine):
        """标价低于门槛，优惠不生效"""
        c = Coupon(kind="platform_coupon", label="满200减50", threshold=200, discount=50)
        r = engine.calculate(150, [c])
        assert r.final_price == 150  # 不满足门槛，原价

    def test_threshold_met_exactly(self, engine):
        c = Coupon(kind="full_reduction", label="每满100减10", threshold=100, discount=10)
        r = engine.calculate(100, [c])
        assert r.final_price == 90

    def test_percent_discount(self, engine):
        c = Coupon(kind="shop_coupon", label="85折", threshold=0, discount=0, percent=0.85)
        r = engine.calculate(100, [c])
        assert abs(r.final_price - 85) < 0.01

    def test_percent_with_max(self, engine):
        """8折但最多减50"""
        c = Coupon(kind="flash_sale", label="8折封顶50", threshold=0, discount=0,
                   percent=0.8, max_discount=50)
        r = engine.calculate(1000, [c])
        assert abs(r.final_price - 950) < 0.01  # 800 折后，但最多减50 → 950


class TestStacking:
    def test_multiple_coupons_stack(self, engine):
        """平台券 + 店铺券 可叠加"""
        platform = Coupon(kind="platform_coupon", label="平台满300减50", threshold=300, discount=50)
        shop = Coupon(kind="shop_coupon", label="店铺满200减30", threshold=200, discount=30)
        r = engine.calculate(500, [platform, shop])
        assert r.final_price == 420

    def test_exclusive_groups(self, engine):
        """秒杀和平台券互斥，只能选更优的"""
        flash = Coupon(kind="flash_sale", label="秒杀价直降200", threshold=0,
                       discount=200, exclusive_group="promo")
        platform = Coupon(kind="platform_coupon", label="平台满100减80", threshold=100,
                          discount=80, exclusive_group="promo")
        r = engine.calculate(500, [flash, platform])
        # 秒杀省200 > 平台券省80 → 选秒杀
        assert r.final_price == 300
        assert any(c.label == "秒杀价直降200" for c in r.applied)
        assert not any(c.label == "平台满100减80" for c in r.applied)

    def test_exclusive_chooses_better(self, engine):
        """互斥组内自动选更优的"""
        weak = Coupon(kind="platform_coupon", label="满100减10", threshold=100,
                      discount=10, exclusive_group="g1")
        strong = Coupon(kind="flash_sale", label="直降100", threshold=0,
                        discount=100, exclusive_group="g1")
        r = engine.calculate(500, [weak, strong])
        assert r.final_price == 400
        assert any(c.label == "直降100" for c in r.applied)


class TestMemberPrice:
    def test_member_price_better(self, engine):
        """会员价 90 优于 原价100+满减10=90 → 相同取先算的会员价"""
        member = 90.0
        c = Coupon(kind="platform_coupon", label="满100减10", threshold=100, discount=10)
        r = engine.calculate(100, [c], member_price=member)
        # 会员价90 与 原价-10=90 持平，final 都是90
        assert abs(r.final_price - 90) < 0.01

    def test_member_price_worse_ignored(self, engine):
        """会员价更贵时不用会员价"""
        member = 110.0
        c = Coupon(kind="platform_coupon", label="满100减20", threshold=100, discount=20)
        r = engine.calculate(100, [c], member_price=member)
        assert r.final_price == 80  # 用原价100-20


class TestCashbackShipping:
    def test_cashback(self, engine):
        c = Coupon(kind="platform_coupon", label="满100减20", threshold=100, discount=20)
        r = engine.calculate(100, [c], cashback=10)
        assert r.final_price == 70
        assert "返现" in r.detail_text

    def test_shipping_fee(self, engine):
        r = engine.calculate(100, [], ship_fee=8)
        assert r.final_price == 108

    def test_cashback_and_shipping(self, engine):
        c = Coupon(kind="platform_coupon", label="满100减20", threshold=100, discount=20)
        r = engine.calculate(100, [c], cashback=10, ship_fee=5)
        assert r.final_price == 75  # 100-20-10+5

    def test_cashback_not_exceed_price(self, engine):
        """返现不能超过应付价（防负数）"""
        c = Coupon(kind="platform_coupon", label="满10减5", threshold=10, discount=5)
        r = engine.calculate(10, [c], cashback=50)
        assert r.final_price == 0  # clamp 到0


class TestEdgeCases:
    def test_zero_price(self, engine):
        r = engine.calculate(0, [])
        assert r.final_price == 0

    def test_negative_price_clamped(self, engine):
        """极端折扣不会算出负数"""
        c = Coupon(kind="full_reduction", label="满1减1000", threshold=1, discount=1000)
        r = engine.calculate(10, [c])
        assert r.final_price >= 0

    def test_empty_coupon_list_detail(self, engine):
        r = engine.calculate(99.9, [])
        assert r.final_price == 99.9
        assert "到手" in r.detail_text

    def test_invalid_coupon_rejected(self):
        """无折扣无百分比的券应被拒绝"""
        with pytest.raises(ValueError):
            Coupon(kind="platform_coupon", label="无效", threshold=0, discount=0)
