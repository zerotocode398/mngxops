# 10b · Nginx 启停（nginx_service）

## 1. 模块目标与范围

运维工具下与「Nginx 升级」同级的独立启停页：对已选在线节点执行 `start` / `stop` / `reload` / `restart`。

底层复用 [`utils/nginx_ops.py`](../utils/nginx_ops.py)（systemctl 或二进制），经 TaskCenter 异步执行与进度遮罩反馈。

**不做**：Nginx 进程态常驻采集、节点列表行内启停按钮、独立 RBAC 资源码。

## 2. 角色与权限

| 能力 | 权限 |
|------|------|
| 菜单与页面 | `nodes.read`（运维工具分区同条件） |
| 执行启停 | `nodes.update` |

## 3. 页面与路由

| 路径 | 说明 |
|------|------|
| `/nginx-service/` | 启停操作台 + 最近启停任务 |
| `/nginx-service/history/` | 启停历史 |
| `POST /nginx-service/api/execute/` | 创建异步任务 |

模板：`nginx_service/index.html`、`history.html`。侧栏：运维工具 → Nginx 启停。

最近任务条数复用 `dashboard.recent_tasks_count`（与升级/安装首页一致）。

## 4. 业务流程

1. 弹窗多选节点（`/nodes/api/search-nodes/`）：仅 online + 有凭证可勾；上限 `node.batch_max_count`。  
2. 选择动作并确认（stop/restart 强提示；reload 说明平滑重载 ≠ 重启）。  
3. API 校验门禁后创建 `TaskCenterTask(operation_type=nginx_service_control)`，后台逐节点调用 `start_nginx`/`stop_nginx`/`reload_nginx`/`restart_nginx`。  
4. 前端 `#asyncProgressOverlay` 轮询；结果树写入任务中心。  
5. **不**因 stop 将节点 `status` 标 offline。

## 5. 设置

- 批量上限：复用 [`node.batch_max_count`](12-settings.md)。  
- 最近任务条数：复用 [`dashboard.recent_tasks_count`](12-settings.md)。

## 6. 实现锚点

- [`apps/nginx_service/views.py`](../apps/nginx_service/views.py)
- TaskCenter 类型与可见性：[`apps/releases/models.py`](../apps/releases/models.py)、列表/进度/取消过滤
- 审计：`OPERATION_AUDIT_MAP["nginx_service_control"]`
- 原型：[`docs/prototypes/nginx-service-prototype.html`](prototypes/nginx-service-prototype.html)

## 7. 关联结论

- Q131 / Q135 / Q137（见 [`AGENTS.md`](../AGENTS.md)）
