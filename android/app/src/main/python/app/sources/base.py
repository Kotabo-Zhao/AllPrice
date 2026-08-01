"""AllPrice — 数据源基类与注册表

所有平台适配器继承 BaseSource，实现 search()。
SourceRegistry 管理多数据源并行搜索 + 故障隔离。
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

from ..models import PlatformOffer

log = logging.getLogger(__name__)


class BaseSource(ABC):
    """数据源适配器基类"""
    platform: str = ""
    platform_label: str = ""

    @abstractmethod
    async def search(self, keyword: str, limit: int = 10) -> list[PlatformOffer]:
        """按关键词搜索该平台商品"""
        raise NotImplementedError


class SourceRegistry:
    """数据源注册表：并行搜索 + 自适应熔断

    熔断策略：
    - 单次失败 → 只降级本轮（结果为空）
    - 连续失败 >= 3 次 → 进入冷却（cooldown 秒内跳过该源，不再干等超时）
    - 冷却期后自动重试；成功则立即恢复
    """

    COOLDOWN_SECONDS = 60.0
    FAIL_THRESHOLD = 3

    def __init__(self, sources: Optional[list[BaseSource]] = None):
        self._sources: dict[str, BaseSource] = {}
        self._fail_count: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        for s in (sources or []):
            self.register(s)

    def register(self, source: BaseSource):
        self._sources[source.platform] = source
        self._fail_count[source.platform] = 0
        self._cooldown_until[source.platform] = 0.0
        log.info(f"Source registered: {source.platform} ({source.platform_label})")

    def available_platforms(self) -> list[str]:
        """所有已注册平台（供健康检查/状态展示）"""
        return list(self._sources.keys())

    def _in_cooldown(self, platform: str) -> bool:
        return time.monotonic() < self._cooldown_until.get(platform, 0.0)

    def _record_failure(self, platform: str):
        self._fail_count[platform] = self._fail_count.get(platform, 0) + 1
        if self._fail_count[platform] >= self.FAIL_THRESHOLD:
            self._cooldown_until[platform] = time.monotonic() + self.COOLDOWN_SECONDS
            log.warning(
                f"Source {platform} failed {self._fail_count[platform]}x, "
                f"cooldown {self.COOLDOWN_SECONDS}s"
            )

    def _record_success(self, platform: str):
        self._fail_count[platform] = 0
        self._cooldown_until[platform] = 0.0

    async def search_all(
        self,
        keyword: str,
        limit: int = 10,
        timeout: float = 10.0,
    ) -> dict[str, list[PlatformOffer]]:
        """并行搜索数据源；连续失败的源进入冷却期自动跳过"""
        results: dict[str, list[PlatformOffer]] = {}
        if not self._sources:
            return results

        # 冷却中的源跳过（避免每次搜索干等真实源超时）
        active = [s for p, s in self._sources.items() if not self._in_cooldown(p)]
        if not active:
            active = list(self._sources.values())  # 全部冷却 → 强制全量重试

        async def _one(src: BaseSource):
            try:
                offers = await asyncio.wait_for(
                    asyncio.to_thread(src.search, keyword, limit),
                    timeout=timeout,
                )
                self._record_success(src.platform)
                return src.platform, offers
            except Exception as e:
                log.error(f"Source {src.platform} search failed: {e}")
                self._record_failure(src.platform)
                return src.platform, []

        outcomes = await asyncio.gather(*[_one(s) for s in active])
        for platform, offers in outcomes:
            if offers:
                results[platform] = offers
        return results
