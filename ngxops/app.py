"""创建 FastAPI 应用并配置模板、静态资源和基础错误边界。"""

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from ngxops.api.contracts import (
    api_error_response,
    api_error_responses,
    is_json_api_request,
)
from ngxops.api.errors import handle_http_error, handle_request_validation_error
from ngxops.api.openapi import OPENAPI_TAGS, install_openapi_schema
from ngxops.dashboard.routes import router as dashboard_router
from ngxops.accounts.routes import router as accounts_router
from ngxops.audit.routes import router as audit_router
from ngxops.rbac.routes import api_router as rbac_api_router
from ngxops.rbac.routes import router as rbac_router
from ngxops.rbac.service import user_has_permission
from ngxops.config import Settings, get_settings
from ngxops.logging_setup import configure_logging, log_exception
from ngxops.credentials.routes import api_router as credentials_api_router
from ngxops.credentials.routes import router as credentials_router
from ngxops.credentials.crypto import load_or_create_fernet_key
from ngxops.configs.routes import router as configs_router
from ngxops.configs.api import api_router as configs_api_router
from ngxops.nodes.routes import api_router as nodes_api_router
from ngxops.nodes.routes import router as nodes_router
from ngxops.nginx_install.routes import router as nginx_install_router
from ngxops.nginx_service.routes import router as nginx_service_router
from ngxops.nginx_uninstall.routes import router as nginx_uninstall_router
from ngxops.releases.api import api_router as releases_api_router
from ngxops.releases.routes import router as releases_router
from ngxops.database.connection import create_database
from ngxops.security.csrf import require_csrf
from ngxops.security.errors import (
    AuthenticationRequired,
    CsrfViolation,
    PermissionDenied,
)
from ngxops.security.responses import (
    handle_authentication_required,
    handle_csrf_violation,
    handle_permission_denied,
)
from ngxops.security.secret import load_or_create_secret_key
from ngxops.security.sessions import SessionMiddleware
from ngxops.settings.routes import router as settings_router
from ngxops.tasks.api import router as tasks_router
from ngxops.tasks.executor import TaskExecutor, recover_interrupted_tasks
from ngxops.tasks.routes import router as task_pages_router
from ngxops.upgrade.routes import router as upgrade_router


logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """描述服务存活状态的 JSON 响应。"""

    status: str


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """创建不连接业务数据库的 FastAPI 应用实例。"""
    active_settings = settings or get_settings()
    configure_logging(active_settings.data_dir, active_settings.log_level)
    database = create_database(active_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """在进程启动时准备可写数据目录。"""
        active_settings.data_dir.mkdir(parents=True, exist_ok=True)
        app.state.secret_key = load_or_create_secret_key(
            active_settings.data_dir,
            active_settings.secret_key,
        )
        app.state.credential_encryption_key = load_or_create_fernet_key(
            active_settings.data_dir
        )
        executor = TaskExecutor(database.session_factory)
        app.state.task_executor = executor
        try:
            if active_settings.database_path.is_file():
                await run_in_threadpool(
                    recover_interrupted_tasks,
                    database.engine,
                    database.session_factory,
                )
            yield
        finally:
            executor.shutdown()
            database.engine.dispose()

    app = FastAPI(
        title="ngxops",
        version=active_settings.version,
        description="Nginx 多节点集中式管理平台",
        openapi_tags=OPENAPI_TAGS,
        debug=active_settings.debug,
        lifespan=lifespan,
        dependencies=[Depends(require_csrf)],
    )
    app.state.settings = active_settings
    app.state.database = database
    app.state.secret_key = active_settings.secret_key
    app.state.permission_checker = user_has_permission
    app.state.task_executor = None
    app.state.templates = Jinja2Templates(directory=str(active_settings.template_dir))
    app.add_middleware(SessionMiddleware)

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        """记录页面和 API 请求的身份、路径、结果与耗时。"""
        if request.url.path.startswith("/static/"):
            return await call_next(request)
        started_at = time.monotonic()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            session = request.scope.get("session")
            user_id = session.get("user_id", "-") if session is not None else "-"
            client_ip = request.client.host if request.client is not None else "-"
            status_code = response.status_code if response is not None else 500
            logging.getLogger("ngxops.access").info(
                "request method=%s path=%s status=%s duration_ms=%d "
                "user_id=%s client_ip=%s",
                request.method,
                request.url.path,
                status_code,
                int((time.monotonic() - started_at) * 1000),
                user_id,
                client_ip,
            )

    app.mount(
        "/static",
        StaticFiles(directory=str(active_settings.static_dir)),
        name="static",
    )
    app.include_router(tasks_router)
    app.include_router(task_pages_router)
    app.include_router(dashboard_router)
    app.include_router(accounts_router)
    app.include_router(audit_router)
    app.include_router(settings_router)
    app.include_router(rbac_router)
    app.include_router(rbac_api_router)
    app.include_router(credentials_router)
    app.include_router(credentials_api_router)
    app.include_router(configs_router)
    app.include_router(configs_api_router)
    app.include_router(nodes_router)
    app.include_router(nodes_api_router)
    app.include_router(releases_router)
    app.include_router(releases_api_router)
    app.include_router(upgrade_router)
    app.include_router(nginx_install_router)
    app.include_router(nginx_service_router)
    app.include_router(nginx_uninstall_router)

    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="检查服务状态",
        description="检查 Web 进程是否可响应，不检查数据库或远程节点。",
        responses=api_error_responses((500,)),
    )
    async def health() -> HealthResponse:
        """返回 Web 进程的存活状态。"""
        return HealthResponse(status="ok")

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> Response:
        """记录未处理异常类型，并向客户端返回通用错误内容。"""
        session = request.scope.get("session")
        user_id = session.get("user_id", "-") if session is not None else "-"
        log_exception(
            logger,
            "未处理的服务异常",
            exc,
            "method={} path={} user_id={}".format(
                request.method,
                request.url.path,
                user_id,
            ),
        )
        if is_json_api_request(request):
            return api_error_response(500, "服务器内部错误")
        if "text/html" in request.headers.get("accept", "").lower():
            return HTMLResponse(
                "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
                "<title>服务器内部错误</title><h1>服务器内部错误</h1>",
                status_code=500,
            )
        return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})

    app.add_exception_handler(
        AuthenticationRequired,
        handle_authentication_required,
    )
    app.add_exception_handler(PermissionDenied, handle_permission_denied)
    app.add_exception_handler(CsrfViolation, handle_csrf_violation)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(
        RequestValidationError,
        handle_request_validation_error,
    )
    install_openapi_schema(app)

    return app
