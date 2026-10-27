# Nginx 启停

## 页面与权限

- `/nginx/service/` 提供节点选择、start/stop/reload/restart、最近任务和真实执行进度；新任务通过全局进度通知显示状态，并可直接打开完整日志。
- `/nginx/service/history/` 支持逗号分隔的主机名、IP、动作、批次关键词（词间 AND）、任务状态筛选和公共分页控件，创建时间按 UTC+8 显示。
- `/nginx/service/task/{task_id}/log/` 展示批次目标、逐节点结果、完整执行日志和多节点日志筛选；不提供任务中心跳转。
- 页面、选择器、历史和日志需要 `nginx_service.read`；创建批次需要 `nginx_service.operate`。
- 选择器沿用升级页的搜索标签、节点表格、分页和每页数量布局，默认只返回 SSH 在线、凭证启用、Nginx 已识别且未锁定的节点。批量上限由 `node.batch_max_count` 控制，默认 3；页面选择上限和后端校验一致。

## 节点门禁

可执行节点必须未删除、未锁定、SSH 状态为 `online`、Nginx 探测状态为可用，并关联启用的凭证。选择器对其他节点展示首个不满足的条件；批次创建时跳过不合格节点并回报原因。后台开始每个节点操作前会再次读取节点和凭证状态。启停不改写节点 SSH 状态或 Nginx 探测状态。

## 执行动作

批次由统一任务执行器在工作线程中顺序处理节点，状态、进度、结果树和追加日志保存在 `ngxops_tasks` 与 `ngxops_task_logs`。批次号格式为 `OP-YYMMDD-NNNN`，每天递增。结果树保存每个目标的标识、动作状态和安全摘要，不保存凭证明文。详情页不显示重复的节点状态列，只展示主机名、IP 和结果。

每个 SSH 会话先探测 `nginx` / `nginx.service` 是否处于活动或启用状态。匹配到受 systemd 管理的 unit 时使用 `systemctl`；否则使用节点记录的 Nginx 二进制路径。二进制停止先尝试优雅退出 `-s quit`，失败后尝试 `-s stop`；二进制重启尝试 quit/stop 后启动。重载只发起 reload，不会在 Nginx 未运行时替换为 start。停止与重启可能中断业务流量，前端在提交前提示风险。

远程命令输出按原始行写入完整日志，显示时间和输出两列；迁移 v19 的专用日志表保存 `node_id`，启停历史详情可按目标节点筛选。任务中心的通用日志只保留核心操作和节点归属，不重复展示全部远程输出；v18 前的旧任务从“节点开始 Nginx”记录推断日志归属。任务通过 `TaskContext` 注册 SSH 客户端关闭回调，并在任务检查点读取协作式取消状态。取消不能保证撤回已经发往远端的 systemd 或 Nginx 命令。批量上限和执行线程池遵守当前单 Uvicorn worker 的部署约束。

## JSON API

- `GET /api/nginx/service/nodes`：按主机名、IP 或节点组搜索可用节点并返回门禁结果和当前 `batch_max_count`，不返回凭证明文。
- `POST /api/nginx/service/execute`：按动作和节点 ID 创建异步批次，响应包含任务 ID、批次号和跳过节点。
- `GET /api/nginx/service/tasks/{task_id}`：返回任务状态、进度、结果树和基于 `after_log_id` 的增量日志。

API 需要 `sessionid` Cookie；写请求还需要全局 CSRF Cookie 和 `X-CSRFToken`。响应模型和状态码由 OpenAPI 登记，错误响应使用共享 `ApiError`。

## 数据与限制

迁移 v18 为通用任务日志增加可空节点归属和筛选索引；迁移 v19 新增 `ngxops_nginx_operation_logs`，按操作类型、任务和节点保存安装、卸载、启停的完整远程输出。启停状态、进度、结果树和核心日志仍复用 NX-005 任务记录。批次递增锁为进程内锁，与项目当前单进程执行器部署约束一致。
