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
    steps: list[dict] = field(default_factory=list)  # 每步明细 [{label, amount}]


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

    def _all_combos_for_base(self, base: float, coupons: list[Coupon]) -> list[PriceBreakdown]:
        """在固定基础价下，穷举所有合法优惠组合（含互斥组约束）"""
        if not coupons:
            return [PriceBreakdown(final_price=base, total_discount=0)]

        # 按互斥组分组：组内互斥（最多取1个），组间可叠加
        groups: dict[str, list[Coupon]] = {}
        non_exclusive: list[Coupon] = []
        for c in coupons:
            if c.exclusive_group:
                groups.setdefault(c.exclusive_group, []).append(c)
            else:
                non_exclusive.append(c)

        # 每个互斥组：选择"不选/选其一"
        group_choices: list[list[Coupon]] = [[]]
        for gname, gcoupons in groups.items():
            new_choices = list(group_choices)
            for gc in gcoupons:
                for existing in group_choices:
                    new_choices.append(existing + [gc])
            group_choices = new_choices

        # 非互斥券：每个券"用/不用"的全组合（上限内）
        combos: list[PriceBreakdown] = []
        checked = 0
        for group_sel in group_choices:
            selected = list(group_sel)
            price = base
            for c in selected:
                price = _apply_coupon(price, c)
            # 非互斥券全组合（2^n，n 小）
            usable = [c for c in non_exclusive]
            for r in range(len(usable) + 1):
                for subset in itertools.combinations(usable, r):
                    p = price
                    applied = list(selected)
                    valid = True
                    for c in subset:
                        new_p = _apply_coupon(p, c)
                        if new_p >= p - 0.005:
                            valid = False  # 该券不生效，跳过此组合
                            break
                        p = new_p
                        applied.append(c)
                    if valid:
                        combos.append(PriceBreakdown(
                            final_price=max(0.0, p),
                            total_discount=base - p,
                            applied=applied,
                        ))
                    checked += 1
                    if checked > MAX_COMBINATIONS:
                        return combos or [PriceBreakdown(final_price=base, total_discount=0)]
        return combos or [PriceBreakdown(final_price=base, total_discount=0)]

    def _best_for_base(self, base: float, coupons: list[Coupon]) -> PriceBreakdown:
        """在固定基础价下，穷举所有合法优惠组合取最优"""
        combos = self._all_combos_for_base(base, coupons)
        return min(combos, key=lambda b: b.final_price) if combos else \
            PriceBreakdown(final_price=base, total_discount=0)

    def enumerate_plans(
        self,
        base_price: float,
        coupons: list[Coupon],
        member_price: Optional[float] = None,
        cashback: float = 0.0,
        ship_fee: float = 0.0,
        max_plans: int = 6,
    ) -> list[dict]:
        """枚举所有合法优惠组合方案，输出结构化明细

        Returns:
            [{
                "plan_index": int,
                "base_label": "原价"|"会员价",
                "base_price": float,
                "steps": [{"label": "满1000减100", "amount": -100, "type": "coupon"}],
                "cashback": float, "ship_fee": float,
                "final_price": float,
                "total_saved": float,
                "is_best": bool,
            }, ...]  按 final_price 升序，最多 max_plans 个
        """
        plans: list[dict] = []
        base_options = [("原价", base_price)]
        if member_price and 0 < member_price < base_price:
            base_options.append(("会员价", member_price))

        for base_label, base in base_options:
            combos = self._all_combos_for_base(base, coupons)
            # 按最终价排序，去重（同优惠组合只留一次）
            seen: set[str] = set()
            uniq: list[PriceBreakdown] = []
            for b in sorted(combos, key=lambda x: x.final_price):
                key = ",".join(sorted(c.label for c in b.applied))
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(b)

            for b in uniq:
                # 只保留"当前真实可用"的方案：
                # ① 至少应用了一张券 或 有返现（零优惠裸价无信息量，不展示）
                # ② 券必须有效（is_active 不为 False；过期/失效券不参与）
                if not b.applied and cashback <= 0:
                    continue
                if any(getattr(c, "is_active", True) is False for c in b.applied):
                    continue
                final = max(0.0, b.final_price - cashback + ship_fee)
                steps = [{"label": f"{base_label}{base:g}元", "amount": 0, "type": "base"}]
                # 逐步计算每个优惠的实际节省（模拟顺序应用）
                running = base
                for c in b.applied:
                    after = _apply_coupon(running, c)
                    saving = round(running - after, 2)
                    steps.append({"label": c.label, "type": "coupon", "amount": -saving})
                    running = after
                if cashback > 0:
                    steps.append({"label": f"返现{cashback:g}元", "amount": -cashback, "type": "cashback"})
                if ship_fee > 0:
                    steps.append({"label": f"运费{ship_fee:g}元", "amount": ship_fee, "type": "ship"})
                plans.append({
                    "base_label": base_label,
                    "base_price": round(base, 2),
                    "steps": steps,
                    "cashback": cashback,
                    "ship_fee": ship_fee,
                    "final_price": round(final, 2),
                    "total_saved": round(base_price - final, 2),
                    "coupon_labels": [c.label for c in b.applied],
                })
                if len(plans) >= max_plans:
                    break
            if len(plans) >= max_plans:
                break

        plans.sort(key=lambda p: p["final_price"])
        for i, p in enumerate(plans):
            p["is_best"] = (i == 0)
            p["plan_index"] = i + 1
        return plans

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
