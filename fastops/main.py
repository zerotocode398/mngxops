"""MngxOps FastAPI 应用入口。"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from fastops.api.router import api_router
from fastops.core.config import get_settings


def create_app() -> FastAPI:
    """创建纯 FastAPI 应用。"""
    settings = get_settings()
    app = FastAPI(
        title="MngxOps API",
        description="MngxOps FastAPI + Jinja 重构版接口。",
        version="0.2.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    if settings.static_path.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(settings.static_path)),
            name="static",
        )
    app.include_router(api_router)
    return app


app = create_app()

