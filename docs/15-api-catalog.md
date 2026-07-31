# 15 · 路由与 API 目录

约定：页面路由为服务端渲染；标注 **JSON** 的接口返回 JSON。权限以各 View 的 `permission_resource`/`permission_action` 为准（超管豁免）。

## 1. 账户 `accounts`

| 方法 | 路径 | name | 说明 |
|------|------|------|------|
| GET/POST | `/login/` | `accounts:login` | 登录 |
| POST/GET | `/logout/` | `accounts:logout` | 登出 |
| GET/POST | `/profile/` | `accounts:profile` | 个人资料 |
| GET/POST | `/password/change/` | `accounts:password_change` | 改密 |

## 2. 仪表盘 `dashboard`

| 方法 | 路径 | name | 说明 |
|------|------|------|------|
| GET | `/` | `dashboard:index` | 首页 |
| GET | `/api/stats/` | `dashboard:stats_api` | **JSON** 统计轮询（节点/待推送/执行中/近7天失败） |

## 3. 用户 `users`

| 路径模式 | 说明 |
|----------|------|
| `/users/` CRUD + `<pk>/lock/` | 用户（路由用 pk，Q82） |
| `/users/groups|roles/` CRUD + manage-users | 角色（两组别名） |
| `/users/teams/` CRUD + members / manage-members | 用户组 |

## 4. 凭证 `credentials`

| 路径 | 说明 |
|------|------|
| `/credentials/` CRUD | 列表/创建/编辑/删除 |
| `<pk>/toggle-enable/` | 启用/禁用（启用触发异步测试） |
| `<pk>/decrypt/` | **JSON** 解密展示 |
| `<pk>/related-nodes/` | 关联节点 |
| `<pk>/enable-progress/` | **JSON** 启用进度 |
| `api/list/` | **JSON** 列表 |

## 5. 节点 `nodes`

| 路径 | 说明 |
|------|------|
| `/nodes/` CRUD、`batch-delete/` | 节点管理 |
| `import/template/`、`import/` | Excel 模板与导入 **JSON** |
| `lock/`、`test/`、`batch-test/` | 锁/SSH 测 |
| `detail/`、`system-info/`、`nginx-version/` | 详情与异步采集 |
| `api/list/`、`api/search-nodes/`、`api/groups/` | **JSON** 选择器 |
| `groups/` CRUD、`manage-nodes/` | 节点组 |

## 6. 配置 `configs`

| 路径 | 说明 |
|------|------|
| `/configs/` CRUD | 配置标签 |
| `bindings/create|…` | 绑定 CRUD、restore |
| `bindings/<pk>/versions/…` | 版本列表/详情/恢复 |
| `bindings/<pk>/compare/…` | 对比与应用 |
| `api/by-nodes/`、`api/preview-glob/`、`api/update-preview/` | **JSON** |
| `sync/`、`sync/api/batch|single|progress` | 同步向导与进度 |

## 7. 发布 / 任务中心 `releases`

| 路径 | 说明 |
|------|------|
| `center/` | 发布中心页 |
| `api/nodes/`、`api/node-bindings/<node_id>/` | **JSON** 节点与绑定 |
| `api/create/` | **JSON** 创建发布任务并可自动执行 |
| `history/` | **任务中心**列表 |
| `tasks/<pk>/`、`tasks/<pk>/cancel/`、`tasks/progress/` | 任务详情 / **JSON** 协作取消 / **JSON** 进度 |
| `list/` | **发布历史** |
| `<pk>/`、`rollback/`、`retry/` | 单任务详情/回滚/重试 |
| `api/selected-rollback/` | **JSON** 勾选回滚 |
| `batch-rollback/<batch_number>/` | 批次回滚入口 |
| `version/<version_id>/content/` | **JSON** 版本内容预览 |
| `center/<batch>/execute|cancel`、`center/task/<id>/…` | 中心执行/取消/单条 |

## 8. 升级 `upgrade`

| 路径 | 说明 |
|------|------|
| `packages/` 上传/校验/删除/下载 | 源码包 |
| `center/` | 四步向导 |
| `api/nginx-v/<node_id>/`、`parse-config/`、`compute-config/`、`batch-progress/` | **JSON** |
| `task/create/`、`task/<pk>/progress|log|cancel|rollback` | 任务 |
| `history/`、`/` | 历史 / 首页 |

## 9. 审计 `audit`

| 路径 | 说明 |
|------|------|
| `/audit/` | 操作日志 |
| `/audit/login/` | 登录日志 |

## 10. 设置 `settings`

| 路径 | 说明 |
|------|------|
| `/settings/` | GitLab 式设置页 |
| `save/` | **JSON** 保存 |
| `api/group/`、`api/all/` | **JSON** 分组/全量 |

## 11. 通用约定

- CSRF：表单与 AJAX 需带 Django CSRF token。
- 分页：`utils.pagination.PerPagePaginationMixin`，`?per_page=10|20|50|100`。
- 错误页：`templates/403.html`、`404.html`、`500.html`。
