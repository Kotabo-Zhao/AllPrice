"""AllPrice — FastAPI 入口

启动: uvicorn app.main:app --reload --port 8001
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from .api.routes import router
from .sources.base import SourceRegistry
from .sources.jd import JDSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
)
log = logging.getLogger("allprice")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据源注册表（若测试已注入则复用）"""
    registry: SourceRegistry = getattr(app.state, "registry", None)
    if registry is None:
        registry = SourceRegistry()
        # 演示/兜底数据源（保证前端开发期有数据）
        from .sources.mock import MockSource
        registry.register(MockSource())
        # 京东公开接口（免费；网络受限时自动熔断，不影响 mock）
        registry.register(JDSource())
        # 淘宝/拼多多爬虫（Playwright；网络受限时自动降级）
        from .sources.taobao import TaobaoSource
        from .sources.pdd import PddSource
        registry.register(TaobaoSource())
        registry.register(PddSource())
        # TODO(v3): 闲鱼/抖音 适配器
        app.state.registry = registry
    log.info(f"AllPrice started with platforms: {registry.available_platforms()}")
    yield
    log.info("AllPrice shutting down")


app = FastAPI(
    title="AllPrice 全价比价",
    version="0.1.0",
    description="全网全平台商品比价系统",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发期放开，上线收紧
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


# ── 前端静态文件（同端口 serve，便于三端部署） ──
# 安卓端通过 ALLPRICE_FRONTEND_DIR 环境变量覆盖（打包目录不可写）
_FRONTEND_DIR = os.environ.get(
    "ALLPRICE_FRONTEND_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend")),
)
# 整个前端目录挂到根路径：/ → index.html, /vue.global.prod.js → 文件
# 注意: mount 注册在 include_router 之后，/api/* 与 /docs 优先匹配不受影响
if os.path.isdir(_FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="frontend")
