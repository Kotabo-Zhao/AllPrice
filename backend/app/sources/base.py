"""AllPrice — 数据源基类与注册表

所有平台适配器继承 BaseSource，实现 search()。
SourceRegistry 管理多数据源并行搜索 + 故障隔离。
"""
from __future__ import annotations

import asyncio
import logging
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
    """数据源注册表：并行搜索 + 故障隔离"""

    def __init__(self, sources: Optional[list[BaseSource]] = None):
        self._sources: dict[str, BaseSource] = {}
        self._health: dict[str, bool] = {}
        for s in (sources or []):
            self.register(s)

    def register(self, source: BaseSource):
        self._sources[source.platform] = source
        self._health[source.platform] = True
        log.info(f"Source registered: {source.platform} ({source.platform_label})")

    def available_platforms(self) -> list[str]:
        return [p for p, ok in self._health.items() if ok]

    def mark_failed(self, platform: str):
        """连续失败熔断标记"""
        self._health[platform] = False
        log.warning(f"Source {platform} marked unhealthy (circuit open)")

    def mark_healthy(self, platform: str):
        self._health[platform] = True

    async def search_all(
        self,
        keyword: str,
        limit: int = 10,
        timeout: float = 10.0,
    ) -> dict[str, list[PlatformOffer]]:
        """并行搜索所有健康数据源；单源失败只降级该源"""
        results: dict[str, list[PlatformOffer]] = {}
        healthy = [s for p, s in self._sources.items() if self._health.get(p, True)]
        if not healthy:
            return results

        async def _one(src: BaseSource):
            try:
                offers = await asyncio.wait_for(
                    asyncio.to_thread(src.search, keyword, limit),
                    timeout=timeout,
                )
                self.mark_healthy(src.platform)
                return src.platform, offers
            except Exception as e:
                log.error(f"Source {src.platform} search failed: {e}")
                self.mark_failed(src.platform)
                return src.platform, []

        outcomes = await asyncio.gather(*[_one(s) for s in healthy])
        for platform, offers in outcomes:
            if offers:
                results[platform] = offers
        return results
