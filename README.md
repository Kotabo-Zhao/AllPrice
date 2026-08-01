# AllPrice 全价比价

全网全平台商品比价系统 — 一套 H5 跑安卓 / iOS / PC。

搜一个产品 → 全网平台拉价 → 算所有优惠叠加后的最终到手价 → 历史走势 → AI 推荐最划算方案。

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + Vite + ECharts + PWA（响应式，三端共用） |
| 后端 | FastAPI + SQLAlchemy + Celery |
| 存储 | PostgreSQL（商品/价格/优惠）+ Redis（缓存/队列） |
| 数据源 | 京东公开接口（免费）+ 开源爬虫（淘宝/拼多多/闲鱼）+ 众包校准 |
| AI | DeepSeek 免费档（商品识别/综合推荐/参数解读） |
| 测试 | pytest（后端单测）+ Playwright（前端 E2E，三端视口） |

## 目录结构

```
AllPrice/
├── backend/
│   ├── app/
│   │   ├── core/         # 核心引擎：优惠计算/商品归一化/走势聚合
│   │   ├── sources/      # 数据源适配器（jd/taobao/pdd/闲鱼…）
│   │   ├── api/          # FastAPI 路由
│   │   └── main.py
│   └── tests/            # pytest
├── frontend/             # Vue3 + Vite
├── docs/                 # 设计文档
└── README.md
```

## 快速开始

（待补充）
