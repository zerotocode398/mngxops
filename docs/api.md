# JSON API 约定

## 基本约定

- 页面路由使用 Jinja2；返回 JSON 的接口统一使用 `/api/` 前缀。`GET /health` 是公开的 JSON 探活接口，不检查数据库或远程节点。
- OpenAPI 文档由应用路由和 Pydantic 模型生成，可从 `/openapi.json`、`/docs` 和 `/redoc` 查看。
- 成功响应使用端点声明的响应模型和 HTTP 状态码。错误响应使用 `ApiError`，不会将 Python 异常文本或请求中的原始字段值写入响应。
- 日期时间使用 ISO 8601 格式；枚举值及字段名保持小写蛇形命名。

## 错误响应

```json
{
  "success": false,
  "message": "请求参数无效",
  "errors": [
    {"field": "query.page", "message": "数值超出允许范围"}
  ]
}
```

`success` 和 `message` 始终存在。`redirect` 仅用于登录失效并指向登录页；`errors` 仅用于 422 参数校验，包含字段路径和提示，不包含用户提交值。未知状态下的额外字段不属于稳定协议。

| 状态码 | 含义 |
|---|---|
| 400 | 请求格式无效，或当前业务条件不允许操作 |
| 401 | 登录态缺失、失效或用户已停用 |
| 403 | 权限或 CSRF 校验失败 |
| 404 | 资源不存在，或为避免泄露而隐藏当前用户不可见资源 |
| 409 | 操作期间资源状态发生并发变化 |
| 422 | 路径、查询或请求体参数校验失败 |
| 500 | 未预期的服务器错误；响应不包含异常详情 |

HTML 页面继续使用重定向、Jinja2 错误页或页面提示，不受 JSON 错误结构约束。

## 认证与 CSRF

- 受保护 API 使用服务端会话 Cookie `sessionid`。OpenAPI 以 `SessionCookie` 安全方案标注；Cookie 是随机会话句柄，不是可独立校验的 API 密钥。
- 权限由同一 RBAC 依赖检查。无权访问返回 403；对不应暴露存在性的资源，端点可将无权与不存在统一为 404。
- 所有非安全方法还需通过应用全局 CSRF 校验。浏览器提交签名 `csrftoken` Cookie，并在 `X-CSRFToken` 请求头提交相同令牌；同时校验 `Origin` 或 `Referer`。
- OpenAPI 在写请求上标注 `CsrfCookie` 和必需的 `X-CSRFToken` 请求头。页面 URL 编码表单使用 `csrf_token` 隐藏字段。

## 已登记接口

| 方法与路径 | 认证 | 成功模型 | 主要错误 |
|---|---|---|---|
| `GET /health` | 无 | `HealthResponse` | 500 |
| `GET /api/stats/` | 会话 | `DashboardStatsResponse` | 401、500 |
| `GET /api/tasks` | 会话 | `TaskListResponse` | 401、403、422、500 |
| `GET /api/tasks/{task_id}` | 会话 | `TaskDetailResponse` | 401、403、404、422、500 |
| `POST /api/tasks/{task_id}/cancel` | 会话与 CSRF | `TaskCancelResponse` | 400、401、403、404、409、422、500 |
| `GET /api/users/teams/{team_id}/members` | 超级管理员会话 | `TeamMembersResponse` | 401、403、404、422、500 |
| `POST /api/users/teams/{team_id}/members` | 超级管理员会话与 CSRF | `TeamMemberUpdateResponse` | 400、401、403、404、422、500 |
| `GET /api/credentials` | `credentials.read` 会话 | `CredentialListResponse` | 401、403、422、500 |
| `GET /api/credentials/{credential_id}/nodes` | `credentials.read` 会话 | `CredentialRelatedNodeListResponse`；支持逗号分隔的主机名/IP/节点组 `search`、SSH `status`、Nginx `nginx_status` 及 `page/page_size`，所有关键词和筛选条件按 AND 组合 | 401、403、404、422、500 |
| `GET /api/credentials/{credential_id}/secret` | `credentials.read` 会话 | `CredentialSecretResponse` | 401、403、404、422、500 |
| `POST /api/credentials/{credential_id}/toggle-enable` | `credentials.enable` 会话与 CSRF | `CredentialToggleResponse` | 401、403、404、422、500、503 |
| `GET /api/credentials/{credential_id}/enable-progress` | `credentials.read` 会话 | `CredentialEnableProgressResponse` | 401、403、404、422、500 |
| `GET /api/credentials/import-template` | `credentials.create` 会话 | xlsx 文件 | 401、403、500 |
| `POST /api/credentials/import` | `credentials.create` 会话与 CSRF | `CredentialImportResponse` | 400、401、403、413、422、500 |
| `GET /api/credentials/export` | 超级管理员会话 | 含明文认证材料的 xlsx 文件 | 401、403、404、422、500 |
| `GET /api/nodes` | `nodes.read` 会话 | `NodeListResponse` | 401、403、422、500 |
| `GET /api/nodes/{node_id}` | `nodes.read` 会话 | `NodeDetailResponse` | 401、403、404、422、500 |
| `GET /api/nodes/groups` | `nodes.read` 会话 | `NodeGroupListResponse` | 401、403、422、500 |
| `POST /api/nodes/batch-delete` | `nodes.delete` 会话与 CSRF | `NodeOperationResponse` | 400、401、403、404、409、422、500 |
| `POST /api/nodes/lock` | `nodes.lock` 或 `nodes.unlock` 会话与 CSRF | `NodeOperationResponse`；解锁返回探测任务 `task_id` | 400、401、403、404、422、500、503 |
| `POST /api/nodes/{node_id}/probe` | `nodes.ssh_test` 会话与 CSRF | `NodeTaskCreatedResponse`（202） | 400、401、403、404、422、500、503 |
| `POST /api/nodes/probe` | `nodes.ssh_test` 会话与 CSRF | `NodeTaskCreatedResponse`（202） | 401、403、404、422、500、503 |
| `POST /api/nodes/{node_id}/system-info` | `nodes.ssh_test` 会话与 CSRF | `NodeTaskCreatedResponse`（202） | 400、401、403、404、422、500、503 |
| `POST /api/nodes/{node_id}/nginx-probe` | `nodes.ssh_test` 会话与 CSRF | `NodeTaskCreatedResponse`（202） | 400、401、403、404、422、500、503 |
| `GET /api/nodes/import-template` | `nodes.create` 会话 | xlsx 文件 | 401、403、500 |
| `POST /api/nodes/import` | `nodes.create` 会话与 CSRF | `NodeImportResponse` | 400、401、403、409、413、422、500 |
| `GET /api/nodes/export` | `nodes.read` 会话 | xlsx 文件 | 401、403、422、500 |
| `POST /api/configs/discover` | `configs.sync` 会话与 CSRF | `ConfigTaskCreatedResponse`（202） | 400、401、403、404、422、500、503 |
| `POST /api/configs/sync` | `configs.sync` 会话与 CSRF | `ConfigTaskCreatedResponse`（202） | 400、401、403、404、422、500、503 |
| `POST /api/configs/sync/batch` | `configs.sync` 会话与 CSRF | `ConfigTaskCreatedResponse`（202） | 400、401、403、404、422、500、503 |
| `GET /api/nginx/uninstall/nodes` | `nginx_uninstall.read` 会话 | `UninstallNodesResponse` | 401、403、422、500 |
| `POST /api/nginx/uninstall/preview` | `nginx_uninstall.execute` 会话与 CSRF | `PreviewResponse` | 400、401、403、422、500 |
| `POST /api/nginx/uninstall/tasks` | `nginx_uninstall.execute` 会话与 CSRF | `UninstallCreateResponse`（202） | 400、401、403、409、422、500 |
| `GET /api/nginx/uninstall/batches/{batch_number}` | 卸载查看权限或本人执行权限 | `UninstallBatchResponse` | 401、403、404、422、500 |
| `GET /api/nginx/uninstall/tasks/{task_id}` | 卸载查看权限或本人执行权限 | `UninstallTaskDetailResponse` | 401、403、404、422、500 |
| `GET /api/releases/nodes` | `releases.read` 或 `releases.publish` 会话 | `ReleaseNodeListResponse` | 401、403、422、500 |
| `GET /api/releases/nodes/{node_id}/bindings` | `releases.read` 或 `releases.publish` 会话 | `ReleaseBindingsResponse` | 401、403、404、422、500 |
| `GET /api/releases/versions/{version_id}` | `releases.read` 或 `releases.publish` 会话 | `ReleaseVersionContentResponse` | 401、403、404、422、500 |
| `POST /api/releases/publish` | `releases.publish` 会话与 CSRF | `PublishCreatedResponse`（202） | 400、401、403、404、409、422、500、503 |
| `GET /api/releases/history` | `releases.read` 会话 | `ReleaseHistoryResponse` | 401、403、422、500 |
| `GET /api/releases/history/{task_id}/bindings/{binding_id}/versions` | `releases.read` 会话 | `ReleaseHistoryVersionsResponse` | 401、403、404、422、500 |
| `GET /api/releases/history/{task_id}/bindings/{binding_id}/versions/{version}` | `releases.read` 或 `releases.publish` 会话 | `ReleaseVersionContentResponse` | 401、403、404、422、500 |
| `POST /api/releases/rollback` | `releases.publish` 会话与 CSRF | `RollbackCreatedResponse`（202） | 400、401、403、404、409、422、500、503 |
| `GET /api/upgrade/packages/check` | `upgrade.create` 会话 | `SourcePackageCheckResponse` | 401、403、422、500 |
| `GET /api/upgrade/modules/check` | `upgrade.create` 会话 | `ModulePackageCheckResponse` | 401、403、422、500 |
| `POST /api/upgrade/nodes/{node_id}/nginx-v` | `upgrade.create` 会话与 CSRF | `ParseConfigResponse` | 400、401、403、404、422、500 |
| `POST /api/upgrade/parse-config` | `upgrade.create` 会话与 CSRF | `ParseConfigResponse` | 401、403、422、500 |
| `POST /api/upgrade/compute-config` | `upgrade.create` 会话与 CSRF | `ComputeConfigResponse` | 400、401、403、422、500 |
| `POST /api/upgrade/tasks` | `upgrade.execute` 会话与 CSRF | `UpgradeTaskCreateResponse` | 400、401、403、409、422、500 |
| `POST /api/upgrade/tasks/{task_id}/cancel` | `upgrade.execute` 会话与 CSRF | `UpgradeCancelResponse` | 400、401、403、404、409、422、500 |
| `POST /api/upgrade/tasks/{task_id}/rollback` | `upgrade.execute` 会话与 CSRF | `UpgradeRollbackResponse` | 400、401、403、404、409、422、500 |
| `POST /api/nginx-install/configure-preview` | `upgrade.read` 或 `upgrade.execute` 会话与 CSRF | `InstallConfigureResponse` | 400、401、403、422、500 |
| `POST /api/nginx-install/tasks` | `upgrade.execute` 会话与 CSRF | `InstallBatchResponse` | 400、401、403、409、422、500 |
| `GET /api/nginx-install/batches/{batch_number}` | `upgrade.read` 或任务触发人的 `upgrade.execute` 会话 | `InstallBatchProgressResponse` | 401、403、404、422、500 |
| `GET /api/nginx/service/nodes` | `nginx_service.read` 会话 | `ServiceNodeListResponse` | 401、403、422、500 |
| `POST /api/nginx/service/execute` | `nginx_service.operate` 会话与 CSRF | `ServiceExecuteResponse` | 400、401、403、422、500 |
| `GET /api/nginx/service/tasks/{task_id}` | `nginx_service.read` 会话 | `ServiceTaskProgressResponse` | 401、403、404、422、500 |
| `GET /api/settings/group` | `settings.read` 会话 | `SettingsGroupResponse` | 401、403、422、500 |
| `GET /api/settings/all` | `settings.read` 会话 | `SettingsAllResponse` | 401、403、500 |
| `POST /api/settings/group` | 超级管理员会话与 CSRF | `SettingsUpdateResponse` | 400、401、403、404、409、422、500 |

用户组成员 GET 支持逗号分隔的用户名/邮箱搜索词，`per_page` 默认为 10、允许 1–100；响应包含总数、页数和当前每页条数。POST 使用 `{ "action": "add" | "remove", "user_ids": [...] }` 对最多 200 个用户执行幂等增删。用户、角色和用户组的其余操作使用 Jinja2 表单页面，并由全局 CSRF 依赖保护。

任务筛选、分页、增量日志游标、可见范围和取消检查点见 [tasks.md](tasks.md)。`GET /api/tasks` 的 `search` 参数按逗号切分，词间 AND，单词在批次号、主机名和 IP 中 OR；RBAC 管理范围和权限来源见 [rbac.md](rbac.md)。后续功能应在对应模块定义请求/响应模型、摘要、参数约束及会产生的状态码，并引用本文件的公共错误协议。

节点探测、系统信息和 Nginx 检测接口返回持久化任务 ID。具备 `nodes.ssh_test` 权限的用户可读取自己创建的对应任务；解锁后自动创建的批量探测任务也允许 `nodes.unlock` 用户读取。`GET /api/tasks/{task_id}` 返回实时进度、日志和结果树。

`POST /api/nodes/import` 的校验错误按单条原因合并；同一原因影响多行时，`errors[].row` 为 `0`、`errors[].merged` 为 `true`，受影响的 Excel 行号范围放在 `errors[].row_range`，`errors[].message` 仅包含异常原因。未合并错误保留原有行号语义，`row_range` 可省略。页面只显示去重后的 `message`，不显示行号。出现校验错误时整份工作簿不会写入。

配置发现与同步接口返回持久化任务 ID。具备 `configs.sync` 权限的用户可读取自己创建的 `config_discover` 和 `config_batch_sync` 任务；发现结果仅含远程路径和错误摘要，同步正文不会写入任务参数、结果或日志。发现清单与同步操作结果按任务 1 MiB 上限截取明细，完整路径操作通过增量任务日志保留。

凭证解密 API 仅返回单个明文字段并设置 `Cache-Control: no-store`；凭证不会出现在选择器响应、任务参数或日志中。启停接口对活动关联节点创建异步测试任务，无关联节点时只改变凭证状态，不创建空任务。节点和凭证导入失败返回行号元数据与具体校验原因，整份工作簿不写入；凭证导出仅供超级管理员使用，导入要求 `credentials.create`，两者均写入不含认证材料的操作审计。

配置标签、绑定和版本历史使用 Jinja2 页面路由；远程发现与同步使用上述 JSON API。页面路径、权限和状态规则见 [configs.md](configs.md)。

发布中心使用 Jinja2 页面 `/releases/center/`，发布历史使用 `/releases/`。历史 API 需要 `releases.read`，按批次返回持久化任务结果树和当前资产门禁；历史绑定的其他版本元数据按需分页读取，版本正文由关联历史项的预览接口单独返回。发布与回滚创建后分别返回 `release_publish`、`release_rollback` 任务 ID；具备 `releases.publish` 的触发人可轮询本人两类任务；具备 `upgrade.read` 的触发人可轮询本人升级任务。结果树不保存配置正文。发布执行、回滚门禁和备份约定见 [releases.md](releases.md)。

Nginx 编译升级使用 Jinja2 页面 `/upgrade/center/`、`/upgrade/packages/`、`/upgrade/modules/`、`/upgrade/history/` 和 `/upgrade/tasks/{task_id}/`。`POST /api/upgrade/parse-config` 与 `POST /api/upgrade/compute-config` 需要 `upgrade.create`；后者接受 `upgrade_mode` 和 `target_prefix`，在切换路径模式时返回服务端重写后的参数。`POST /api/upgrade/nodes/{node_id}/nginx-v` 通过 SSH 读取节点基线。`POST /api/upgrade/tasks`、`POST /api/upgrade/tasks/{task_id}/cancel` 和 `POST /api/upgrade/tasks/{task_id}/rollback` 需要 `upgrade.execute` 与 CSRF；包冲突检查接口需要 `upgrade.create`。任务进度和日志沿用 `GET /api/tasks/{task_id}`，页面、接口和任务门禁见 [upgrade.md](upgrade.md)。

Nginx 全新安装页面见 [nginx-install.md](nginx-install.md)。configure 预览和安装批次创建分别返回 `InstallConfigureResponse`、`InstallBatchResponse`；批次进度仅向安装读取权限用户或触发人开放。安装任务复用通用日志/取消 API，并按可取消阶段校验权限。

Nginx 启停页面、历史和操作日志见 [nginx-service.md](nginx-service.md)。节点选择和任务进度使用独立 `nginx_service.read` API；批次创建要求 `nginx_service.operate`，并经过全局 CSRF 校验。

Nginx 卸载页面、历史和日志见 [nginx-uninstall.md](nginx-uninstall.md)。路径预览与批次创建需要 `nginx_uninstall.execute`；节点选择、历史和任务读取需要 `nginx_uninstall.read`。卸载状态、结果树和取消沿用 NX-005 统一任务接口。

系统设置页面、分组校验、实际生效范围和数据保留策略见 [settings.md](settings.md)。读取 API 仅返回当前登记的预置项，保存 API 只接受同一分组的已接线 key。
