# 17 FastAPI + Jinja 迁移

Q165 将现有项目迁移为 FastAPI + Jinja。当前入口已切换为纯 FastAPI，Django 不再作为运行时依赖；旧 Django 代码仅作为未迁移模块的历史实现暂存，待对应模块完成迁移与验证后删除。

## 当前入口

- `python run_server.py fastapi [host:port]`：启动 Uvicorn，默认地址为 `0.0.0.0:11993`。
- `/fastapi`：FastAPI 迁移控制台，使用 Jinja 渲染。
- `/login` / `/logout`：FastAPI 原生登录与退出，直接校验既有 `auth_user` 的 `pbkdf2_sha256` 密码哈希。
- `/docs` / `/redoc` / `/openapi.json`：FastAPI 接口文档与 OpenAPI schema。
- `/api/v1/healthz`：健康检查。
- `/fastapi/nodes`、`/fastapi/credentials`、`/fastapi/configs`、`/fastapi/releases`、`/fastapi/audit`、`/fastapi/audit/login`、`/fastapi/tasks`、`/fastapi/settings`：已迁移的只读页面。
- `/api/v1/nodes`、`/api/v1/credentials`、`/api/v1/configs/nodes`、`/api/v1/releases/history`、`/api/v1/audit/*`、`/api/v1/tasks*`、`/api/v1/settings*`：已迁移的可测试 JSON API。

## 目录约束

- `fastops/api/routes/`：按领域拆分路由。
- `fastops/api/deps.py`：登录态、权限等 FastAPI 依赖。
- `fastops/core/`：配置、路径和安全工具。
- `fastops/db/`：SQLite 连接与基础查询工具。
- `fastops/schemas/`：按领域拆分 Pydantic 响应模型，保证 OpenAPI 文档有效。
- `fastops/services/`：不依赖 Django ORM 的业务查询与权限服务。
- `fastops/templates/`：Jinja 页面模板。

## 接口文档要求

1. 新增 API 必须声明 `response_model`，并为筛选、分页等查询参数补充 `description`。
2. 需要登录的 API 使用统一 Cookie 安全方案，保证 `/docs` 能看到鉴权要求。
3. 页面路由与 JSON API 分离：页面放在 `/fastapi/*`，接口放在 `/api/v1/*`。
4. 健康检查保持匿名可访问，用于启动与部署验证。
5. FastAPI 页面 UI 以原 Django 页面为准，保留左侧功能模块菜单、顶部导航、卡片/表格/分页等既有交互基调；接口测试入口放在 `/docs`，不在业务页面暴露调试链接。

## 迁移原则

1. 业务模块边界不变：账号、节点、凭证、配置、发布、任务中心、审计、系统设置、Nginx 运维模块逐个迁移。
2. 数据库优先保留 SQLite，路径沿用 `MNGXOPS_HOME/db.sqlite3`。
3. 每迁移一个模块，先抽取不依赖 Django 的 service/helper，再替换路由和模板。
4. 旧 Django 代码只在对应 FastAPI 路由完成并验证后删除。
5. 旧测试可在模块迁移后删除或重写；迁移期不强制补齐新测试。

## 建议迁移顺序

1. 只读列表页：节点、凭证、配置列表、发布历史、审计日志、任务中心、系统设置已迁移；后续迁移 Nginx 运维历史等剩余只读页。
2. 表单类 CRUD：节点组、凭证、用户组、角色。
3. 异步任务页：发布、同步、升级、安装、启停、卸载。
4. 权限、登录、审计中间件统一收口。
5. 删除剩余 Django 模块、模板、视图和旧测试。
