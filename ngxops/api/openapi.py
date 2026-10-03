"""补充应用 OpenAPI 文档中的认证约定。"""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


OPENAPI_TAGS = [
    {
        "name": "dashboard",
        "description": "需要登录会话的首页统计轮询接口。",
    },
    {
        "name": "system",
        "description": "服务探活与运行状态接口。",
    },
    {
        "name": "tasks",
        "description": "需要有效 sessionid Cookie 的异步任务查询与取消接口。",
    },
    {
        "name": "users",
        "description": "超级管理员使用的用户组成员查询与 RBAC 管理接口。",
    },
    {
        "name": "audit",
        "description": "受审计权限保护的操作记录与登录记录页面。",
    },
    {
        "name": "settings",
        "description": "受 settings.read 保护的预置系统设置读取和超级管理员写入接口。",
    },
    {
        "name": "credentials",
        "description": "需要凭证权限的 SSH 凭证选择、解密和启停接口。",
    },
    {
        "name": "nodes",
        "description": "节点与节点组选择器、批量资产导入导出和状态门禁接口。",
    },
    {
        "name": "configs",
        "description": "配置标签、节点绑定、版本历史和受保护的配置发现/同步任务接口。",
    },
    {
        "name": "releases",
        "description": "受发布权限保护的节点选择、版本预览和异步发布任务接口。",
    },
    {
        "name": "upgrade",
        "description": "源码包管理、Nginx 编译参数预览和异步升级/回滚接口。",
    },
    {
        "name": "nginx_install",
        "description": "Nginx 全新安装参数预览、批次创建与进度接口。",
    },
    {
        "name": "nginx_service",
        "description": "Nginx 启停节点选择、异步批次执行和日志轮询接口。",
    },
]


def install_openapi_schema(app: FastAPI) -> None:
    """为受保护 JSON API 标注服务端会话 Cookie 认证。"""

    def custom_openapi():
        """生成包含会话认证方案的 OpenAPI 文档。"""
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=OPENAPI_TAGS,
        )
        components = schema.setdefault("components", {})
        schemes = components.setdefault("securitySchemes", {})
        schemes["SessionCookie"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": "sessionid",
            "description": "由服务端管理的登录会话 Cookie。",
        }
        schemes["CsrfCookie"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": "csrftoken",
            "description": "CSRF 令牌 Cookie；写请求还须在 X-CSRFToken 头重复提交此令牌。",
        }
        for path, path_item in schema.get("paths", {}).items():
            if not path.startswith("/api/"):
                continue
            for method, operation in path_item.items():
                if isinstance(operation, dict) and "responses" in operation:
                    requirement = {"SessionCookie": []}
                    if method.lower() not in ("get", "head", "options", "trace"):
                        requirement["CsrfCookie"] = []
                        operation.setdefault("parameters", []).append(
                            {
                                "name": "X-CSRFToken",
                                "in": "header",
                                "required": True,
                                "description": "与 csrftoken Cookie 相同的令牌。",
                                "schema": {"type": "string"},
                            }
                        )
                    operation["security"] = [requirement]

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi
