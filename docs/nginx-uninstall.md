# Nginx 卸载

## 页面与权限

- `/nginx/uninstall/` 显示近 7 天成功/失败、进行中任务和最近卸载记录。
- `/nginx/uninstall/center/` 提供选择节点、探测删除范围、确认执行三步向导。批量上限由 `node.batch_max_count` 控制，默认 3；保存设置后，刷新页面更新选择上限，创建时由后端按最新值校验。
- `/nginx/uninstall/history/` 支持节点、IP、批次、prefix、包名和状态筛选/分页。
- `/nginx/uninstall/task/{task_id}/log/` 展示卸载快照、结果树及可轮询的完整日志。
- 页面/历史使用 `nginx_uninstall.read`；预览和执行使用 `nginx_uninstall.execute`。迁移 v12 将既有 `nodes.read` 授权复制为卸载查看，将 `nodes.update` 复制为卸载执行，适配原项目的授权迁移策略。

## API

| 方法与路径 | 用途 | 权限 |
|---|---|---|
| `GET /api/nginx/uninstall/nodes` | 搜索节点并返回卸载门禁 | `nginx_uninstall.read` |
| `POST /api/nginx/uninstall/preview` | SSH 探测 `nginx -V`、包归属、systemd、运行态和删除路径 | `nginx_uninstall.execute` |
| `POST /api/nginx/uninstall/tasks` | 按确认的范围创建批次任务 | `nginx_uninstall.execute` + CSRF |
| `GET /api/nginx/uninstall/batches/{batch_number}` | 读取逐节点任务进度 | `nginx_uninstall.read` 或本人 `nginx_uninstall.execute` |
| `GET /api/nginx/uninstall/tasks/{task_id}` | 读取状态、结果树和增量日志 | `nginx_uninstall.read` 或本人 `nginx_uninstall.execute` |
| `POST /api/tasks/{task_id}/cancel` | 协作式取消 | `nginx_uninstall.execute` + CSRF |

写请求使用会话 Cookie 和 `X-CSRFToken`，所有接口在 OpenAPI 中登记结构化请求/响应与错误模型。SSH 阻塞操作仅在预览同步路由的线程池或 NX-005 任务线程中运行；页面可轮询真实任务状态。

## 删除范围和安全门禁

- 仅活动、未锁定、SSH 在线、凭证启用且 `nginx_available=True` 的节点可选；创建任务与后台执行时均重新检查。
- 通过 `rpm -qf` 或 `dpkg -S` 确认软件包归属。包安装使用 `dnf remove -y`、`yum remove -y` 或 `apt-get remove -y`，不会对包管理文件执行 `rm`，也不会使用 `purge`。
- 源码安装使用 `nginx -V` 获取 `--prefix` 和 `*-path`。默认必选 prefix，并默认勾选 prefix/sbin/modules/conf/pid 路径；prefix 可在页面修订。危险系统根目录、相对路径、路径穿越和路径变化会拒绝。Nginx 配置/日志/模块路径会收敛到最近的 `nginx` 目录，父子目标去重。
- 发布备份目录按 `release.backup_dir/{hostname}` 生成，编译目录取 `upgrade.default_work_dir`，模块目录为其 `nginx-modules` 子目录；由页面分别选择清理。设置变更用于后续预览和新建卸载任务，任务创建时服务端重新读取并校验路径。
- 运行中的 Nginx 先通过 systemd 或 `nginx -s quit`/`nginx -s stop` 停止，不执行 `kill -9`。系统包和 systemd 操作需要 root 或免密 sudo。
- 源码安装在检测为 systemd 托管时禁用 unit，并只删除 `/etc/systemd/system` 中的 unit 文件；包安装只清理可能残留的平台自写 `/etc/systemd/system/nginx.service`。发行版 `/lib`、`/usr/lib` unit 不删除。
- 执行成功后在同一事务清空节点 Nginx 可执行路径/版本，更新 `nginx_available=False`，将未标记删除的配置绑定改为 `orphaned`。节点资产中的 Nginx 主配置路径保留，供重新安装时沿用；不删除发布历史或绑定版本。
- 卸载主程序已成功移除、但备份或 unit 清理等后续步骤失败时，节点状态仍回写为 Nginx 不可用；任务保留 `failed` 和失败步骤，避免平台继续将已卸载节点当作可用节点。

## 持久化

统一任务类型为 `nginx_uninstall`，每个节点单独记录任务，共享 `UN-YYMMDD-NNNN` 批次号。迁移 v12 新增 `ngxops_nginx_uninstall_runs` 保存节点身份、来源、包名、prefix、清理选项和创建时间；实际进度、结果树、触发人和脱敏日志保存在 `ngxops_tasks`/`ngxops_task_logs`。快照不保存 SSH 凭证明文。取消是协作式的，已执行的远程命令不会被强制终止，也不提供卸载回滚。

部署时备份数据库和密钥后显式执行 `python -m ngxops.database upgrade` 应用 v12。
