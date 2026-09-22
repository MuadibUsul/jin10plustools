"""PLUS 日报服务端入口：uvicorn app.main:app"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.ai.jev_client import JevClient
from app.ai.worker import AnalysisWorker
from app.api import assets, auth, board, collab, issues, materials
from app.config import get_settings
from app.db.session import get_session_factory
from app.services.maintenance import MaintenanceWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("plus")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    worker: AnalysisWorker | None = None
    if settings.ai_worker_enabled and settings.jev_api_key:
        worker = AnalysisWorker(get_session_factory(), JevClient.from_settings(), settings.ai_worker_interval_seconds)
        worker.start()
    else:
        log.warning("AI 分析未启用（未配置 PLUS_JEV_API_KEY 或 PLUS_AI_WORKER_ENABLED=false）")
    maintenance = MaintenanceWorker(get_session_factory(), settings.retention_days)
    maintenance.start()
    yield
    maintenance.stop()
    if worker:
        worker.stop()


app = FastAPI(title="PLUS Research API", version="0.2.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(materials.router)
app.include_router(assets.router)
app.include_router(issues.router)
app.include_router(board.router)
app.include_router(collab.router)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


class _WebFiles(StaticFiles):
    """选稿网页静态文件：每次都向服务端确认是否有新版本（未变化时返回 304，不影响速度），
    避免部署后浏览器继续使用缓存的旧脚本 / 样式"""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# 选稿网页（同源，登录后调用上面的接口）；必须最后挂载，避免遮住 /api
app.mount("/", _WebFiles(directory=Path(__file__).parent / "web", html=True), name="web")
