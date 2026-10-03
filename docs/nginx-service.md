# Nginx 启停

## 页面与权限

- `/nginx/service/` 提供节点选择、start/stop/reload/restart、最近任务和真实执行进度。
- `/nginx/service/history/` 支持主机名、IP、动作、批次关键词，任务状态筛选和分页。
- `/nginx/service/task/{task_id}/log/` 展示批次目标、逐节点结果、完整任务日志及任务中心入口。
- 页面、选择器、历史和日志需要 `nginx_service.read`；创建批次需要 `nginx_service.operate`。
- 批量上限由 `node.batch_max_count` 控制，默认 3；页面选择上限和后端校验一致。

## 节点门禁

可执行节点必须未删除、未锁定、SSH 状态为 `online`、Nginx 探测状态为可用，并关联启用的凭证。选择器对其他节点展示首个不满足的条件；批次创建时跳过不合格节点并回报原因。后台开始每个节点操作前会再次读取节点和凭证状态。启停不改写节点 SSH 状态或 Nginx 探测状态。

## 执行动作

批次由统一任务执行器在工作线程中顺序处理节点，状态、进度、结果树和追加日志保存在 `ngxops_tasks` 与 `ngxops_task_logs`。批次号格式为 `OP-YYMMDD-NNNN`，每天递增。结果树保存每个目标的标识、动作状态和安全摘要，不保存凭证明文。

每个 SSH 会话先探测 `nginx` / `nginx.service` 是否处于活动或启用状态。匹配到受 systemd 管理的 unit 时使用 `systemctl`；否则使用节点记录的 Nginx 二进制路径。二进制停止先尝试优雅退出 `-s quit`，失败后尝试 `-s stop`；二进制重启尝试 quit/stop 后启动。重载只发起 reload，不会在 Nginx 未运行时替换为 start。停止与重启可能中断业务流量，前端在提交前提示风险。

任务通过 `TaskContext` 注册 SSH 客户端关闭回调，并在任务检查点读取协作式取消状态。取消不能保证撤回已经发往远端的 systemd 或 Nginx 命令。批量上限和执行线程池遵守当前单 Uvicorn worker 的部署约束。

## JSON API

- `GET /api/nginx/service/nodes`：按主机名、IP 或节点组搜索节点并返回门禁结果，不返回凭证明文。
- `POST /api/nginx/service/execute`：按动作和节点 ID 创建异步批次，响应包含任务 ID、批次号和跳过节点。
- `GET /api/nginx/service/tasks/{task_id}`：返回任务状态、进度、结果树和基于 `after_log_id` 的增量日志。

API 需要 `sessionid` Cookie；写请求还需要全局 CSRF Cookie 和 `X-CSRFToken`。响应模型和状态码由 OpenAPI 登记，错误响应使用共享 `ApiError`。

## 数据与限制

NX-052 不新增迁移和独立运行表。动作及批次、目标摘要、触发用户、任务状态、结果和日志都复用 NX-005 的任务记录。批次递增锁为进程内锁，与项目当前单进程执行器部署约束一致。
