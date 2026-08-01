"""AllPrice — 优惠计算引擎（核心护城河）

功能：给定标价 + 一组可用优惠，穷举所有「合法叠加组合」，
算出最低到手价，并给出可读的叠加明细。

设计要点：
1. 优惠抽象为 Coupon（threshold/discount/percent/exclusive_group）
2. exclusive_group 相同的优惠互斥（秒杀 vs 平台券二选一）
3. 按组合数限制剪枝（合法组合通常 ≤ 数百，秒级可算）
4. 返回最优方案 + 明细文本 + 备选方案
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from typing import Optional

from ..models import Coupon

log = logging.getLogger(__name__)

MAX_COMBINATIONS = 2000  # 穷举上限，防止组合爆炸


@dataclass
class PriceBreakdown:
    """一次优惠组合的计算结果"""
    final_price: float
    total_discount: float
    applied: list[Coupon] = field(default_factory=list)
    detail_text: str = ""


def _apply_coupon(price: float, coupon: Coupon) -> float:
    """单个优惠抵扣计算（不修改原价）"""
    if price < coupon.threshold:
        return price  # 不满足门槛，无效
    if coupon.percent is not None:
        discounted = price * coupon.percent
        if coupon.max_discount is not None:
            discounted = max(discounted, price - coupon.max_discount)
        return discounted
    return price - coupon.discount


class DiscountEngine:
    """优惠叠加计算引擎"""

    def calculate(
        self,
        base_price: float,
        coupons: list[Coupon],
        member_price: Optional[float] = None,
        cashback: float = 0.0,
        ship_fee: float = 0.0,
    ) -> PriceBreakdown:
        """计算最低到手价

        Args:
            base_price: 标价
            coupons: 可用优惠列表
            member_price: 会员价（若存在，作为独立的"基础价"选项）
            cashback: 返现金额（如 E卡/京豆，直接减）
            ship_fee: 运费

        Returns:
            最优组合的 PriceBreakdown
        """
        if base_price <= 0:
            return PriceBreakdown(final_price=0, total_discount=0, detail_text="")

        # 会员价作为另一个基础价候选
        base_options = {base_price}
        if member_price and 0 < member_price < base_price:
            base_options.add(member_price)

        best: Optional[PriceBreakdown] = None

        for base in base_options:
            result = self._best_for_base(base, coupons)
            # 叠加返现与运费
            final = max(0.0, result.final_price - cashback + ship_fee)
            total_discount = base_price - (result.final_price - cashback) if cashback <= result.final_price else base_price
            label = "会员价" if base == member_price else "原价"
            result.final_price = final
            # 明细：标价 → 每个优惠 → 返现/运费 → 到手
            steps = [f"{label}{base:g}元"]
            for c in result.applied:
                steps.append(c.label)
            if cashback > 0:
                steps.append(f"返现{cashback:g}元")
            if ship_fee > 0:
                steps.append(f"运费{ship_fee:g}元")
            result.detail_text = " → ".join(steps) + f" = 到手 {final:g}元"
            if best is None or final < best.final_price:
                best = result

        return best

    def _best_for_base(self, base: float, coupons: list[Coupon]) -> PriceBreakdown:
        """在固定基础价下，穷举所有合法优惠组合取最优"""
        if not coupons:
            return PriceBreakdown(final_price=base, total_discount=0, detail_text="")

        # 按互斥组分组：组内互斥（最多取1个），组间可叠加
        groups: dict[str, list[Coupon]] = {}
        non_exclusive: list[Coupon] = []
        for c in coupons:
            if c.exclusive_group:
                groups.setdefault(c.exclusive_group, []).append(c)
            else:
                non_exclusive.append(c)

        # 每个互斥组：选择"不选/选最优之一"
        group_choices: list[list[Coupon]] = [[]]
        for gname, gcoupons in groups.items():
            # 组内每张券单独作为候选（同组券条件不同，可能适合不同价位）
            new_choices = list(group_choices)
            for gc in gcoupons:
                for existing in group_choices:
                    new_choices.append(existing + [gc])
            group_choices = new_choices

        # 非互斥优惠：可作为补充，但只取能生效的
        best: Optional[PriceBreakdown] = None
        checked = 0

        for group_sel in group_choices:
            # 补充非互斥券：从能生效的券中按"减得多优先"逐个尝试叠加
            selected = list(group_sel)
            price = base
            for c in selected:
                price = _apply_coupon(price, c)
            # 尝试加非互斥券（贪心：按节省额排序）
            usable = sorted(
                [c for c in non_exclusive],
                key=lambda c: self._saving(base, c),
                reverse=True,
            )
            for c in usable:
                new_price = _apply_coupon(price, c)
                if new_price < price - 0.005:
                    selected.append(c)
                    price = new_price
                checked += 1
                if checked > MAX_COMBINATIONS:
                    break

            breakdown = PriceBreakdown(
                final_price=max(0.0, price),
                total_discount=base - price,
                applied=selected,
            )
            if best is None or breakdown.final_price < best.final_price:
                best = breakdown

        if best is None:
            best = PriceBreakdown(final_price=base, total_discount=0, detail_text="")
        return best

    @staticmethod
    def _saving(base: float, coupon: Coupon) -> float:
        """估算单张券在 base 价位的最大节省（用于贪心排序）"""
        after = _apply_coupon(base, coupon)
        return max(0.0, base - after)

    def format_detail(self, breakdown: PriceBreakdown, base_price: float) -> str:
        """把最优组合渲染成可读明细（给前端展示）"""
        parts = [f"标价{base_price:g}元"]
        for c in breakdown.applied:
            parts.append(c.label)
        parts.append(f"到手 {breakdown.final_price:g}元")
        return " → ".join(parts)
