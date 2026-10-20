"""PLUS 日报服务端入口：uvicorn app.main:app"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler

from app.ai.jev_client import JevClient
from app.ai.worker import AnalysisWorker
from app.api import assets, auth, board, collab, issues, materials
from app.api import briefs, chat2api, export, feedback, live, settings as team_settings
from app.api import news_skill_os
from app.config import get_settings
from app.db.session import get_session_factory
from app.services.maintenance import MaintenanceWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("plus")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    worker: AnalysisWorker | None = None
    if settings.ai_worker_enabled:
        worker = AnalysisWorker(get_session_factory(), JevClient.from_settings() if settings.jev_api_key else None, settings.ai_worker_interval_seconds)
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


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    if request.url.path == '/api/settings':
        return JSONResponse(status_code=422, content={'detail': '设置格式不正确，请检查字段长度与格式'})
    return await request_validation_exception_handler(request, exc)


app.include_router(auth.router)
app.include_router(materials.router)
app.include_router(assets.router)
app.include_router(issues.router)
app.include_router(board.router)
app.include_router(collab.router)
app.include_router(briefs.router)
app.include_router(team_settings.router)
app.include_router(live.router)
app.include_router(export.router)
app.include_router(feedback.router)
app.include_router(news_skill_os.router)
@app.middleware('http')
async def api_no_store(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
    return response


app.include_router(chat2api.router)


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


# Explicit workspace routes precede the legacy static-file mount.
_WEB=Path(__file__).parent/'web'


@app.get('/',include_in_schema=False)
@app.get('/fresh/',include_in_schema=False)
@app.get('/annotations/',include_in_schema=False)
@app.get('/login/',include_in_schema=False)
def workspace_page():
    return FileResponse(_WEB/'portal.html',headers={'Cache-Control':'no-cache'})


@app.get('/daily/',include_in_schema=False)
def daily_page():
    return FileResponse(_WEB/'index.html',headers={'Cache-Control':'no-cache'})


@app.get('/briefs/',include_in_schema=False)
def briefs_page():
    return FileResponse(_WEB/'briefs.html',headers={'Cache-Control':'no-cache'})


@app.head('/briefs/',include_in_schema=False)
def briefs_head():
    return FileResponse(_WEB/'briefs.html',headers={'Cache-Control':'no-cache'})


# Legacy script, guide and extension-download URLs remain available.
@app.get('/settings/',include_in_schema=False)
def settings_page():
    return FileResponse(_WEB/'settings.html',headers={'Cache-Control':'no-cache'})


@app.head('/settings/',include_in_schema=False)
def settings_head():
    return FileResponse(_WEB/'settings.html',headers={'Cache-Control':'no-cache'})


app.mount("/", _WebFiles(directory=Path(__file__).parent / "web", html=True), name="web")
