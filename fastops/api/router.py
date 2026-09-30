"""FastAPI 总路由。"""

from fastapi import APIRouter

from .routes import assets, audit, auth, configs, pages, releases, settings, tasks

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(pages.router)
api_router.include_router(assets.router)
api_router.include_router(audit.router)
api_router.include_router(configs.router)
api_router.include_router(releases.router)
api_router.include_router(settings.router)
api_router.include_router(tasks.router)
