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
        # ZOL 真实报价源（手机/电脑/数码全覆盖；SSR 可抓，真实价格+参数+图）
        from .sources.zol import ZolSource
        registry.register(ZolSource())
        # 演示/兜底数据源（保证前端开发期有数据；ZOL 未覆盖品类自动落此源）
        from .sources.mock import MockSource
        registry.register(MockSource())
        # 注：jd/taobao/pdd 接口全部反爬失效（登录+签名+风控），不再注册，
        #     避免每次搜索空等超时；后续有真实通道（官方API/登录态）再接入
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
