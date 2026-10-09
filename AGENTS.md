# ngxops 开发策略台账

本文件是 ngxops 重写工作的阶段台账。每项完成后更新状态、实现范围和重要决策；新发现的问题按最小可独立交付的功能补充条目。

## 项目基线

- 本轮完成：NX-064 单文件发行、统一 CLI 与运行日志方案。
- NX-032 后续修正（2026-10-08）：配置管理节点列表显式 JOIN 配置标签，并通过 `contains_eager` 加载关系，修复排序引用未加入主查询的表导致 SQLite `no such column: ngxops_configs.name`。仅查询实现调整，无数据库迁移。

- 目标：在本仓库重写同级 `mngxops` 中的 Nginx 多节点运维平台，将 Django 技术栈迁移为 FastAPI、Jinja2、jQuery、JSON、SQLite3。
- 运行环境：Python 3.9；本机默认虚拟环境为 `D:\PyCharm\联动优势\works\django-labs\venv3\Scripts\activate`。
- 当前状态：NX-001 至 NX-011、NX-020 至 NX-022、NX-030 至 NX-032、NX-040 至 NX-042、NX-050 至 NX-053、NX-060 至 NX-064 已完成；NX-022 已接入独立 SSH/Nginx 探测与节点详情，NX-030 至 NX-032 已接入配置绑定/版本/同步，NX-040 至 NX-041 已接入异步批量发布、发布历史与版本回滚，NX-042 已接入统一任务中心页面、筛选、详情、日志和协作取消，NX-050 已接入源码/模块包管理、四步编译升级、历史和二进制回滚，NX-051 已接入独立全新安装向导、systemd/二进制启动分流、安装历史和自动配置同步，NX-052 已接入 Nginx 启停操作台、批次进度、日志详情和历史，NX-053 已接入 Nginx 卸载，NX-060 已接入操作审计及登录日志，NX-061 已接入分组系统设置和数据保留，NX-062 已接入仪表盘统计和最近任务，NX-063 已核对业务闭环、OpenAPI 和部署文档，NX-064 已提供 PyInstaller 单文件构建配置、英文管理 CLI 和轮转运行日志。同级 `mngxops` 是功能、交互和业务规则的只读参考。
- 参考资料：`dev.md` 是本项目要求；`../mngxops/README.md` 说明产品和运行方式；`../mngxops/docs/` 描述功能、接口、数据模型、UI 与非功能要求；`../mngxops/AGENTS.md` 记录已落地优化结论，但可能遗漏需求、边界或实现细节，也可能不能完整反映当前代码。不得将它当作完整规格或唯一事实来源。
- `../mngxops/optimize.md` 是针对原 Django 仓库的原地渐进迁移计划，其“所有页面不使用服务端模板”的目标与本项目 `dev.md` 指定 Jinja2 冲突。本仓库按 `dev.md` 重写；遇到架构冲突时以本项目要求为准，原项目模型、schema 与数据保护信息仍需逐项核对。
- 历史代码和提交：按 `dev.md` 说明，ngxops 之前的历史提交不作为需求基线。

## 开发约束

1. 按原项目真实操作流复刻功能与 UI。页面布局、文案、筛选、分页、弹窗、空态、状态色和交互均以原项目为准；每个模块开工前都要深入分析 `mngxops` 对应代码，包括路由、视图、模型、服务、模板、静态脚本、接口和现有测试，并与需求文档及台账交叉核对。页面和接口的实际行为、状态流转、权限门禁、失败处理及边界条件都要纳入迁移分析，不能只按 `mngxops/AGENTS.md` 或摘要开发。
2. 迁移业务行为，不照搬 Django 专属实现。原项目中的冗余实现可以清理；对能保持行为一致的性能改进可纳入重写，改变用户可见行为或业务规则时先记录依据。
3. 使用 Python 3.9 兼容语法和 PEP 风格。注释及函数 docstring 用中文；每个函数有一句话说明；函数保持单一职责，避免重复、过长函数和深层嵌套。
4. 页面使用 Jinja2 服务端渲染和 jQuery 增强，不引入无需求的 SPA 或前端构建体系。共享布局、样式和交互应复用公共组件。
5. JSON 接口使用明确的请求/响应模型、状态码和错误语义，并保持 FastAPI OpenAPI 文档可读、有效；页面接口与 JSON 接口的职责要清楚。
6. 长任务的真实状态、进度、结果和日志应持久化并可轮询；前端不得以假倒计时代替执行进度。SSH 和其他阻塞 I/O 不得阻塞 FastAPI 事件循环。
7. 凭证、私钥、会话密钥等敏感信息不得写入日志、审计详情或普通明文存储。批量操作、权限检查、状态门禁和错误反馈按原项目规则实现。
8. 每个阶段按下表细粒度推进；实现后更新状态为“进行中 / 已完成 / 待确认”，并记录行为差异、接口变化或遗留事项。

## 待明确的架构决策

以下决策在基础阶段结合最小垂直切片确定，并写回本文件，不影响先建立功能台账：

- 数据持久层已选择 SQLAlchemy 2.x 同步 Engine/Session；须按 `docs/database.md` 处理事务、外键、迁移和连接生命周期。
- HTML 表单使用 Jinja2；JSON API 使用 FastAPI 路由。NX-003 已确定服务端 SQLite Session、签名 CSRF Cookie、表单/请求头令牌，以及统一认证/权限依赖；具体功能路由按页面与 API 职责分模块登记。
- 长任务执行器的线程/线程池实现、进度更新粒度、启动时遗留任务处理和单进程部署边界。
- SSH 凭证使用数据目录 `.fernet_key` 独立于会话签名密钥加密；密钥与数据库必须共同备份。
- 原 `mngxops` 数据库的 schema 基线登记和兼容接管流程仍待明确；完成核验前不得由 ngxops 写入或升级该库。
- ngxops 使用 `NGXOPS_*` 作为环境变量前缀；为兼容旧部署仍读取 `MNGXOPS_*`，同一配置同时存在时前者优先。数据目录默认使用启动进程的当前工作目录。

## 分阶段任务

状态说明：**待开发**表示尚未在 ngxops 实现；完成条目须同步更新状态和说明。依赖编号表示建议顺序。

### S0 · 基础骨架与横切能力

| 编号 | 工作项 | 验收要点 | 依赖 | 状态 |
|---|---|---|---|---|
| NX-001 | FastAPI 应用骨架、配置与启动入口 | Python 3.9 可启动；配置、日志、静态资源、模板目录和错误处理边界明确；本机路径不成为运行依赖 | - | 已完成 |
| NX-002 | SQLite 数据层与初始化/迁移策略 | 核心表约束、外键、事务、索引和升级路径有文档；并发写入行为明确 | NX-001 | 已完成 |
| NX-003 | 会话、登录态、CSRF、认证与权限依赖 | 匿名访问、登录态、登出、权限拒绝和超管权限行为符合原项目；HTML 与 JSON 错误响应分别明确 | NX-001, NX-002 | 已完成 |
| NX-004 | Jinja2 全局壳、导航和公共交互 | 侧栏/折叠态、权限菜单、主题、表格、分页、确认/提示、图标和公共弹窗与原 UI 对齐 | NX-001, NX-003 | 已完成 |
| NX-005 | 统一异步任务与进度协议 | 持久化任务状态、批次、进度、日志、结果树、触发人；提供可轮询接口；启动后不会把遗留运行任务伪报成功 | NX-002, NX-003 | 已完成 |
| NX-006 | API 文档和接口约定基线 | JSON 接口在 OpenAPI 中有摘要、参数/响应模型、状态码和认证说明；错误结构稳定，便于后续功能测试调用 | NX-001, NX-003 | 已完成 |

**NX-001 实施记录**：新增 `ngxops` 应用工厂与 ASGI 导出、Uvicorn 启动脚本、环境配置、Jinja2/静态目录挂载、`/health` 探活接口及通用 HTML/JSON 异常响应。源码运行默认监听 `127.0.0.1:8000`，冻结运行默认监听 `0.0.0.0:11993`；数据目录由 `NGXOPS_HOME` 覆盖，未设置时使用当前工作目录。创建 app 不连接数据库。依赖锁定为当前 Python 3.9 环境已安装的 FastAPI 0.124.2、Uvicorn 0.38.0 和 Jinja2 3.1.6。未执行自动化测试或启动验证。

**NX-002 实施记录**：采用 SQLAlchemy 2.0.54，新增 Engine/Session 工厂、请求级 Session 依赖、独立 Session 生命周期上下文和显式迁移 runner。SQLite 连接启用外键与 30 秒锁等待；迁移通过 `python -m ngxops.database upgrade` 手动执行，以 `BEGIN IMMEDIATE` 串行化。应用启动不打开数据库或建表；无本项目迁移标记的既有数据库会被拒绝修改。核心约束与原模型来源记录在 `docs/database.md`。NX-002 完成时迁移列表为空；业务表由后续模块分阶段补齐。未执行自动化测试或数据库写入验证。

**NX-003 实施记录**：新增 `auth_user` 最小身份表和 `ngxops_sessions` 服务端会话表，并登记数据库版本 1。会话 Cookie 保存随机句柄，登录辅助函数轮换句柄，登出辅助函数撤销记录；非安全 HTTP 方法由全局 CSRF 依赖校验签名 Cookie、表单/请求头令牌及来源。新增当前用户、登录、超管和可注入 RBAC 解析器依赖；HTML 未登录跳转登录页，JSON/AJAX 返回 401；无权页面遵循同源 Referer 回跳或 Jinja2 403，JSON/AJAX 返回 403。密钥沿用 `NGXOPS_SECRET_KEY`，未设置时保存在数据目录 `.secret_key`。核对原项目 `apps/accounts/views.py`、`login_lock.py`、`users/models.py`、`permissions.py`、账户/RBAC 文档及相关测试后，将登录表单/密码校验/失败锁定留给 NX-010，将 RBAC 表与个人角色优先于用户组角色的解析留给 NX-011；NX-011 接入 `app.state.permission_checker` 前普通用户按配置缺失拒绝。Django session 序列化不兼容且原数据库仍受迁移基线保护。当前 multipart CSRF 使用 `X-CSRFToken` 请求头，Python 3.9 安全解析器待文件上传模块复核。未执行自动化测试、应用启动或数据库写入验证。

**NX-004 实施记录**：新增 `templates/base.html` 全局 Jinja2 壳和 `ngxops/ui.py` 公共渲染上下文入口，集中生成导航、活动菜单、CSRF token、一次性权限提示，并提供 `render_page(request, template_name, context, user, db_session, status_code)` 供后续页面复用。导航按原项目分组和权限组合展示；超级管理员可见管理菜单，普通用户通过 `app.state.permission_checker` 判定，单页内缓存重复权限结果，解析器未配置时按拒绝处理。侧栏使用与原项目相同的 localStorage/sessionStorage 键，支持宽屏图标折叠、子菜单浮层和移动端抽屉；查询参数 `nav` 仅允许覆盖到 `nginx_install`、`upgrade`、`nginx_service`。新增 `static/css/app.css`、`static/js/app.js`、`includes/csrf_field.html`、`includes/pagination.html`、`includes/pagination_footer.html` 和 `includes/empty_state.html`；表格分页上下文约定为 `pagination={page, pages, total, per_page, per_page_options}`。公共 JS 提供 `showToast`、`showConfirm`、`showAlert`、`submitPostConfirm` 和弹窗表格行选择，默认以文本方式插入动态提示，显式 `asHtml=true` 时允许调用方传 HTML；jQuery AJAX 自动携带 `X-CSRFToken`。统一 403 页面改为继承全局布局。与参考项目差异：参考项目没有全局深色主题切换，因此保留单一主题色板；导航 GET 覆盖已实现，POST 的 `nav` 读取留待具体表单/路由接线；导航 URL 按计划中的 FastAPI 路径预留，业务路由尚未开发。Bootstrap、Bootstrap Icons 与 jQuery 通过 CDN 加载，沿用参考项目的外部资源依赖。未运行自动化测试、应用启动或浏览器验证；公共页面接入与真实 RBAC 行为待 NX-010/NX-011 及业务路由落地时验证。

**NX-005 实施记录**：新增迁移版本 2、`ngxops_tasks` 与追加式 `ngxops_task_logs`，保存操作类型、状态、单调进度、当前步骤、JSON 结果树、批次、目标摘要、触发用户和时间；日志以 `(task_id, id)` 游标读取，结果树限制 1 MiB。新增 `ngxops/tasks/executor.py`：每进程 4 个线程，通过独立 SQLAlchemy Session 更新状态；任务函数使用 `TaskContext` 上报进度/结果/脱敏日志、登记本地取消清理回调并在检查点读取取消；异常不保存堆栈或异常文本，任务状态更新受 `pending/running` 条件保护。新增 `GET /api/tasks`、`GET /api/tasks/{task_id}` 和 `POST /api/tasks/{task_id}/cancel`，包含分页、状态/类型/批次筛选、增量日志、结构化 OpenAPI 模型、登录、CSRF 与 RBAC 可见范围。取消立即写入 `cancelled`，执行登记的本地资源关闭回调，不强杀远端命令；应用启动仅在数据库文件已存在时检查任务表，将遗留 `pending/running` 标为 `failed` 并记录中断，不重跑。线程池/Future 只在单进程内，部署限定单个 Uvicorn worker；跨进程队列、远端命令终止和前端任务中心留待后续条目。相对 `mngxops`，终态沿用其五个状态；日志拆成独立行、结果树改为 JSON，且重启遗留任务显式失败，不保留伪活跃状态。业务任务尚未接入。验证：Python 3.9 AST 解析 7 个变更 Python 文件通过；SQLAlchemy mapper 配置与应用导入通过；OpenAPI 生成包含 4 条路径；`git diff --check` 通过。未运行自动化测试或数据库迁移，后续需在备份/独立数据目录执行显式迁移并做业务任务联调。

**NX-006 实施记录**：新增 `ngxops/api/contracts.py` 的共享 `ApiError`/字段校验模型及 OpenAPI 错误响应生成器；JSON API 的 HTTP 异常、参数校验、认证、权限、CSRF 和未预期异常统一返回 `success: false`、`message`，401 可带 `redirect`，422 可带不含原始输入值的 `errors`。页面错误仍按页面语义返回。任务列表、详情、取消和 `/health` 均在 OpenAPI 中声明成功/错误模型及状态码；受保护 API 标注 `sessionid` 会话 Cookie，取消 POST 另标注 `csrftoken` Cookie 与必需的 `X-CSRFToken` 头。新增 `docs/api.md`，并更新任务/安全文档。与参考项目差异：原系统接口以 Django 视图逐项返回 JSON，本项目约定统一错误结构；422 不返回 FastAPI 默认 `detail`，仅提供字段路径和安全提示且不回显输入，保留页面错误响应边界。核对原 `TaskCenterCancelView` 后，终态不可取消沿用 400，并发状态变化返回 409。验证：Python 3.9 AST 解析 7 个变更 Python 文件通过；应用导入和 OpenAPI 生成通过，包含 4 条路径与 8 个 schema，确认会话/CSRF 安全方案、CSRF 头及错误响应引用均存在；直接校验 422/401/404 错误结构，输入值未回显；`git diff --check` 通过。未运行自动化测试。

### S1 · 账户与访问控制

| 编号 | 工作项 | 验收要点 | 依赖 | 状态 |
|---|---|---|---|---|
| NX-010 | 登录、登出、个人资料、改密和登录锁定 | 登录页视觉对齐；密码校验、失败锁定/解锁、会话失效和登录日志符合参考规则 | NX-003, NX-004 | 已完成 |
| NX-011 | 用户、角色、用户组与 RBAC | 支持用户/角色/用户组管理、权限矩阵、直授与组角色解析；导航、页面和接口使用同一权限判断 | NX-010 | 已完成 |

**NX-010 实施记录**：新增 `/login/`、`/logout/`、`/profile/` 和 `/password/change/` 页面路由；登录页独立渲染，资料和改密页继承 NX-004 全局壳。仪表盘由 NX-062 实现前，根路径跳转登录页或个人中心。新增 `ngxops/accounts/passwords.py`，以 Django 默认 `pbkdf2_sha256` 格式（600,000 次迭代）生成/校验摘要；改密校验旧密码、重复输入、至少 8 位、用户名相似度、纯数字和紧凑弱口令表。迁移版本 3 新建 `ngxops_login_failure_states`、`ngxops_login_logs`；连续失败 5 次锁 15 分钟，成功、到期后重新失败、改密时清理相应状态，`clear_login_fail_lock(session, user_id)` 供 NX-011 用户管理接入提前解锁。登录记录包含来源 IP、User-Agent、结果与原项目失败原因枚举，不保存密码；未知用户名执行虚拟摘要计算。登录对同设备静默替换，异设备显示 30 秒确认并在确认后撤销旧会话；改密撤销账户全部旧会话并轮换当前会话。新增 `python -m ngxops.accounts <username>` 首次超级管理员初始化命令，仅允许数据库已迁移至版本 3 且尚无用户时运行。与 `mngxops` 的差异：登出从 GET 改为 POST 以满足全局 CSRF；个人资料只读与参考 `ProfileView` 实现一致；未迁移没有模板/脚本调用的 `check-session/` 心跳路由；登录锁定设置在 NX-061 前使用原项目默认 5/15；Django 全量常见口令词表由紧凑集合代替；提前解锁服务已完成，管理按钮由 NX-011 接线。新增 `docs/accounts.md` 并更新数据库、安全和启动文档。已执行 Python 3.9 AST 解析（37 个 Python 文件）、应用导入/OpenAPI 生成、3 个 Jinja 模板编译、临时 SQLite 迁移/登录锁定/登录日志/密码修改/设备冲突流程烟测及新增文件空白扫描；`git diff --check` 通过。未运行自动化测试、真实数据目录迁移或浏览器验证；本机尚未安装新增 `python-multipart` 依赖。首次部署须显式执行 `python -m ngxops.database upgrade`，再用初始化命令创建首位管理员。

**NX-011 实施记录**：新增 `ngxops/rbac/permission_defs.py`、`models.py`、`service.py` 与 `routes.py`，权限解析统一接入 `app.state.permission_checker`，供导航和 `require_permission` 共用。迁移版本 4 建立权限项、角色、用户组、用户资料、个人角色/直授、用户组成员/角色及角色权限关联表，并初始化 29 个权限项；不在应用启动时改库。新增用户列表、创建/编辑/删除/启停，角色列表、权限矩阵、成员管理，用户组列表、角色维护和成员弹窗；用户最多 3 个个人角色，解锁清除 NX-010 失败锁定，不能删除或停用当前用户。成员接口为 `GET/POST /api/users/teams/{team_id}/members`，有 Pydantic 请求/响应模型、分页/搜索、最多 200 人批量操作、超管会话及 CSRF 门禁。与 `mngxops` 的差异：用户组成员 JSON 接口迁至 `/api/` 并登记 OpenAPI；个人角色与用户组角色分开保存，不复制组角色到个人角色，以遵循模块文档中的个人角色优先、无个人角色时动态继承组角色规则；Jinja 页面复用全局 CSRF、分页、提示和确认交互。渲染验证时修复 `templates/base.html` 对导航 `items` 键被字典方法遮蔽的问题。新增 `templates/rbac/` 页面和 `docs/rbac.md`，更新 `docs/database.md`、`docs/security.md`、`docs/api.md`。验证：Python 3.9 `compileall`、SQLAlchemy mapper 配置、8 个 RBAC Jinja 模板编译、OpenAPI 路径/成功错误模型/session-CSRF 安全方案检查通过；临时 SQLite 执行 v1-v4 迁移、验证权限种子数量、超管登录、空/非空列表与表单渲染、角色/用户/用户组创建和编辑页、成员 API 增删、直授/个人角色优先/用户组回退权限烟测通过；匿名成员 API 返回 401；`git diff --check` 通过。未对真实数据目录执行迁移或做浏览器视觉验证，mngxops 数据库仍不会被自动接管。

**NX-011 后续修正**：资料页和改密页现向全局导航传入请求级 SQLAlchemy Session，恢复普通用户按 RBAC 显示菜单；改密失败回显在线程池使用独立 Session 渲染，避免异步路由在事件循环线程执行同步权限查询。本次未重新运行自动化测试或浏览器验证。

**NX-011 用户组角色优先级修正**：按用户页面功能测试 11 调整解析规则：存在有效用户组角色时使用所有所属组角色的权限并集；没有有效组角色时回退到个人角色；用户直授权限仍独立叠加。用户组角色关联不复制到成员个人角色，解除关联后组成员仍保留成员关系，原有个人角色重新生效。用户列表显示当前生效角色；导航“用户组管理”改指向独立 `/users/teams/` 页面。更新 `ngxops/rbac/service.py`、`ngxops/rbac/routes.py`、`ngxops/ui.py`、`docs/rbac.md`。`tests/test_rbac.py` 覆盖不同权限的优先级、用户列表角色回显、解除关联后的回退、成员和个人授权保留。

### S2 · 节点与凭证资产

| 编号 | 工作项 | 验收要点 | 依赖 | 状态 |
|---|---|---|---|---|
| NX-020 | SSH 凭证 CRUD、加密和启停联调 | 密码/私钥安全存储与解密；启用时按原规则测试关联节点；进度、权限、错误和敏感数据处理完整 | NX-005, NX-011 | 已完成 |
| NX-021 | 节点、节点组及导入导出 | 节点 CRUD、分组、锁定、逻辑删除/同 IP 恢复、筛选分页、模板、批量导入导出与原 UI 对齐 | NX-020 | 已完成 |
| NX-022 | SSH 探测、系统信息与 Nginx 能力识别 | 单个/批量探测和详情采集可追踪；SSH 在线状态与 Nginx 可用状态分开记录；离线/未知门禁一致 | NX-005, NX-021 | 已完成 |

**NX-020 实施记录（已完成）**：迁移版本 5 建立 `ngxops_credentials`，保留 `(name, created_by)` 唯一约束、用户删除级联和认证/测试状态约束。数据目录 `.fernet_key` 独立于会话签名密钥；密码和私钥列仅保存 Fernet 密文，编辑留空保留原密文；私钥由 Paramiko 校验，不接受带口令密钥。`/credentials/` 提供筛选分页、新增/编辑/删除；`GET /api/credentials`、`GET /api/credentials/{id}/secret`、`POST /api/credentials/{id}/toggle-enable` 与 `GET /api/credentials/{id}/enable-progress` 分别受 RBAC、会话和 CSRF 保护，解密响应禁止缓存。接入 NX-021 后，禁用凭证会将活动关联节点置离线；重新启用时用 NX-005 任务线程池对未锁定节点最多 3 并发测试 SSH，再单独写入 SSH 在线状态和 Nginx `-v` 探测结果，锁定节点跳过并可轮询真实进度/结果树。无活动关联节点按 Q97 仅启用、不创建空任务。凭证测试不保存异常原文或凭证明文。Paramiko 5.0.0 无 `DSSKey`，因此不接受无法校验/使用的 DSA 私钥。凭证明文 xlsx 导入/导出仍留 NX-060 审计模块后处理。验证：本轮 `pytest -q tests` 覆盖凭证密文、解密授权、启停、删除、节点任务进度与敏感数据保护；未连接真实 SSH 主机，使用隔离数据库和伪造 SSH 客户端验证任务与状态回写。

**NX-021 实施记录（已完成）**：迁移版本 6 建立 `ngxops_nodes`、`ngxops_node_groups`、节点组成员关联和 `ngxops_node_sync_settings`；IP 唯一约束覆盖活跃与软删除节点，凭证删除置空，节点软删除保留主键及历史关联。新增节点与分组 Jinja 页面、搜索/环境/SSH 状态/节点组筛选和分页、凭证与节点组选择、单/批量逻辑删除、锁定/解锁、同 IP 恢复、xlsx 模板/全文件校验导入/筛选或勾选导出，以及 `GET /api/nodes`、`GET /api/nodes/groups`、批量删除和锁定 API。导出仅包含凭证名称，不含认证材料；导入限制 `.xlsx` 与 8 MiB，任一行失败整批不写。迁移版本 7 为任务加通用 `subject_type/subject_id` 索引，避免通过任务摘要或敏感字段名关联凭证测试。与 mngxops 的差异/后续接线：并发默认数和批量上限暂用原默认 3，待 NX-061 设置模块接入；节点组成员编辑使用表格页面；解锁只重置为未知，SSH 重测由 NX-022 提供；导入/导出及资产写操作的审计接线留 NX-060。新增 `docs/nodes.md`，更新任务、数据库、API、凭证及安全文档和 openpyxl 依赖。验证：Python 3.9 `compileall`、26 个 Jinja 模板编译、SQLAlchemy mapper 配置、OpenAPI 生成（16 条路径、27 个 schema）、迁移 v1-v7、节点/分组页面和 API、xlsx 导入/导出、校验整批拒绝、同 IP 主键恢复、锁定/删除、凭证启用任务与状态回写均通过；本轮 `pytest -q tests` 为 5 passed，`git diff --check` 通过。真实数据目录迁移、真实 SSH 主机和浏览器视觉验证未执行。

**NX-022 实施记录（已完成）**：新增 `ngxops/nodes/tasks.py`，复用 Paramiko 连接和 Nginx 探测实现，将单节点、最多 3 节点批量 SSH/Nginx 探测、系统信息采集和单独 Nginx 版本检测交由 NX-005 持久化任务执行器；任务保留目标、触发人、真实进度、脱敏日志及结构化结果树，并通过 `GET /api/tasks/{task_id}` 轮询。新增 `GET /api/nodes/{node_id}`、`POST /api/nodes/{node_id}/probe`、`POST /api/nodes/probe`、`POST /api/nodes/{node_id}/system-info`、`POST /api/nodes/{node_id}/nginx-probe`，统一要求 `nodes.read` 或 `nodes.ssh_test`，写请求受全局 CSRF 保护；任务中心的受限轮询权限已覆盖四类节点任务。节点列表新增双维度状态、单/批量探测按钮和自动采集详情弹窗。状态规则：SSH 成功写在线，实际连接尝试（成功或失败）都会更新 `last_probe_at`；连接失败写离线，Nginx 命令失败只写 `nginx_available=false` 和 `last_nginx_probe_at`，不把 SSH 改为离线；SSH 失败时不清除原 Nginx 结果；锁定节点的单探测拒绝，批量结果标记失败且不连接。离线/未知节点可发起探测以恢复状态；安装仅依赖 SSH 在线，Nginx 运维门禁由后续模块按 SSH 在线且 Nginx 可用执行。与 `mngxops` 差异：`last_probe_at` 在失败的实际 SSH 尝试后也更新，区别于参考项目仅记录成功时间；详情查询改用 GET；统一使用任务结果树与任务轮询 API；系统信息采集和 Nginx 检测各自在单条 SSH 会话中运行，避免每条命令重复连接；批量并发及上限暂用默认 3，待 NX-061 设置模块接入。新增/更新 `docs/nodes.md`、`docs/tasks.md`、`docs/api.md` 和本台账。验证：Python 3.9 `compileall`、Jinja2 节点列表模板编译、OpenAPI 21 条路径和 5 个 NX-022 路径登记、JavaScript 语法检查及 `git diff --check` 通过；未运行 pytest，未连接真实 SSH 主机或做浏览器视觉验证。

### S3 · 配置管理与同步

| 编号 | 工作项 | 验收要点 | 依赖 | 状态 |
|---|---|---|---|---|
| NX-030 | 配置标签、节点绑定与版本历史 | 标签与绑定分层；绑定路径/内容/状态、版本快照、恢复和差异对比可用；删除状态遵循参考规则 | NX-021 | 已完成 |
| NX-031 | 远程配置发现与同步向导 | 支持配置发现、路径预览、批量/单节点同步及真实进度；同步失败、跳过和结果树可追溯 | NX-005, NX-022, NX-030 | 已完成 |
| NX-032 | 配置列表、筛选与绑定管理交互 | 节点配置明细页、绑定弹窗、未绑定标签区、状态/Nginx 筛选、预览和确认操作符合原页面行为 | NX-030, NX-031 | 已完成 |

**NX-030 实施记录**：新增 SQLAlchemy `Config`、`ConfigBinding`、`BindingVersion` 模型与迁移 v8（`ngxops_configs`、`ngxops_config_bindings`、`ngxops_binding_versions`），保留标签名不唯一、`(config_id, node_id)` 和 `(binding_id, version)` 唯一键、用户/标签/节点/绑定级联关系；绑定同步状态约束只接受当前有效状态。新增 `/configs/` 标签分页、标签新增/编辑/级联删除/详情、多节点绑定创建、绑定正文与路径编辑审阅、解绑/标记删除/恢复、绑定版本列表/正文/恢复和只读逐行对比页面；权限统一使用 `configs.read/create/update/delete`，表单经过全局 CSRF。新建绑定仅允许活动、未锁定、SSH 在线且 Nginx 已检测可用的节点；批量校验后同事务写入绑定和 v1。编辑确认通过预期版本号原子更新，生成新快照并设为 `modified`；恢复历史同样生成新版本且需后续发布。解绑规则：Nginx 不可用时物理删且不连远程；可用时 `not_synced/orphaned/marked_deleted` 物理删，其他状态标记待同步清理。与 mngxops 的差异：页面列表先提供基础分页，NX-032 补节点展开/筛选/绑定弹窗；批量绑定整批事务拒绝部分成功；绑定标签和节点身份固定，只改路径与正文；未接入远程发现、同步或 `ConfigSyncSetting`，交由 NX-031。新增 `docs/configs.md`，更新数据库与 API 文档。验证：Python 3.9 `compileall`、应用导入/ORM mapper/OpenAPI 生成（107 条应用路由、35 条 OpenAPI 路径）、11 个配置模板编译通过；隔离 SQLite v1-v8 与 `TestClient` 页面烟测覆盖标签/绑定创建、编辑审阅和版本 v1-v3、版本对比/详情/恢复、标记删除及恢复、Nginx 不可用物理解绑、标签删除级联；未连接真实 SSH 主机、运行 pytest、迁移真实数据目录或进行浏览器视觉验证。

**NX-030 Q150 状态联动修正**：核对 `mngxops/docs/07-configs.md` 与节点探测/凭证测试写回路径后，补充节点 Nginx 探测确认不可用时将其绑定（除 `orphaned`、`marked_deleted`）转为 `orphaned`。实现位于 `ngxops/configs/services.py::mark_node_bindings_orphaned`，由 `ngxops/nodes/tasks.py::_apply_probe_result` 和 `ngxops/credentials/tasks.py::_apply_node_test` 在同一数据库事务调用；SSH 连接失败未取得 Nginx 结果时不改变绑定状态。更新 `docs/configs.md`。验证：临时 SQLite v1-v8 数据库覆盖两个回写路径中的状态转换及例外状态保留，并确认 SSH 失败时绑定状态不变；NX-030 详情/确认页烟测通过；Python 3.9 `compileall`、应用导入/OpenAPI 生成（35 条路径、37 个 schema）通过；未运行 pytest、连接真实 SSH 主机或迁移真实数据目录。

**NX-031 实施记录（已完成）**：新增 `ngxops/configs/discovery.py` 递归读取主配置与 include，支持绝对/相对路径和安全引用的 glob，扫描复用单条 SSH 会话；内置参数文件跳过，递归最多 3 层、最多发现 500 个文件。新增迁移 v9 和 `ConfigSyncSetting`，每节点保存独立的同步主配置路径、最后更新人；该表与 NX-021 的 `NodeSyncSetting`（节点 xlsx 导入/导出路径）保持分开。新增 `GET /configs/sync/` 节点筛选/统计向导，支持主机/IP、节点组关键词、Nginx 状态筛选、异步路径发现、全量/按发现清单部分同步，以及最多 3 节点批量同步；`POST /api/configs/discover`、`POST /api/configs/sync`、`POST /api/configs/sync/batch` 均使用 Pydantic 请求/响应模型、`configs.sync` 权限、会话与 CSRF 门禁和 OpenAPI 错误契约。发现类型 `config_discover`、同步类型 `config_batch_sync` 由 NX-005 执行器持久化，任务保存真实进度、增量日志、路径/错误结果树；配置正文不写入任务参数、日志或结果，凭证仅在线程运行时解密。完整且无错误的全量发现才执行缺失绑定 orphan 标记和 `marked_deleted` 远程清理；部分同步仅更新选中且本次发现到的路径，不标记其他绑定 orphan，完整扫描后仍处理待删除绑定以保持参考行为；扫描有错误时不进行可能误删的状态变更。已知绑定读取失败写入通用错误摘要和任务关联，远程删除失败保留待删除绑定；远程命令路径经过 shell 引用。相对 `mngxops`：重用 `TaskContext` 与结构化 JSON 结果/任务轮询；把原 `ConfigSyncSetting` 迁移为独立 SQLAlchemy 表而不复用节点导入设置；部分同步改为从本轮远程发现清单勾选路径，允许初次导入子集；完整扫描不成功时跳过 orphan/删除动作，防止读取异常造成误判。新增/更新配置、任务、API、节点路径分离文档及 `tests/test_config_sync.py`。验证：针对测试扫描器、异步发现/正文隔离、全量版本更新/orphan/标记删除、部分同步路径隔离和批量任务的隔离 SQLite/TestClient 测试为 5 passed；Python 3.9 compileall、JavaScript `node --check` 和 `git diff --check` 通过。未连接真实 SSH 主机或做浏览器视觉验证；部署须备份后显式运行 `python -m ngxops.database upgrade` 应用 v9。

**NX-032 实施记录（已完成）**：`GET /configs/` 改为节点分页视图，按主机名/IP/配置名/远程路径逗号关键词 AND 搜索、节点组、绑定状态和 Nginx 状态组合筛选；默认展示活动、未锁定且 Nginx 已识别的节点，状态计数按活跃节点汇总，待推送合并 `not_synced` 与 `modified`。当前配置列表仅显示节点及绑定状态汇总；点击节点进入独立配置明细页。未绑定标签区仅在无搜索、组和绑定状态筛选时显示；按原项目 Q5 不提供未绑定标签批量删除。列表绑定弹窗支持配置/节点组/主机/IP 搜索、多节点选择，按现有绑定禁选重复目标，默认带入标签路径及模板/最近绑定正文；提交继续使用 NX-030 全量校验、事务创建和 v1 快照。解除/恢复与未绑定标签删除使用公共确认弹窗和 CSRF 表单，并支持返回原列表筛选上下文。筛选分页默认值按原配置列表为 10 节点/页。更新 `docs/configs.md`。用户页面功能测试 91–94 修正与验证结果见后续记录。

**NX-032 用户页面功能测试 91–94 修正**：新增配置页将节点选择按钮和已选数量放入目标节点操作区；节点弹窗按 Enter 才应用查询，查询标签、每页选择、结果数量和分页控件使用响应式栅格，点击表行或键盘 Enter/空格均可切换节点。配置主列表只展示节点与聚合统计，新增 `GET /configs/nodes/{node_id}/` 查看绑定名称、路径、版本、状态和操作，并保留列表返回筛选。发现/单节点同步从节点资产 `NodeSyncSetting.main_conf_path` 读取主配置路径；弹窗里显式修改会更新节点资产字段，后续批量同步复用该路径。旧 `ConfigSyncSetting` 表保留但不再读写；Nginx 安装同步节点资产字段，卸载保留该路径。发现结果保留主配置及 include 文件路径，单/多文件选择支持部分同步，并增加发现项全选。更新 `ngxops/configs/routes.py`、`ngxops/configs/api.py`、`ngxops/configs/tasks.py`、安装/卸载路径回写、相关模板/脚本、`docs/configs.md`、`docs/nodes.md`、数据库/卸载文档。验证：`pytest -q tests/test_config_sync.py` 为 11 passed；全套 `pytest -q tests` 为 26 passed、4 failed，失败位于本次未改动的凭证、节点及 RBAC 用例；Python `compileall` 和三个配置脚本的 `node --check` 通过。未连接真实 SSH 或进行浏览器视觉验证；无数据库迁移。

**NX-031 结果树和组合筛选验证补记**：发现结果项按路径展示，不重复保存文件名；同步任务保留每节点完整类别计数与逐项日志，结果树最多展开 75 条操作明细和 20 条错误摘要。主机名/IP 搜索、节点组/标签关键词和 Nginx 状态筛选可组合取交集；组/标签多个关键词支持逗号分隔。新增三节点大结果集测试验证裁剪后 JSON 小于统一任务的 1 MiB 上限，并新增组合筛选回归测试。本轮更新验证数：`tests/test_config_sync.py` 7 passed，全套 `pytest -q tests` 12 passed；Python 编译、`node --check static/js/config-sync.js`、OpenAPI 39 条路径/41 个 schema 及三条新 API 的会话、CSRF Cookie、`X-CSRFToken` 和错误状态声明检查通过；相关文件无尾随空白。真实 SSH 与浏览器视觉验证仍未执行。

### S4 · 发布、回滚与任务中心

| 编号 | 工作项 | 验收要点 | 依赖 | 状态 |
|---|---|---|---|---|
| NX-040 | 发布中心与批量发布执行 | 按节点选择绑定/版本；同节点复用 SSH；先备份、上传并校验，批次内统一 reload；失败与离线节点规则一致 | NX-005, NX-022, NX-030 | 已完成 |
| NX-041 | 发布历史、版本预览与回滚 | 发布历史按批次展示；单条/批量回滚创建新任务；备份、权限、删除节点和 Nginx 状态门禁符合原规则 | NX-040 | 已完成 |
| NX-042 | 统一任务中心列表、详情、取消与结果树 | 汇总同步、探测、发布及运维任务；筛选、批次跳转、协作取消、进度、日志和结果树字段稳定 | NX-005, NX-031, NX-040 | 已完成 |

**NX-040 实施记录**：新增 `/releases/center/` 发布中心和 `/api/releases/nodes`、`/api/releases/nodes/{node_id}/bindings`、`/api/releases/versions/{version_id}`、`POST /api/releases/publish`。页面支持节点搜索、节点组/环境/SSH/Nginx/绑定状态组合筛选，节点展开和绑定选择、逐绑定选版本、正文预览、确认清单及按任务 API 轮询真实进度/日志/结果树。发布与版本读取受 `releases.publish` / `releases.read` RBAC 保护；发布 API 最多接受 500 个绑定且最多 3 个可发布节点，同一发布批次运行期间返回 409；锁定/删除/离线/Nginx 未确认可用的节点按原规则跳过。普通绑定按选定版本发布；`marked_deleted` 绑定走远程删除并在节点 reload 成功后物理删除本地记录。

执行通过 NX-005 `release_publish` 任务在线程池运行，不新增 Django 式 `ReleaseTask`/`TaskCenterTask` 双模型或数据库迁移；每日 `release-YYMMDD-XXXX` 批次号、目标摘要和节点→绑定结果树保存在统一任务记录。每节点使用一条 SSH，会话内串行处理：远程存在目标时先按 `{backup_dir}/{hostname}/` 备份；SFTP 写 `/tmp` 唯一临时文件；校验临时文件大小/MD5，复制并校验目标文件；逐绑定执行 `nginx -t`，整节点通过后仅执行一次 reload/start。当前绑定成功后写入 `synced_version`、MD5、同步状态和时间；同节点后续准备失败或 reload 失败会恢复本批已替换文件，首次发布则删除新文件。跨节点并发与节点勾选上限沿用 NX-061 前默认 3。备份目录可通过 `NGXOPS_RELEASE_BACKUP_DIR` 配置，默认沿用原项目路径。

与参考项目的差异/边界：节点和绑定选择 API 使用分页 Pydantic 契约；业务进度统一复用 NX-005 任务日志/结果树，配置正文只在线程内存中用于上传，不写入任务数据；结果树路径摘要最多 160 个字符，远程命令原始输出不写入任务数据以防 `nginx -t` 回显配置行；待删除文件新增备份，校验或统一 reload 失败时恢复，成功 reload 后才物理删除本地绑定。发布历史与回滚页由 NX-041 实现，统一任务中心 UI 留 NX-042。取消会关闭已登记 SSH 连接并协作恢复尚未 reload 的文件，已发出的远程命令不能强制终止。未运行 pytest、真实 SSH 主机或浏览器视觉验证；NX-040 无新迁移。

**NX-041 实施记录**：新增 `/releases/` 发布历史页面、`GET /api/releases/history`、按需分页版本清单/预览接口和 `POST /api/releases/rollback`。历史按 `source_batch` 分页并以批次→节点→配置展示，支持关键词、批次号、节点 IP、结果状态筛选；已删除节点和已清理绑定仍从统一任务结果树展示，历史版本元数据按需分页读取，正文通过关联历史项的预览接口读取。历史读取要求 `releases.read`，回滚创建要求 `releases.publish` 和 CSRF；`release_rollback` 已加入受限任务轮询权限范围。

回滚仅允许已完成的成功/失败发布项和仍可用的发布操作。单条回滚可选任一不同于历史发布版本的现存绑定快照；批量回滚默认取各绑定本次发布版本的上一版，同一绑定跨批次只允许批量选择最新任务。创建时复核节点未删除、未锁定、SSH 在线、Nginx 已确认可用、凭证启用，以及绑定存在、未标记删除、远程路径未变化；批量中已失效项目返回跳过原因，执行前仍由发布执行器复核节点状态。回滚复用发布的备份、上传、MD5、`nginx -t`、节点统一 reload 和失败恢复流程，并创建新的 `release-YYMMDD-XXXX` `release_rollback` 任务。发布和回滚共用 pending/running 批次门禁，不新增数据库表或迁移。

行为差异/边界：手动回滚按配置版本快照重新部署，回滚前仍备份远程当前文件；远程删除历史不可回滚；新任务保存受限路径摘要及 SHA-256 指纹，发现绑定路径变化时拒绝回滚；NX-041 前已创建且超长路径摘要被截断的任务没有指纹，无法安全校验路径，因此该类历史不能回滚。批量回滚只使用上一版，指定其他版本限单条请求。验证：Python 3.9 `compileall`、JavaScript `node --check`、历史模板编译和应用 OpenAPI 路径/响应模型检查通过；未运行自动化测试、真实 SSH 或浏览器视觉验证；NX-041 无新迁移。

**NX-042 实施记录**：新增 `/tasks/` 任务列表与 `/tasks/{task_id}/` 详情 Jinja 页面，沿用统一任务表，不新增业务任务模型或迁移。列表按创建时间倒序分页，提供原交互的逗号关键词标签、任务类型/状态自动筛选、15 条默认分页（可选 10/15/30/50）和定宽摘要表；每个关键词在批次号、主机名、IP 中 OR 匹配，关键词之间 AND。补充 `GET /api/tasks` 的 `search` 参数并与页面共用过滤规则；页面路由不加入 OpenAPI。详情展示触发人、时间、目标、进度、结果树和追加式日志；活跃任务轮询统一任务 API 更新真实进度/结果/日志，日志按 200 条游标增量读取并可继续加载。取消复用 `POST /api/tasks/{task_id}/cancel`、CSRF 和执行器资源关闭回调；终态返回 400、状态竞态返回 409，远端命令仍遵循协作式取消边界。

任务列表、详情、取消共享 NX-005/API 的 RBAC 可见范围；超级管理员或 `releases.read` 可查全部，其他业务权限仅查询本人可见任务。导航加入 `configs.sync` 并与任务 API 映射一致。与 `mngxops` 的差异：任务中心地址使用 `/tasks/`，因为 `/releases/` 已由 NX-041 用作发布历史；配置发现 `config_discover` 在 ngxops 是 NX-031 实际创建的类型，故可筛选，旧系统未启用的漂移/Glob 类型仍隐藏；`releases.publish` 沿用 NX-041 已登记的本人发布/回滚轮询权限。结构化 JSON 结果树由公共递归视图展示，日志按 NX-005 独立行呈现，不重建 Django 文本结果字段。新增 `tests/test_task_center.py`，验证多词筛选、结果/日志、搜索 API、取消终态门禁、受限用户隔离、发布权限回跳和 OpenAPI 边界。验证：全套 `pytest -q tests` 17 passed；Python 3.9 `compileall`、`node --check static/js/task-center.js`、OpenAPI 搜索参数检查、真实 HTTP 登录及任务中心响应检查通过。未连接真实 SSH 或做浏览器视觉验证；NX-042 无新迁移。

### S5 · Nginx 生命周期运维

| 编号 | 工作项 | 验收要点 | 依赖 | 状态 |
|---|---|---|---|---|
| NX-050 | 源码包/第三方模块包与升级向导 | 上传校验、包管理、四步升级配置、模块参数、执行进度/日志/历史与取消/回滚符合原功能 | NX-005, NX-022 | 已完成 |
| NX-051 | Nginx 全新安装 | 节点选择、安装参数、systemd 能力分流、权限/端口告警、进度和安装历史符合原流程 | NX-005, NX-022 | 已完成 |
| NX-052 | Nginx 启停操作台 | 支持 start/stop/reload/restart；批量门禁、批次进度、日志详情和历史一致 | NX-005, NX-022 | 已完成 |
| NX-053 | Nginx 卸载 | 预览与确认准确；源码安装和 yum/apt 包安装分流；卸载后更新节点状态并处理 orphaned 绑定 | NX-005, NX-022, NX-030 | 已完成 |

**NX-050 实施记录（已完成）**：新增迁移 v10 和 `ngxops_nginx_source_packages`、`ngxops_nginx_module_packages`、`ngxops_nginx_upgrade_runs`，模型分别保存上传元数据和每节点升级/回滚快照。源码包支持 `.tar.gz/.tgz`，离线模块包支持 `.tar.gz/.tgz/.zip`；默认 20 MiB 上传限制由 `NGXOPS_UPGRADE_PACKAGE_MAX_SIZE_MB` 配置，归档校验成员路径、链接/特殊文件类型、成员数和解压体积，文件保存在数据目录 `nginx_packages/`。覆盖/删除与批次创建通过进程内锁串行化，仍被活动任务引用的包不可覆盖或删除。

新增 `/upgrade/center/` 四步升级向导、源码/模块包管理、按节点分页的 `/upgrade/history/` 和 `/upgrade/tasks/{task_id}/`；JSON API 包含 `nginx -V` 读取、配置解析/预览、批量任务创建、阶段取消和二进制回滚，统一任务类型为 `nginx_upgrade` / `nginx_rollback`，任务进度、日志和结果树沿用 NX-005。升级门禁要求节点未删除/未锁定、SSH 在线、凭证启用及 Nginx 可用；当前批次最多 3 节点。升级线程依次执行工具检查、源码上传/MD5、解压、Git 或离线模块准备、备份、configure/make/make install、`nginx -t`、reload/start 和版本回写。configure 参数使用 shell token 解析与引用序列化，切换路径的 `--prefix` 和显式 `--sbin-path` 由服务端改写。回滚只恢复升级前二进制并重新加载，不恢复旧版 configure 或第三方模块源码。

与 `mngxops` 的实现差异：全新安装模式留给 NX-051；Nginx 任务改为统一任务记录而非 Django `NginxUpgradeTask`/Task Center 双写；异步进度和增量日志复用统一 API；取消仅允许 pending、读取配置和源码包上传阶段，已发远程命令遵循协作式检查点；运行批量上限暂固定为 3，待 NX-061 接入设置。升级详情和共享任务中心按 `upgrade.execute` 与当前阶段显示取消/回滚操作。上传归档增加路径、特殊文件和展开体积检查；HTTP(S) Git URL 禁止嵌入用户凭证。新增 `docs/upgrade.md`，更新 API、任务、数据库、README 配置说明和本台账。验证：Python 3.9 `compileall ngxops`、两个升级 JS `node --check`、应用导入和 OpenAPI 生成（57 条路径，8 条升级 API）、47 个 Jinja 模板编译、`git diff --check` 通过。未运行 pytest、真实 SSH/编译或浏览器验证，未对实际数据库执行 v10 迁移；部署需备份数据库、`.fernet_key`、`.secret_key` 和 `nginx_packages/`，再显式执行 `python -m ngxops.database upgrade`。

**NX-051 实施记录（已完成）**：新增迁移 v11 和 `ngxops_nginx_install_runs`，记录单节点安装快照、批次、configure 参数、阶段、路径及独立配置同步摘要；为兼容 NX-011 初始权限种子，对缺失的 `nginx_install.read/create` 使用 `INSERT OR IGNORE` 补种。新增安装首页、三步向导、历史筛选分页、任务详情/增量日志页及三个 JSON API；节点选择要求未删除、未锁定、SSH 在线且启用凭证，最多 3 台，已有/未知 Nginx 状态不拦截安装。独立流水线复用 NX-050 安全归档、模块准备和 SSH 助手，执行 gcc/make 检查、源码传输校验、configure/make/make install、端口改写、`nginx -t` 和启动；可管理 systemd 时注册、enable、start `nginx.service`，systemd 无管理能力时直接启动二进制，管理操作失败不静默降级。安装成功回写节点 Nginx 状态/版本/路径及两类配置主路径，随后复用完整配置同步；同步失败单独记录，不否定安装结果。取消仅允许 pending、工具检查、上传、解压和模块准备阶段；包管理与安装批次共享进程内锁，活动安装引用的源码/模块包不能覆盖或删除。

与 `mngxops` 的实现差异：安装和升级各用独立统一任务流水线；任务中心复用 `nginx_install` 权限范围和增量日志 API；安装的 `pending/running` 阶段折叠为统一任务状态，历史提供“进行中”组合筛选；最大批量暂固定为 3，待 NX-061 接入设置。端口 80 的非 root SSH 风险在参数步离开时提示，开始安装确认再次解释权限分流；已有 Nginx 风险在确认页和开始确认提示。新增 `docs/nginx-install.md`，更新 API、数据库、任务、升级、README 和本台账。验证：全套 `pytest -q tests` 为 19 passed；Python 3.9 `compileall`、安装 JS `node --check`、应用导入/OpenAPI 三条安装 API 检查和安装首页/向导/详情渲染及早期取消检查通过。测试验证了迁移、批次门禁与状态、历史运行筛选、包活动引用保护、配置主路径回写和路径覆盖参数拒绝。未连接真实 SSH 节点、运行编译或验证实际 systemd；未对真实数据目录执行 v11 迁移或浏览器视觉验证。部署前备份数据库、`.fernet_key`、`.secret_key` 和 `nginx_packages/`，再显式运行 `python -m ngxops.database upgrade`。

**NX-052 实施记录（已完成）**：新增 `/nginx/service/` 启停操作台、节点选择器、近期任务和右侧实时批次进度，`/nginx/service/history/` 搜索/状态筛选/分页，以及 `/nginx/service/task/{id}/log/` 任务详情与增量完整日志。新增 `GET /api/nginx/service/nodes`、`POST /api/nginx/service/execute` 和 `GET /api/nginx/service/tasks/{id}`，使用明确响应模型及 OpenAPI 错误/CSRF 契约；页面、节点选择、历史和日志要求 `nginx_service.read`，批次创建要求 `nginx_service.operate`。执行复用 NX-005 线程池、`ngxops_tasks` 和 `ngxops_task_logs`，不新建业务表；批次号为 `OP-YYMMDD-NNNN`，逐节点写入进度、结果树和脱敏日志。批次门禁为节点未删除/未锁定、SSH 在线、Nginx 已探测可用及凭证启用；后台再次复核并临时解密凭证。每节点探测活动/启用的 `nginx` systemd unit，否则安全引用节点 Nginx 二进制；二进制 stop 先尝试 `-s quit` 再回退 `-s stop`，restart 尝试退出后启动，reload 始终为纯 reload。动作不改写节点 SSH 在线或 Nginx 探测状态。最大批量暂固定为 3，待 NX-061 设置模块接入。新增 `docs/nginx-service.md`，更新 API、任务、数据库、README 和本台账；无数据库迁移。验证：Python 3.9 `compileall ngxops`、`node --check static/js/nginx-service.js`、应用导入和 OpenAPI 三条启停 API/会话与 CSRF 安全声明生成、三个 Jinja 模板编译、`git diff --check` 通过；未运行 pytest、连接真实 SSH 节点或做浏览器视觉验证。

**NX-053 实施记录（已完成）**：新增迁移 v12 和 `ngxops_nginx_uninstall_runs`，保存卸载批次、节点身份、软件包来源、prefix、可选目录及执行选项快照；新增 `nginx_uninstall.read/execute` 权限，并将已有 `nodes.read/update` 的角色和个人直授复制到对应卸载权限。新增卸载首页、三步预览/确认中心、历史筛选分页和任务日志详情页；JSON API 提供节点筛选、SSH 来源/路径预览、批次创建、批次进度及增量日志详情。任务类型为 `nginx_uninstall`，按节点复用 NX-005 持久化任务、结果树、日志和协作取消，单批最多 3 台。

卸载前要求节点活动、未锁定、SSH 在线、凭证启用且 Nginx 可用；创建和后台执行阶段再次校验。通过 `rpm -qf`/`dpkg -S` 识别软件包，使用 dnf/yum/apt-get `remove`，不删除包管理器维护的文件、不执行 purge；Debian 命令通过 `env DEBIAN_FRONTEND=noninteractive` 运行以兼容 sudo。源码安装按 `nginx -V` 的 prefix 和 path 预览，危险根路径、路径穿越和路径变化会拒绝，删除目标会去重；systemd 清理仅限 `/etc/systemd/system` 的平台 unit，不移除发行版 unit。卸载成功或主程序已移除后的部分失败均回写节点 `nginx_available=False`、清空 Nginx 路径/版本及配置发现主路径，并将适用的配置绑定置为 `orphaned`；节点导入/导出路径和配置版本历史保留。

与 `mngxops` 的差异：执行改用统一任务中心与逐节点快照，不双写 Django 专用任务；取消沿用协作式检查点，不强杀远程命令；增加路径校验、父子目标归并及包管理器文件保护。修正实现审阅发现的快照 `package_manager` 键名不一致和 sudo 下 Debian 环境变量调用形式。新增 `docs/nginx-uninstall.md`，更新 API、任务、数据库说明和本台账。验证：Python 3.9 `compileall ngxops`、卸载 JS `node --check`、58 个 Jinja 模板编译、应用导入/OpenAPI 检查（共 68 条路径，卸载 5 条 API，写请求声明 Session/CSRF 与错误状态）、临时 SQLite 全量 v1-v12 迁移和权限种子检查、NX-053 文件尾随空白扫描通过。未运行 pytest、连接真实 SSH 节点或做浏览器视觉验证；部署前备份数据库和密钥，再显式运行 `python -m ngxops.database upgrade` 应用 v12。

### S6 · 平台配套与运维收尾

| 编号 | 工作项 | 验收要点 | 依赖 | 状态 |
|---|---|---|---|---|
| NX-060 | 操作审计与登录日志 | 重要写操作和批次可追溯到操作人；任务关联可跳转；日志不含凭证明文、私钥或会话密钥 | NX-003, NX-005 | 已完成 |
| NX-061 | 系统设置与数据保留 | 设置项可读写且实际接线；默认值初始化不覆盖用户值；清理跳过进行中任务并遵守保留天数 | NX-002, NX-005 | 已完成 |
| NX-062 | 仪表盘与最近任务 | 统计、快捷入口按权限展示；任务跳转、空态和轮询数据准确 | NX-011, NX-021, NX-040, NX-050, NX-051 | 已完成 |
| NX-063 | 全流程核对、接口文档与部署说明 | 按“凭证→节点→同步→发布/回滚→生命周期运维”核对行为/UI；OpenAPI 与实现一致；补齐启动、初始化、配置和限制说明 | NX-010–NX-062 | 已完成 |

**NX-060 实施记录（已完成）**：新增迁移 v13 与 `ngxops_audit_logs`，保存操作人 ID/名称快照、模块、动作、来源 IP、结果、摘要、可选任务 ID、批次号和 UTC 时间；用户删除时保留日志并将用户 ID 置空，任务 ID 不设外键以便任务清理后仍可追溯。新增 `/audit/` 操作日志和 `/audit/logins/` 登录日志页面，统一要求 `audit.read`，支持关键词、模块/结果/日期筛选、分页、详情展开、模块入口与任务详情跳转；日期筛选按北京时间解释并映射 SQLite UTC 时间。登录记录复用 NX-010 的 `ngxops_login_logs`，不复制密码或会话内容。

通过 SQLAlchemy Unit of Work 在业务事务内捕获用户、凭证、节点/节点组、配置/绑定/版本、角色/用户组和 Nginx 源码/模块包的变更；节点逻辑删除记作删除，详情只来自白名单对象标识，不保存字段前后值。统一任务创建与取消写入带任务 ID/批次号的摘要；节点与凭证明文工作簿的导入/导出采用批次摘要；RBAC 用户授权、角色/用户组成员和管理员清除登录锁定有摘要记录。批量摘要与关联业务变更在同一事务提交。

凭证导出仅超级管理员可用；批量导入要求 `credentials.create`，上传限制 `.xlsx` 与 8 MiB，整文件校验后在同一事务中新建当前用户凭证。工作簿内或已有凭证重名时拒绝整批，密码/私钥仍仅以 Fernet 密文入库。批量删除继续独立要求 `credentials.delete`，不随导入权限开放。导出按勾选 ID 或当前筛选范围生成明文工作簿，响应设 `Cache-Control: no-store`。审计只写数量、范围和最多 20 个凭证名称，不含密码/私钥；模板不提供可误导入的示例凭证。导入保留密码中的有效首尾空格，仅剥除 Excel 单元格外围换行，避免改变认证材料。客户端 IP 使用 ASGI 已解析地址，不直接信任请求头中的 `X-Forwarded-For`。

与 `mngxops` 的差异：任务关联改为 NX-005 统一任务 ID，不保留 Django 专属 Task Center 外键；异步记录与业务行同事务写入，用户删除后保留名称快照；页面使用 Jinja2 与公共分页/提示组件。新增 `ngxops/audit/`、`templates/audit/` 和 `docs/audit.md`，更新凭证、节点、API、数据库文档及迁移清单。验证：Python 编译、应用导入/OpenAPI 生成（71 条路径）、审计和凭证模板编译、`git diff --check` 与新增文件尾随空白扫描通过；隔离临时预览库成功应用 v1-v13，`/health` 和 `/login/` 返回 200。未运行 pytest、浏览器视觉验证或真实数据目录迁移。部署前停止服务并备份数据库，再显式执行 `python -m ngxops.database upgrade` 应用 v13。

**NX-061 实施记录（已完成）**：新增迁移 v14 和 `ngxops_system_settings`，按已接线 `PRESET_SETTINGS` 保存键值、类型、分组、校验展示元数据及最近修改人；唯一 key 和分组/排序索引保留，用户删除时修改人置空。`initialize_defaults` 补缺并刷新展示元数据，不覆盖现有 value；设置读取遇到缺失行时回退代码预置值。新增 `/settings/` 分组页以及 `GET /api/settings/group`、`GET /api/settings/all`、`POST /api/settings/group`；查看要求 `settings.read`，写入限超级管理员并经全局 CSRF。整数项按预置 min/max 校验，整组验证后才修改；审计只记录分组、修改 key 和数量，不写设置值。设置列表只登记当前有消费者的配置；仪表盘自动刷新项随后由 NX-062 接入。

接入登录锁定阈值/时长、节点批量上限和默认 SSH 端口、SSH 超时/重试、配置发现深度和默认路径、发布备份目录、源码/模块包上传上限、升级/安装向导默认值、近期任务数量及全站异步任务轮询间隔。批量上限同时用于页面选择反馈和后端门禁；SSH 超时/重试通过共享 Paramiko 连接路径覆盖探测、配置、发布、升级、安装、启停和卸载。卸载预览和任务快照也读取当前发布备份目录、编译工作目录设置，并在任务创建时重新校验清理路径。新增每日后台保留清理并提供 `python -m ngxops.settings purge` 手动执行：使用 UTC 创建时间与保留天数，0 表示关闭；任务中心、发布、升级、操作审计和登录日志分别按设置清理，跳过 pending/running 任务及升级活动阶段，任务日志和业务运行快照按外键级联。清理在独立 Session/线程执行，每进程每天最多触发一次，保持单 Uvicorn worker 部署边界。

新增 `docs/settings.md`，更新账户、审计、API、数据库、卸载、安装、README 和本台账。与 `mngxops` 的差异：Django 缓存/信号改为同步 SQLAlchemy 设置读取和统一任务表分类清理；默认补齐由设置读取/页面路径触发，不在启动阶段连接数据库；手动清理使用 `python -m ngxops.settings purge`，不引入 Django 管理命令。NX-061 代码已完成；未运行 pytest。部署前备份并显式应用 v14。

**NX-061 最终验证补记**：Python 3.9 AST 解析 108 个 Python 文件通过；应用导入、SQLAlchemy mapper 配置、OpenAPI 生成（73 条路径、117 个 schema）、62 个 Jinja 模板加载和 14 条迁移登记检查通过；12 个 JavaScript 文件 `node --check` 通过；对工作区 209 个文本文件扫描未发现尾随空白，`git diff --check` 通过。未运行 pytest、执行数据库迁移或清理、连接真实 SSH 节点或进行浏览器视觉验证。当前工作区的大部分项目文件为未跟踪文件，Git 差异检查不覆盖它们，因此另行执行文本空白扫描。

**NX-062 实施记录（已完成）**：新增仪表盘首页、统计轮询 API、模板、样式和脚本。首页按节点、配置绑定和任务分组展示数字，快捷入口和卡片跳转按目标模块权限控制；最近任务及任务统计复用统一任务中心的可见范围和摘要格式，受 dashboard.recent_tasks_count 条数设置限制。统计 API 使用 DashboardStatsResponse 并声明会话认证及错误响应；系统刷新间隔设置 system.dashboard_refresh_interval 默认 30 秒，可设 5 至 3600 秒，无数据库迁移。根路径现渲染仪表盘，已登录用户访问登录页会跳回首页。节点与配置统计排除软删除节点；待推送数合并 not_synced 和 modified，以对齐 NX-032 筛选。新增 docs/dashboard.md 并更新 API、设置说明。未运行 pytest、执行数据库迁移、连接远程节点或做浏览器视觉验证；静态检查结果补记如下。

**NX-062 验证补记**：Python 3.9 虚拟环境 compileall、dashboard/base/empty-state 三个 Jinja 模板编译、dashboard.js 的 node --check、应用导入与 OpenAPI 生成通过；确认 /api/stats/ 引用 DashboardStatsResponse 并声明 SessionCookie，根路由仅由 dashboard_home 处理。新增与更新文本文件无尾随空白，git diff --check 通过。按预览配置启动本地服务，/health 和 /login/ 均返回 HTTP 200；未运行 pytest、执行数据库迁移或登录后进行浏览器视觉检查。

**NX-063 实施记录（已完成）**：按凭证、节点、配置同步、发布/回滚及安装/升级/启停/卸载闭环，交叉核对 ngxops 页面路由、Pydantic API、服务门禁、任务状态、模板/脚本与 NX-010 至 NX-062 记录，并对照同级 mngxops 的模块文档、路由、服务、模板和测试范围。补齐 docs/README.md 文档索引与 docs/deployment.md 启动部署说明，覆盖 Python 版本、统一数据目录、显式 v14 迁移、首个管理员、环境变量、密钥/包备份、单 worker、重启遗留任务、CDN 依赖及原 Django 数据库不接管边界；README 功能清单和文档入口同步更新。基于应用生成的 OpenAPI 对照 docs/api.md 全部 59 个 JSON/探活操作，修正凭证启停、凭证导出、节点批量删除和节点锁定共 4 条错误状态码；路径、状态码和成功响应模型均逐项一致。58 个受保护 API 的会话认证、写请求 CSRF Cookie/请求头及 ApiError 错误响应模型声明完整。未改变业务路由或数据库 schema。静态核对 OpenAPI、20 份 Markdown 文档链接和文本空白通过；未运行 pytest、连接真实 SSH 节点或做浏览器视觉验证。

### S7 · 单文件发行与本地管理 CLI

| 编号 | 工作项 | 验收要点 | 依赖 | 状态 |
|---|---|---|---|---|
| NX-064 | 单文件构建、服务启停、数据库和管理员命令、运行日志 | Python 3.9 可在 Windows amd64、Linux amd64/arm64、macOS amd64/arm64 原生构建单文件；产物按 `ngxops-{os}-{cpu}` 命名；英文 CLI 支持 `--help`、前后台启停、状态查询、数据库初始化/升级、默认 admin 创建/重置和可选用户名；轮转日志记录请求、业务操作及异常且不泄露凭证 | NX-001, NX-003, NX-010, NX-060, NX-063 | 已完成 |

**NX-064 实施记录（已完成）**：新增 `ngxops.spec`，PyInstaller console one-file 包含 `templates/`、`static/` 和 Paramiko 动态子模块；构建按操作系统/CPU 架构分别执行。运行与构建依赖统一登记在 `requirements.txt`。源码与冻结程序未设置 `NGXOPS_HOME` 时，均将启动进程当前工作目录作为数据目录；仍可通过 `NGXOPS_HOME` 或 CLI `--home` 覆盖。新增 `python -m ngxops` 及二进制共用英文 CLI：`serve/start/stop/status`、`database init/upgrade`、`admin create/reset`；密码通过 getpass 隐藏输入，密码重置只接受超级管理员、清除登录锁并撤销既有会话。后台启停使用 OS 文件锁、进程创建时间/路径/随机标记校验、健康检查和平台信号，固定单 worker。新增 `logs/ngxops.log`、`logs/ngxops-cli.log` 10 MiB/10 份轮转；记录请求路径/状态/时长/用户/IP、提交后的业务审计摘要、CLI 管理操作、后台任务和未处理异常；不记录查询参数、正文、Cookie、认证头或 SQL 绑定参数，常见敏感字段在格式化时遮蔽。新增 `docs/packaging.md`，更新 README、部署、账户、安全和文档索引。未新增数据库迁移；本轮未执行 PyInstaller 构建和后台启停烟测，须在每个目标操作系统验证二进制生命周期。

**NX-064 多平台构建补记**：新增英文帮助的 `tools/build_binary.py` 与共享目标映射 `tools/build_target.py`；Windows amd64 输出 `ngxops-win10-amd.exe`，Linux amd64/arm64 输出 `ngxops-linux-amd64`/`ngxops-linux-arm64`，macOS amd64/arm64 输出 `ngxops-macos-amd64`/`ngxops-macos-arm64`。必须在目标操作系统和 CPU 架构原生构建；Linux glibc 兼容基线按最旧目标发行版选择。未在本轮实际构建各平台产物。

**NX-064 管理命令简化补记**：`admin create` 和 `admin reset` 省略用户名时默认使用 `admin`；仍可附加其他用户名。原 `admin reset-password` 统一收敛为 `admin reset`。README、账户、部署和打包文档已同步。

**NX-064 CLI 配置更名与默认值补记**：新配置前缀为 `NGXOPS_*`，为兼容已有部署仍读取同名 `MNGXOPS_*`，两者同时设置时新前缀优先。未指定 home 时，源码与单文件程序都使用启动命令所在的当前工作目录。根帮助将 home 默认值显示为可移植的 `NGXOPS_HOME or ./`，不嵌入构建机器的绝对路径；子命令帮助展示带默认值的选项，`admin create/reset` 的位置参数用户名显示 `(default: admin)`。旧默认数据目录不会自动迁移。验证：Python 3.9 compileall、源码 root/admin/database/serve help、Windows amd64 PyInstaller 构建及冻结版 root/admin create help 通过；冻结版 help 在沙箱外运行以允许 PyInstaller 解压临时运行库。未执行数据库初始化或后台服务启停。

## 后续更新格式

每个条目完成时保留编号，并补充：实际完成范围、关键文件/接口、与 mngxops 的行为差异及理由、验证结果、未完成项。若参考项目规则不清或 UI 无法确认，先将对应条目标为“待确认”，记录具体问题后再推进相关行为。

## 用户页面功能测试
PS：这个产品主要面向 Linux 运维工程师，目前是用户在测试，你不需要启动程序，按需调整即可，同时不要改调整点的序号。

### 全局交互规范

- 操作成功、失败和状态反馈统一使用右上角滑入式 `showToast`，默认 3 秒自动消失并显示倒计时进度；需要用户阅读或确认的内容使用全局 `showAlert`/`showConfirm` 自定义弹窗，不使用浏览器原生 `alert`、`confirm`。
- 查询输入使用 `data-query-tags` 公共控件；回车将当前输入提交为可移除标签并查询，中英文逗号均可拆分关键词，Backspace/Delete 可移除最后一个标签，移除标签后自动更新结果。筛选后保留查询值和搜索焦点。
- 同一查询框中的多个关键词按 AND 匹配；不同查询条件也按 AND 组合，只有需求明确规定时才使用 OR。搜索框与其他筛选器放在紧凑、响应式的查询行中，直接位于列表内容上方；不增加独立“查询条件”标题或空白行。
- 服务端列表使用公共分页页脚，显示总数、当前页/总页数和每页条数；翻页及修改每页条数保留全部筛选条件。弹窗内客户端分页也要显示记录数、页码、每页选项和可见翻页按钮，分页时保留跨页选择。
- 查询、分页、确认和提示优先复用公共模板/JS/CSS 组件；业务静态资源变更后递增模板中的版本参数，避免浏览器继续读取旧缓存。长表格弹窗使用可滚动布局，分页和操作区不得被内容遮挡。

1. **已确认**：左侧菜单栏主项文字保持 16px、分组标题保持 14.4px；恢复原有紫蓝渐变与浅色文字。
2. **已确认**：主菜单悬停使用低对比底色、当前项使用浅蓝窄标记；子菜单以文字缩进、竖向引导线和较轻字重表达层级，选中项恢复白色半透明底色；折叠弹出菜单及移动抽屉样式一致，链接点击区域保持原尺寸。
3. **已确认**：侧栏收缩后菜单项宽度一致，页面初始化时子菜单弹层默认收起。
4. **已确认**：用户直授权限和角色权限矩阵共用 `templates/rbac/_permission_matrix.html`，只显示中文资源名称，不再显示 `nodes` 等内部资源码。用户列表和用户组关联角色处显示的是用户自定义的角色名称，本次不改写角色名或数据库权限编码。验证：模板静态核对；未运行自动化测试或浏览器视觉验证。
5. **已确认**：Nginx 全新安装与编译安装/升级共用 `upgrade.read/create/delete/execute` 权限，权限矩阵只显示“Nginx 安装/升级”一行。安装历史/包下载用 `read`，包上传用 `create`，包删除用 `delete`，安装/升级任务执行和取消用 `execute`；两个向导入口都接受 `read` 或 `execute`，只读用户不能提交任务；任务类型仍分别保存。迁移 v15 将旧 `nginx_install.read` 授权复制到 `upgrade.read`、旧 `nginx_install.create` 复制到 `upgrade.execute`，覆盖角色及用户直授权后删除旧权限项。与参考实现的独立安装门禁不同，本项目按用户确认统一使用公共安装/升级权限。实现：`ngxops/rbac/permission_defs.py`、`ngxops/rbac/service.py`、`ngxops/security/dependencies.py`、`ngxops/nginx_install/routes.py`、`ngxops/upgrade/routes.py`、`ngxops/tasks/api.py`、`ngxops/ui.py`、迁移 v15 及相关文档。验证：静态核对权限门禁、导航、任务中心范围、迁移映射与 API 文档；未运行自动化测试或浏览器视觉验证。部署需先备份，再显式执行 `python -m ngxops.database upgrade` 应用 v15。
6. **已确认**：首页“新建节点”入口跳转到节点列表 `/nodes/`；用户可从列表页进入新增表单。入口通过 `request.url_for('list_nodes')` 反向解析，避免硬编码路径。实现：`templates/dashboard/index.html`、`ngxops/nodes/routes.py`。验证：静态核对首页入口路由名对应节点列表路由；未运行自动化测试或浏览器视觉验证。
7. **已确认**：节点组列表使用与原项目相近的紧凑管理表格，显示组名、成员数、描述、创建人和时间；点击成员数可跳转到该组节点列表。新增/编辑页收窄并居中，按原项目分为“基本信息”和“关联节点”两张紧凑卡片；名称、描述纵向排列，描述使用多行文本框。成员选择改为弹窗表格，主页面只显示已选主机/IP标签；弹窗支持主机名/IP搜索、当前结果全选、SSH/锁定状态及节点组上限提示。保留现有权限、最多 3 组约束和 `node_ids` 表单提交，无 API 或数据结构变化。与参考项目差异：节点选项由当前表单页预载，弹窗内客户端筛选，不新增原项目的节点搜索 API。实现：`templates/nodes/groups.html`、`templates/nodes/group_form.html`、`static/css/node-groups.css`、`static/js/node-groups.js`、`ngxops/nodes/routes.py`。验证：JavaScript 语法检查及 `git diff --check`；未运行自动化测试或浏览器视觉验证。
8. **已确认**：节点批量导入错误按单条原因聚合，避免重复显示相同节点组/凭证错误；单行问题保留实际 `row`，跨行聚合项设 `row=0`、`merged=true`，并将连续或非连续行号范围单独放在 `row_range`。导入结果弹窗只显示去重后的异常 `message`，不展示行号；API 行号元数据保留供其他调用方定位原表。整份工作簿仍先校验，任一问题都会拒绝整批写入。与参考实现一致采用去重汇总策略，同时保留机器可读的行号范围。实现：`ngxops/nodes/services.py`、`ngxops/nodes/routes.py`、`templates/nodes/list.html`、`docs/nodes.md`、`docs/api.md`。验证：静态检查错误聚合、JSON 模型和弹窗展示路径；未运行自动化测试或浏览器视觉验证。
9. **已确认**：节点导入弹窗关闭时清空错误提示、移除错误样式并重置文件选择；关闭前发出的导入请求若稍后才返回，其结果不会重新写入弹窗。实现：`templates/nodes/list.html`。验证：静态检查 Bootstrap 弹窗关闭事件、表单重置和请求序号门禁；未运行自动化测试或浏览器验证。
10. **已确认**：全站右上角提示统一通过公共 `showToast` 实现底部倒计时进度条；默认显示 3 秒后自动关闭，调用方传入自定义持续时间时进度同步变化，保留手动关闭按钮。DOM 和动画对齐 `mngxops`：`.toast-progress` 是提示项的直接子元素，自身宽度从 100% 收缩到 0；使用 4px 深色半透明进度线，减少动态效果模式下仍保留倒计时动画，不会固定为静态色块。涉及：`static/js/app.js`、`static/css/app.css`，各业务提示调用点无需修改；递增 `templates/base.html` 的静态资源版本参数，避免浏览器继续使用旧文件。验证：静态核对公共提示入口、计时器和动画时长使用同一值，并确认 CSS 资源 URL 已更新；未运行自动化测试或浏览器验证，待页面确认。
11. **已确认**：用户、角色、用户组入口分工明确；“用户组管理”不再跳到角色别名页。用户属于的组存在角色时，以这些组角色权限并集为准；否则回退到个人角色，直授权限独立叠加。关系继续分开保存，解除组角色关联会立即撤销成员继承权限并保留成员、个人角色关系，不做冗余个人角色删除。用户列表展示有效角色。与原 Django 个人角色优先规则不同，依据本项确认采用用户组优先。验证：`tests/test_rbac.py` 覆盖组角色优先、个人角色回退、直授权限、解绑后关系保留及用户列表有效角色回显。
12. **已确认**：用户个人角色、所属用户组和用户组关联角色均使用可搜索多选弹窗；取消不修改主表单选择，确认后更新表单值，最多 3 个个人角色的限制保留。实现：`templates/rbac/user_form.html`、`templates/rbac/team_form.html`、`templates/rbac/_entity_picker_modal.html`、`static/js/user-form.js`。验证：`tests/test_rbac.py` 检查三个弹窗、角色上限和关系字段；取消/确认过程尚未用浏览器自动化执行。
13. **已确认**：新增/编辑用户和用户组按参考项目使用紧凑分区卡片，左侧色条区分基本信息、角色、用户组和权限。实现：`templates/rbac/user_form.html`、`templates/rbac/team_form.html`、`static/css/user-form.css`、`static/css/team-form.css`。验证：`tests/test_rbac.py` 检查页面渲染、分区类名和样式资源；浏览器视觉验证未执行。
14. **已确认**：服务端分页列表统一复用分页页脚，展示总数、当前/总页数与每页条数；发布中心/历史、用户组成员弹窗同步展示总数、页数和每页批次。GET 列表的关键词输入由全局控件统一转换为可移除蓝色标签，并保留中英文逗号分词规则。成员弹窗的搜索标记和每页选择器直接由模板渲染，成员 API 接受 `per_page` 并返回总数/总页数；共享标签脚本排除每页表单中用于保留筛选条件的隐藏字段。实现：`templates/includes/pagination_footer.html`、`static/js/app.js`、`static/css/app.css`、`templates/rbac/teams_list.html`、发布页面脚本。验证：`tests/test_rbac.py` 检查用户/角色/用户组分页、发布页搜索控件、成员分页 API 和共享脚本；`node --check` 与 `git diff --check` 通过。浏览器视觉与动态交互验证未执行；全套 `pytest -q tests` 为 18 passed、4 failed，失败均在配置同步测试，测试替身 `fake_discovery` 未接收生产调用的 `max_depth` 参数，与本项 RBAC/UI 改动无关。
15. **已确认**：用户新建/编辑页的直授权限与角色权限统一使用资源/动作矩阵布局；提交字段和直授权限叠加规则保持不变。共享响应式样式位于 static/css/rbac-matrix.css，由 templates/rbac/user_form.html 和角色表单共同加载。验证：tests/test_rbac.py 检查用户表单矩阵和样式资源，并提交节点读取直授权限后确认权限解析生效。
16. **已确认**：角色新增/编辑页按“基本信息”“权限矩阵”分区为独立色条卡片，与原参考项目的角色表单分类一致；权限字段、校验、全选和保存行为不变。实现：templates/rbac/role_form.html、static/css/role-form.css、static/css/rbac-matrix.css。验证：tests/test_rbac.py 检查新增和编辑页卡片结构、样式资源及矩阵渲染。
17. **已确认**：移除展开子菜单左侧的竖向树形引导线；菜单层级缩进、悬停/选中标记、折叠浮层边框保持不变。实现：static/css/app.css，并将 templates/base.html 的 app.css 资源版本递增至 v13。验证：tests/test_nodes.py 检查共享样式不再声明 submenu::before；git diff --check 通过。
18. **已确认**：节点组名称以普通表格字重显示。移除“搜索节点组”字段标签和“筛选”按钮，保留带可访问名称的输入框与清空入口；按 Enter 提交 GET 查询，继续保留每页大小并重置到第一页。实现：templates/nodes/groups.html。验证：tests/test_nodes.py 检查渲染文案、名称单元格、Enter 提交处理和查询结果；节点专项测试 3 passed。
19. **已确认**：用户组“选择关联角色”弹窗增加客户端分页（每页 10/25/50 项）；输入文字不再即时过滤，按 Enter 后应用查询。多个关键词改为 AND 匹配，因此“Nginx”与“执行”只保留同时包含两个关键词的角色；已选关系跨页保留。查询输入字号调整为全站紧凑正文大小。角色数据仍由表单页预载，无 API、保存字段或权限规则变化。与原参考项目的内嵌复选框列表不同，保留已确认的自定义弹窗，并在弹窗内本地分页和筛选。实现：`templates/rbac/_entity_picker_modal.html`、`static/js/user-form.js`、`static/css/app.css`、用户/用户组表单静态资源版本。验证：`tests/test_rbac.py` 检查 12 个角色行、分页控件、Enter 查询、AND 条件、分页逻辑和紧凑字号；JavaScript 语法检查及 RBAC 专项测试通过。
```text
Nginx ×，执行 ×
输入名称或描述，按回车添加条件
匹配 4 项
已选 8 项
选择	角色	描述
Nginx 依赖包	源码包/模块包 上传、删除
Nginx 卸载执行	-
Nginx 启停	-
Nginx 安装/升级	执行
```
20.  **已确认**：登录页展示当前设置中的连续失败锁定次数和时长；达到阈值或锁定期间再次登录时，显示锁定状态及逐秒更新的剩余时间，到期提示可重试，也保留联系管理员提前解锁的入口说明。未知用户名和普通密码错误继续显示相同文案，不展示锁定前账户失败次数。锁定提示不会自动消失。实现：`ngxops/accounts/service.py`、`ngxops/accounts/routes.py`、`templates/accounts/login.html`、`static/css/accounts.css`、`docs/accounts.md`。验证：`tests/test_accounts_login.py` 覆盖默认和变更后的设置、第五次失败锁定、后续剩余时间及未知用户名错误文案；账户登录专项测试、Python 编译和 `git diff --check` 通过。

21.  **已确认**：用户列表、角色管理和用户组管理去掉独立搜索按钮，回车提交标签查询；列表重新加载后恢复查询框焦点，Backspace/Delete 可移除最后一个查询标签，仍可点标签上的移除按钮。实现：`static/js/app.js`、`templates/base.html`、`templates/rbac/users_list.html`、`templates/rbac/roles_list.html`、`templates/rbac/teams_list.html`。回归用例补充到 `tests/test_rbac.py`；未启动服务或进行浏览器验证。
22.  **已确认**：用户组管理成员弹窗去掉独立搜索按钮，回车将输入转为查询标签并搜索，焦点保持在查询框；标签支持 Backspace/Delete 和按钮移除。成员加载沿用服务端 `page/per_page/search` 分页、总数/总页数和 10/25/50/100 每页选项，并同步服务端回传的规范页码。实现：`static/js/app.js`、`templates/rbac/teams_list.html`、`docs/rbac.md`；回归用例补充到 `tests/test_rbac.py`。未启动服务或进行浏览器验证。
23.  **已完成**：移除节点列表标题栏中的节点组跳转入口，节点组仍可由侧栏进入。
24.  **已完成**：节点列表查询行移除“搜索、节点组、环境、SSH 状态”可见字段标题，保留辅助技术可读取的标签。
25.  **已完成**：节点列表搜索提示改为“搜索主机名或 IP”。
26.  **已完成**：节点列表搜索改为查询标签，回车提交、移除标签后自动查询并恢复搜索焦点；选择器变更自动提交，移除独立“筛选”按钮，清空条件使用图标按钮。保留节点组、环境和 SSH 状态筛选，以维持原项目实际筛选行为；分页和每页条数继续保留查询参数。
27.  **已完成**：新增/编辑节点表单拆分为基本信息、SSH 认证、Nginx 配置和节点组分区卡片；卡片宽度、左侧色条、浅灰标题栏、留白和操作栏样式对齐凭证表单。
28.  **已完成**：SSH 端口控件限制 1～65535，新增默认读取 `node.ssh_default_port`（默认 22）；新增 IP 默认 `127.0.0.1`。Nginx 可执行路径和主配置路径分别读取 `config.default_nginx_bin` 与 `config.default_nginx_path`，默认 `/usr/sbin/nginx` 与 `/etc/nginx/nginx.conf`，映射按系统设置定义保持一致。
29.  **已完成**：节点表单关联节点组弹窗支持查询标签、回车添加、Backspace/Delete 移除、焦点保持；查询按多个关键词 AND 匹配，并提供 10/25/50/100 条分页，跨页保留选择和最多 3 组限制。
30.  **已完成**：节点组列表使用查询标签，支持回车提交、删除标签后自动查询及搜索焦点恢复；移除独立搜索脚本，分页和每页条数保留查询条件。
31. **已完成**：新增节点组的“基本信息”和“关联节点”卡片应用凭证表单的分区样式，包括左侧色条、浅灰标题栏、统一留白和居中宽度；提高卡片规则优先级并为样式表加版本参数，避免色条被卡片边框规则覆盖或浏览器继续使用旧缓存。
32. **已完成**：节点组关联节点弹窗支持查询标签、回车添加、Backspace/Delete 移除、焦点保持和多关键词 AND 匹配；提供 10/25/50/100 条分页，跨页保留已选节点及每节点最多 3 个节点组限制。选择、搜索和分页在已加载的活动节点中完成。
33. **已完成**：节点导入的活跃节点冲突和工作簿内重复错误均显示冲突 IP，并保留原有按 Excel 行号归并方式。
34. **已完成**：编辑节点返回列表后使用全局自动消失提示显示更新主机名，并清理一次性查询参数。
35. **已完成**：节点表单的节点组选择改用用户编辑页共用的实体选择弹窗和脚本，保留最多选择 3 个节点组及所选项摘要。
36. **已完成**：单条删除使用全局确认弹窗调用批量删除 API，不进入独立确认页；成功后仍留在当前列表地址刷新，并通过 sessionStorage 在刷新后显示全局自动消失提示。批量锁定/删除成功提示也在刷新后显示。
37. **已完成**：节点批量删除按钮移至操作栏右侧，对齐凭证列表的删除操作布局。
38. **已完成**：节点导入审计记录增加主机名和 IP 资产明细，不记录凭证；明细最多 4000 字符，超出时标注未列出数量。
```text
2026-10-08 13:55:36	wangtianci	节点管理	导入节点	127.0.0.1	成功
批量导入成功：新建 24 台
```
39. **已完成**：节点列表和配置同步达到系统设置上限后禁用其余可选项，全选只补足至上限；升级、安装页面读取动态上限，不再写死为 3。发布、启停和卸载前端已使用动态上限；探测、锁定/解锁/删除、凭证测试、配置同步、发布、升级、安装、启停和卸载接口均按当前系统设置复核节点数。刷新页面读取最新设置。
40. **已完成**：凭证管理去掉“筛选”按钮，认证方式和启用状态变更时自动提交查询。
41. **已完成**：凭证名称/SSH 用户搜索使用回车提交的查询标签；多个关键词按 AND 匹配，移除标签、翻页和每页条数变化均保留当前查询条件。
42. **已完成**：凭证管理在标题栏下直接显示查询控件和数据列表，不为查询单独增加卡片或标题行。
43. **已完成**：将凭证勾选列固定为 40px，收紧复选框左右留白并为名称列设置最小宽度。
44. **已完成**：单条和批量删除使用全局 `submitPostConfirm` 确认弹窗；公共方法支持重复表单字段。批量删除先校验所有 ID，再单事务删除并写汇总审计。
45. **已完成**：凭证列表“更新时间”将 UTC 存储值转换为北京时间（UTC+8）展示。
46. **已完成**：凭证管理，新增凭证 SSH 认证信息，采用分区卡片。
47. **已完成**：凭证批量导入拒绝工作簿内重名及与当前用户已有凭证重名；任一行重复均整批拒绝，不再静默覆盖已有凭证。导入弹窗只显示去重后的具体错误，不显示行号；API 保留原表行号元数据。
```text
凭证名称已存在，不允许重复导入
```
48. **已完成**：凭证批量删除在审计抑制范围内 flush，仅保留汇总记录；审计详情改为在原单元格下方向下展开，固定列宽不随展开变化。`git diff --check` 通过；未运行自动化测试或启动服务。
修复前日志示例：
```text
2026-10-08 10:54:07	wangtianci	凭证管理	删除凭证管理	127.0.0.1	成功	删除「sdfsdf」
2026-10-08 10:54:07	wangtianci	凭证管理	删除凭证管理	127.0.0.1	成功	删除「123」
2026-10-08 10:54:07	wangtianci	凭证管理	删除凭证管理	127.0.0.1	成功	删除「hyj」
2026-10-08 10:54:07	wangtianci	凭证管理	删除凭证管理	127.0.0.1	成功	删除「cmas-backend」
2026-10-08 10:54:07	wangtianci	凭证管理	删除凭证管理	127.0.0.1	成功	删除「zxdg-root」
2026-10-08 10:54:07	wangtianci	凭证管理	删除凭证管理	127.0.0.1	成功	删除「ldys-umpay-56」
2026-10-08 10:54:07	wangtianci	凭证管理	删除凭证管理	127.0.0.1	成功	删除「ldys-umpay-66」
2026-10-08 10:54:07	wangtianci	凭证管理	删除凭证管理	127.0.0.1	成功	删除「kylin-root」
2026-10-08 10:54:07	wangtianci	凭证管理	删除凭证管理	127.0.0.1	成功	删除「cmas-mas」
2026-10-08 10:54:07	wangtianci	凭证管理	删除凭证管理	127.0.0.1	成功	删除「ldys-root」
2026-10-08 10:54:07	wangtianci	凭证管理	批量删除凭证	127.0.0.1	成功	批量删除 10 条凭证：ldys-root、cmas-mas、kylin-root、ldys-umpay-66、ldys-umpay-56、zxdg-root、...
```
49. **已完成**：持有 `credentials.create` 的普通用户可从凭证列表批量导入；导出仍限超管，批量删除继续要求独立的 `credentials.delete`，不会由导入权限带出。与参考项目仅管理员可导入的规则不同，按本项用户确认开放创建权限。实现：`ngxops/credentials/routes.py`、`templates/credentials/list.html`、`docs/credentials.md`、`docs/api.md`。未运行自动化测试或启动服务。
50. **已完成**：关闭凭证导入弹窗时清空错误、重置文件；关闭期间使未完成请求失效并中止，重新打开不会显示上次导入异常。
51. **已完成**：工作簿内重复提示具体凭证名称；与当前账号已有项重名提示名称及冲突来源。认证方式等字段错误说明具体字段和允许值，不回显密码/私钥；页面只展示去重后的异常信息，不显示行号。实现：`ngxops/credentials/workbooks.py`、`ngxops/credentials/routes.py`、`templates/credentials/list.html`、`docs/credentials.md`、`docs/api.md`。未运行自动化测试或启动服务。
52. **已完成**：节点组新增/编辑的成员节点选择弹窗显示客户端分页、记录数和每页 10/25/50/100 条选项，跨页保留勾选；查询按全局标签控件处理，输入过程中不筛选，回车提交标签后再查询，删除标签后更新结果，多关键词按 AND 匹配。弹窗改为可滚动布局，并为全局脚本和节点组脚本递增静态版本参数，避免旧缓存导致标签和分页逻辑不生效。实现：`templates/base.html`、`templates/nodes/group_form.html`、`static/js/node-groups.js`。未启动服务或运行浏览器测试。
53. **已完成**：节点探测任务触发时补齐 `request_client_ip` 导入，修复节点列表主机详情及“测试连接”请求在任务入队时报 `NameError`。实现：`ngxops/nodes/routes.py`。已通过 Python 3.9 语法解析和导入项静态核对；未连接真实 SSH 节点。
54. **已完成**：配置同步任务对每个节点只建立一条 SSH 连接，并复用于配置发现和待删除远程文件清理；Nginx 全新安装后的自动配置同步继续复用安装阶段的连接。SSH transport 每 30 秒发送 keepalive，降低长时间编译期间连接因空闲而断开的概率；连接由创建方在任务结束时关闭，独立发现/清理调用仍自行管理连接。实现：`ngxops/credentials/tasks.py`、`ngxops/configs/discovery.py`、`ngxops/configs/tasks.py`、`ngxops/upgrade/services.py`、`ngxops/nginx_install/services.py`、`docs/configs.md`、`docs/nginx-install.md`。未连接真实 SSH 节点。
55. **已完成**：本节新增全局交互规范，统一右上角限时提示和自定义确认/提示弹窗、查询标签及焦点/删除行为、关键词与筛选条件 AND 关系、查询区域布局、分页条件保留、弹窗跨页选择和静态资源版本更新要求。
56. **已完成**：节点批量锁定会将 SSH 状态写为离线；批量解锁清除锁定后自动创建持久化 SSH/Nginx 探测任务，完成后更新 SSH 状态、探测时间和 Nginx 版本。无凭证时状态保持未知，凭证不可用时记为离线；探测任务可由 `nodes.unlock` 用户本人查看。实现：`ngxops/nodes/routes.py`、`ngxops/nodes/tasks.py`、`ngxops/tasks/api.py`、节点列表脚本。未连接真实 SSH 节点。
57. **已完成**：节点列表中的 SSH 探测、系统信息采集、Nginx 检测及解锁探测完成提示提供“查看完整日志”链接，指向对应任务详情；公共 toast 支持安全文本链接并在悬停时暂停关闭计时。实现：`static/js/app.js`、`static/css/app.css`、`static/js/nodes.js`。未启动服务或进行浏览器验证。
58. **已完成**：包含结构化节点列表的任务详情使用逐节点紧凑清单，显示主机、状态和摘要，逐项完整结果按需展开；日志首屏限制 50 条，通过加载更多查看剩余日志，适合多节点任务扫描。实现：`ngxops/tasks/routes.py`、`templates/tasks/detail.html`、`static/js/task-center.js`、`static/css/task-center.css`、`docs/tasks.md`。未启动服务或进行浏览器验证。
59. **已完成**：任务详情默认显示前三个目标节点；其余目标通过展开/收起控件查看，保留完整目标数。实现：`ngxops/tasks/routes.py`、`templates/tasks/detail.html`、`static/js/task-center.js`。未启动服务或进行浏览器验证。
60. **已完成**：节点资料修改新增“节点调整”审计动作，记录节点标识和变更字段名，不写任意备注内容或变更前后值；节点任务审计增加任务说明和目标主机/IP 摘要，日志行可跳转对应任务详情。实现：`ngxops/audit/service.py`、`templates/audit/list.html`（复用既有任务链接）、`docs/audit.md`。遵循审计日志不可编辑规则。
61. **已完成**：任务中心移除放大镜查询按钮并改用全局 `data-query-tags` 输入；回车提交、标签移除/退格、焦点恢复由公共脚本处理。筛选下拉在提交时保留未提交关键词、AND 组合和每页条数，查询布局与公共控件一致。实现：`templates/tasks/center.html`、`static/js/task-center.js`、`static/css/task-center.css`。未启动服务或进行浏览器验证。
62. **已完成**：节点列表和详情弹窗的 SSH 探测时间统一按 CST（Asia/Shanghai，UTC+8）显示；SQLite 中的 UTC naive 时间未改变，详情 API 返回带时区偏移的北京时间。实现：`ngxops/nodes/routes.py`、`templates/nodes/list.html`、`static/js/nodes.js`、`docs/nodes.md`。未启动服务或进行浏览器验证。
63. **已完成**：节点列表批量锁定时，若所选节点均已锁定则在右上角显示“所选节点已锁定，无需再次锁定”警告；混合选择时只锁定未锁定节点，并以警告提示跳过数量。实现：`ngxops/nodes/routes.py`、`templates/nodes/list.html`、`static/js/nodes.js`、`docs/nodes.md`。未启动服务或进行浏览器验证。
64. **已完成**：节点 SSH 探测、系统信息、Nginx 检测及解锁探测在任务创建后立即显示带“查看完整日志”的提示，完成后再次提示执行结果。实现：`static/js/nodes.js`。未启动服务或进行浏览器验证。
65. **已完成**：任务详情节点结果摘要单行显示，超过 80 字符时省略；点击摘要展开完整结果，浮层不会撑开表格行，轮询更新时保留展开状态。实现：`static/js/task-center.js`、`static/css/task-center.css`。未启动服务或进行浏览器验证。
66. **已完成**：任务详情初始和增量日志将目标 IP 映射为“主机名 (IP)”（如“节点 1/3 app-01 (10.10.77.102)：SSH 成功”），隐藏日志级别。实现：`ngxops/tasks/routes.py`、`templates/tasks/detail.html`、`static/js/task-center.js`、`static/css/task-center.css`。未启动服务或进行浏览器验证。
67. **已完成**：凭证关联节点数量可打开分页弹窗，展示主机名、IP、SSH 状态、Nginx 版本和北京时间探测时间；新增 `GET /api/credentials/{credential_id}/nodes`，要求 `credentials.read`。实现：`ngxops/credentials/routes.py`、`templates/credentials/list.html`、`docs/credentials.md`、`docs/api.md`。未启动服务或进行浏览器验证。
68. **已完成**：节点列表探测时间列标题改为“探测时间”，时间仍按北京时间展示。实现：`templates/nodes/list.html`、`docs/nodes.md`。
69. **已完成**：凭证启用测试在任务创建后立即显示带“查看完整日志”的提示，任务结束后显示测试结果摘要；禁用操作不创建 SSH 任务。实现：`templates/credentials/list.html`、`docs/credentials.md`。未启动服务或进行浏览器验证。
70. **已完成**：操作日志关联任务显示可点击的 `#任务ID`，直接跳转统一任务详情。实现：`templates/audit/list.html`、`docs/audit.md`。未启动服务或进行浏览器验证。
71. **已完成**：凭证关联节点数量采用与节点组“成员节点”一致的字号和链接强调样式。实现：`templates/credentials/list.html`、`static/css/credentials.css`、`docs/credentials.md`。
72. **已完成**：凭证列表“最近测试”列仅保留测试结果，不显示测试时间。实现：`templates/credentials/list.html`、`docs/credentials.md`。
73. **已完成**：凭证启用测试审计摘要改为“创建任务：#ID 目标…”，任务 ID 在 flush 后正确关联，避免重复输出内部操作类型和凭证 ID；详情中的 `#ID` 在新标签页跳转任务详情。实现：`ngxops/audit/service.py`、`ngxops/audit/routes.py`、`templates/audit/list.html`、`docs/audit.md`。未启动服务或进行浏览器验证。
74. **已完成**：凭证启用/禁用写入明确审计动作和明细，分别显示“启用凭证「名称」”与“锁定凭证「名称」”，不再生成通用的凭证更新记录。实现：`ngxops/credentials/routes.py`、`docs/credentials.md`。
```text
2026-10-08 16:44:17	wangtianci	凭证管理	更新凭证管理	127.0.0.1	成功	 更新「zxdg-root」
```
75. **已完成**：节点 SSH 任务及凭证关联节点测试的“查看完整日志”链接在新标签页打开，相关任务 toast 调整为 3 秒。实现：`static/js/app.js`、`static/js/nodes.js`、`templates/credentials/list.html`、`docs/nodes.md`、`docs/credentials.md`。未启动服务或进行浏览器验证。
76. **已完成**：节点列表状态筛选项明确为“全部 SSH 状态”；筛选仍对应 SSH 状态，Nginx 状态独立显示。实现：`templates/nodes/list.html`、`docs/nodes.md`。
77. **已完成**：节点 SSH 探测和凭证关联节点测试在实际连接成功或失败后都会更新 `last_probe_at`，失败探测不再显示“未探测”；锁定跳过及未配置/不可用凭证等未发起 SSH 连接的情况不更新探测时间。文档说明 `unknown` 表示当前没有可用探测结论及其常见触发场景，包括新建/恢复节点、解锁或凭证启用后的异步探测，以及解锁探测时没有配置凭证。实现：`ngxops/nodes/tasks.py`、`ngxops/credentials/tasks.py`、`docs/nodes.md`。
78. **已答复**：建议保留 SSH“未知”状态，用于区分尚无有效探测结论与实际连接失败的“离线”；新建/恢复节点、解锁或重新启用凭证后的异步探测期间都需要该状态。
79. **已完成**：凭证管理关联节点数量按钮移除跳转箭头，保留自定义弹窗提示。实现：`templates/credentials/list.html`、`docs/credentials.md`。
80. **已完成**：关联节点弹窗加入公共查询标签、焦点恢复和条件清空行为，支持按主机名/IP/节点组搜索及 SSH/Nginx 状态筛选，关键词和筛选条件按 AND 组合；分页保留条件并支持 10/25/50/100 条。弹窗为只读列表，没有跨页勾选操作；查询逻辑位于模板内联脚本，未修改静态资源，无需递增静态版本。实现：`ngxops/credentials/routes.py`、`templates/credentials/list.html`、`docs/credentials.md`、`docs/api.md`。
81. **已完成**：凭证关联节点弹窗移除 SSH/Nginx 状态和节点组筛选，只按主机名/IP搜索，多个关键词按 AND 匹配；查询行不显示 X 清空按钮。分页调整为凭证列表标准，显示总数、当前/总页数、首页/上一页/下一页/末页及每页 10/25/50/100 条，并保留查询条件。接口改为 `page/per_page`。实现：`ngxops/credentials/routes.py`、`templates/credentials/list.html`、`docs/credentials.md`、`docs/api.md`。未运行自动化测试或浏览器验证。
82. **已完成**：配置标签删除沿用全局确认弹窗，删除后将一次性消息交给全局右上角 `showToast`，不再在列表顶部渲染提示条。实现：`ngxops/configs/routes.py`、`templates/configs/list.html`、`static/js/config-list.js`。验证：配置删除提示专项用例通过。
83. **已完成**：移除配置列表的节点组筛选框和独立搜索按钮；搜索改为全局查询标签，主机名、IP、配置名、远程路径关键词按 AND 匹配，分页保留条件并恢复搜索焦点。实现：`ngxops/configs/routes.py`、`templates/configs/list.html`、`static/js/config-list.js`。验证：配置列表组合搜索用例通过。
84. **已完成**：合并“手动添加”和列表级“创建绑定”为单一新增配置表单，可填写标签、远程路径和内容，目标节点允许 0 个或多个；节点选择按主机名/IP/节点组多词 AND 搜索，支持查询标签、10/25/50/100 分页和跨页保留选择。创建标签、绑定及 v1 快照在同一事务提交，已有标签仍可从详情页添加绑定。实现：`ngxops/configs/routes.py`、`templates/configs/form.html`、`static/js/config-create.js`。验证：零绑定/多节点绑定及版本快照用例通过；无数据库迁移。
85. **已完成**：配置列表增加 Nginx 识别状态开关，默认只显示已识别节点，可切换全部未锁定活动节点；开关保留查询和绑定状态条件。实现：`templates/configs/list.html`、`static/js/config-list.js`、`ngxops/configs/routes.py`。验证：默认开关和筛选查询用例通过。
86. **已完成**：提高配置列表“绑定状态”筛选栏的文字和未选中状态对比度，状态筛选在列表顶部清晰显示。实现：`templates/configs/list.html`。验证：页面渲染断言确认状态栏和各筛选项存在。
87. **已完成**：同步列表搜索仅保留主机名/IP 全局查询标签，多个关键词按 AND 匹配并跨字段组合；移除节点组/Nginx 筛选项和筛选按钮，默认固定显示已识别 Nginx 节点，公共分页保留搜索条件。实现：`ngxops/configs/routes.py`、`templates/configs/sync_wizard.html`。验证：同步向导筛选行为和页面文案用例通过。
88. **已完成**：移除批量任务日志面板及“可同步节点 x 个”提示，批量任务状态仍显示实际执行进度和结果摘要；单节点任务日志保留。实现：`templates/configs/sync_wizard.html`、`static/js/config-sync.js`。验证：同步页面断言确认批量日志和总数提示已移除。
89. **已完成**：批量同步按钮改名为“全量同步”，移至节点选择状态栏右侧，与选择数量和全选操作分区对齐。实现：`templates/configs/sync_wizard.html`。验证：同步页面按钮文案断言通过。
90. **已完成**：配置列表查询栏移除重复的每页数量选择器，保留当前 `per_page` 隐藏参数；每页数量继续由表格下方分页栏调整，搜索时保留当前页大小。实现：`templates/configs/list.html`。未运行自动化测试或浏览器验证。
91. **已完成**：对齐新增配置页的节点选择按钮；搜索回车应用，调整查询/标签/每页/分页布局，支持鼠标点击表行及键盘勾选。验证：配置新增表单断言和配置同步专项回归通过。
92. **已完成**：配置主列表移除绑定明细，只保留节点统计；点击节点进入独立绑定列表，保留筛选返回和绑定操作。验证：节点列表不泄露配置名称，节点明细页可搜索并展示绑定。
93. **已完成**：配置发现及同步读取节点资产中的 Nginx 主配置路径；弹窗显式编辑会写回节点资产，批量同步使用同一字段。旧 `ConfigSyncSetting` 数据不再覆盖。验证：设置自定义路径后，向导及无路径 API 请求均传入该路径。
94. **已完成**：发现结果展示主配置及 include 子配置路径，支持全选、多选部分同步和单文件部分同步，也保留节点全量/跨节点批量同步。验证：隔离 SSH 测试发现两条子配置并完成单条、多条同步。
95. **已完成**：跨节点操作按钮命名为“批量同步”；单节点/批量任务创建与完成均使用全局 toast，并提供新标签页打开任务详情日志的入口。任务终态后刷新当前列表 URL，保留筛选分页，并通过一次性 sessionStorage 提示显示结果。实现：`templates/configs/sync_wizard.html`、`static/js/config-sync.js`。验证：配置同步专项测试、JavaScript 语法检查；未连接真实 SSH 或进行浏览器视觉验证。
96. **已完成**：全选、已选数量、节点上限和批量按钮改用统一垂直居中的响应式状态栏，修正节点配置同步工具行上下错位。实现：`templates/configs/sync_wizard.html`。验证：同步向导页面渲染断言、模板语法检查；未进行浏览器视觉验证。
```text
全选可同步节点
未选择节点
最多选择 2 个节点
```
97. **已完成**：单节点/批量任务进入终态后刷新当前同步列表，更新绑定统计和最近同步时间；完成 toast 跨刷新保留任务摘要及完整日志链接。实现：`static/js/config-sync.js`。验证：静态检查会话提示保存/恢复与保留当前 URL；未进行浏览器自动化验证。
98. **已完成**：最近同步时间由服务端把数据库 UTC 时间转换为 UTC+8 后格式化显示，不改写数据库值。实现：`ngxops/configs/routes.py`、`templates/configs/sync_wizard.html`。验证：同步向导页面时区用例通过。
99. **已完成**：发现文件标题、全选和发现状态使用三列网格对齐，并重置全选复选框的 Bootstrap 默认浮动/外边距，确保“发现 1 个配置文件”与按钮垂直居中；窄屏时状态换行并靠右。实现：`templates/configs/sync_wizard.html`。验证：配置同步专项测试 13 passed（含页面结构断言）；未进行浏览器视觉验证。
```text
发现的远程文件
全选
发现 1 个配置文件
```
100. **已完成**：保持节点粒度任务和 `node.batch_max_count` 并发上限；单节点多配置及多节点多配置均由每节点单条 SSH 会话完成发现/读取/清理。批量进度百分比按已完成节点数计算，detail 限频更新每个活动节点；持久化日志逐步记录 SSH 连接、配置发现、逐文件新增/更新/跳过、缺失检查、待删除清理和节点摘要，并包含主机名/IP。任务中心的配置同步详情页实时轮询新增日志，以绿色终端展示并自动跟随最新日志；远程配置发现与同步页不显示动态执行步骤。发现路径补全兼容带主机信息的日志。实现：`ngxops/configs/tasks.py`、`static/js/config-sync.js`、`static/js/task-center.js`、`static/css/task-center.css`、`templates/configs/sync_wizard.html`、`templates/tasks/detail.html`。验证：配置同步专项测试 13 passed，JS/Python 语法和 `git diff --check` 通过；未连接真实 SSH 或进行浏览器视觉验证。
101. **已完成**：多主机配置同步任务详情的执行日志增加“同步主机”下拉筛选；按主机名/IP 定位日志，选择主机后隐藏其他节点及通用日志，并自动补载未显示的日志页；单主机任务不显示筛选控件。实现：`templates/tasks/detail.html`、`static/js/task-center.js`、`static/css/task-center.css`。验证：配置同步专项测试覆盖单/多主机筛选器渲染；JS 语法检查和 `git diff --check` 通过。
102. **已完成**：发布中心移除节点组、环境和 SSH 状态筛选；不影响节点表格中的状态信息及 API 的兼容筛选参数。实现：`templates/releases/center.html`、`static/js/releases.js`。
103. **已完成**：发布中心移除独立搜索按钮，回车提交全局查询标签；状态、Nginx 开关和分页操作通过异步请求刷新，保留已选配置。实现：`templates/releases/center.html`、`static/js/releases.js`。
104. **已完成**：发布查询使用配置管理相同的逗号查询标签和 AND 关键词；绑定状态栏及清空交互沿用配置列表样式；Nginx 开关默认仅显示已识别节点。节点分页默认 10 台，支持 10/25/50/100，URL 保留查询状态。实现：`ngxops/releases/routes.py`、`ngxops/releases/api.py`、`templates/releases/center.html`、`static/js/releases.js`。
105. **已完成**：发布中心“绑定状态”筛选改用配置管理的紧凑标签、状态色和标题字重，筛选项保留当前搜索、Nginx 状态和每页数。实现：`templates/releases/center.html`。
106. **已完成**：节点列表继续按节点分页；展开节点后绑定表按配置名服务端分页，默认 10 项并支持 10/25/50/100，显示总数/页码，避免大量标签一次性载入页面。节点级全选跨页选择全部可发布绑定并遵守 500 项批次上限。绑定 API 响应新增 `page/page_size/total/total_pages/selectable_total`，显式 JOIN 配置表并用 `contains_eager` 加载配置关系，确保 SQLite 按配置名排序可用。实现：`ngxops/releases/api.py`、`static/js/releases.js`、`templates/releases/center.html`、`docs/releases.md`、`docs/api.md`。无数据库迁移。
107. **已完成**：核对并确认发布/回滚执行器每节点只建立一条 SSH 会话，同节点逐配置执行备份、上传/删除与 `nginx -t`，全部通过后只 reload/start 一次；`_reload_nginx` 优先识别 systemd 活跃/已启用单元，否则检查进程并使用节点 Nginx 二进制操作。新增任务日志中的主机名/IP 标识，方便多主机筛选。实现：`ngxops/releases/services.py`、`docs/releases.md`。执行器既有行为，无数据库迁移。
108. **已完成**：发布和回滚任务详情复用配置批量同步的终端式增量日志、自动跟随最新日志和多主机筛选；新日志包含主机名/IP，旧日志仍支持按主机名定位。实现：`templates/tasks/detail.html`、`static/js/task-center.js`、`ngxops/releases/services.py`。静态验证：Python 3.9 `py_compile` 4 个模块、Node 检查 2 个脚本及 `git diff --check` 通过；未运行服务、pytest 或浏览器验证。
109. **已完成**：发布中心的“仅显示已识别 Nginx”计数与配置管理统一按未软删除且未锁定节点统计，解决锁定节点造成的分母差异；发布列表仍保留锁定节点以展示其不可发布状态。实现：`ngxops/releases/routes.py`、`docs/releases.md`、`docs/configs.md`。
110. **已完成**：移除发布中心查询栏的清空按钮，搜索词可从查询标签逐个移除；绑定状态用“全部”复位，Nginx 开关保留原切换。实现：`templates/releases/center.html`、`static/js/releases.js`。
111. **已完成**：修复公共查询标签控件的 `getQueryTagValue` 对表单标签只读到当前输入、读不到已提交标签的问题；发布页主机名/IP/配置名/路径搜索现可异步生效，多词仍按 AND 过滤节点。搜索命中配置名/路径时自动展开节点并分页显示命中绑定；仅主机名/IP 命中时展开显示全部绑定。节点级全选继续请求未过滤绑定，仍覆盖全部可发布项。实现：`static/js/app.js`、`templates/base.html`、`static/js/releases.js`、`ngxops/releases/api.py`、`docs/releases.md`、`docs/api.md`。
112. **已完成**：绑定明细表改用固定布局和显式列宽，缩窄绑定状态/同步版本列并为发布版本选择器保留 176px，长配置名和路径可换行。实现：`templates/releases/center.html`、`static/js/releases.js`。静态验证：Python 3.9 编译 2 个模块、Node 检查 2 个脚本、Jinja 编译 2 个模板和 `git diff --check` 通过；未启动服务、运行 pytest 或做浏览器验证。
