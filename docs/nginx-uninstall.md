# Nginx 卸载

## 页面与权限

- `/nginx/uninstall/` 显示卸载统计和最近任务，通过“查看全部”进入历史；最近任务区不提供筛选框。
- `/nginx/uninstall/center/` 按选择节点、探测删除范围、确认执行依次展开操作区，不显示“执行步骤”说明卡或编号步骤导航。点击探测后立即进入路径页并显示加载状态，SSH 预览请求在后台返回后更新结果；失败时在当前页显示错误。节点选择器沿用升级页的摘要标签、AND 查询标签、节点表格、分页和每页数量控件，只列 SSH 在线、凭证启用、Nginx 已识别、未锁定且没有活动卸载任务的节点。
- 路径页以加粗子标题展示主机名/IP、systemctl 或二进制托管方式及运行状态；桌面宽度下保持单行，较窄屏幕允许换行。发布备份、编译工作目录和第三方模块目录分别显示在每台节点自己的路径列表中，默认勾选，可逐节点独立选择或取消。
- 中心页右栏显示“卸载进度”，包括批次总进度、逐节点状态/当前步骤/进度条、完整日志入口和协作取消；数据来自持久化卸载批次轮询。桌面布局沿用升级中心 1.45:0.8 主栏/进度栏比例，进度栏最小宽度 290px；991.98px 以下转为单栏。
- 页面选择和预览、创建接口均按系统设置 `node.batch_max_count` 限制，默认 3；刷新中心页应用最新上限。
- `/nginx/uninstall/history/` 支持节点、IP、批次、prefix、包名、操作人和状态筛选/分页。
- `/nginx/uninstall/task/{task_id}/log/` 展示卸载快照、结果树及同批次完整远程操作日志；批次涉及多个节点时可按节点筛选。
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
- 卸载节点选择器默认只列 SSH 在线且 Nginx 已识别的活动节点；锁定、凭证不可用和已有卸载任务的节点不列入可选列表。
- 源码安装使用实时 `nginx -V` 获取 `--prefix` 和 `*-path`。prefix 必选，prefix 下的 configure 子路径保留目录层级；选择父目录时，页面自动勾选其子路径。执行前再次读取编译参数并要求 prefix 与预览一致。
- 删除源码 prefix 前检查其直接子项。允许 Nginx 常见安装目录和 `nginx -V` 声明的子路径；出现其他顶层目录或文件、无法读取编译参数、目录检查失败或 prefix 已变化时拒绝递归删除。危险系统根目录、相对路径和路径穿越同样拒绝。
- 发布备份目录按 `release.backup_dir/{hostname}` 生成，编译目录取 `upgrade.default_work_dir`，模块目录为其 `nginx-modules` 子目录；由页面分别选择清理。设置变更用于后续预览和新建卸载任务，任务创建时服务端重新读取并校验路径。
- 运行中的 Nginx 先通过 systemd 或 `nginx -s quit`/`nginx -s stop` 停止，不执行 `kill -9`。系统包和 systemd 操作需要 root 或免密 sudo。
- 源码安装在检测为 systemd 托管时禁用 unit，并只删除 `/etc/systemd/system` 中的 unit 文件；包安装只清理可能残留的平台自写 `/etc/systemd/system/nginx.service`。发行版 `/lib`、`/usr/lib` unit 不删除。
- 卸载历史与升级、安装和启停历史一样，按逗号分隔的 AND 搜索可匹配操作人用户名；安装首页最近任务也显示操作人。
- 单节点远程预览异常日志记录异常类型、堆栈位置和节点编号，不写凭证明文；返回结果须包含来源、systemd 托管、运行状态及完整路径字段，否则将节点设为不可执行。
- 执行成功后在同一事务清空节点 Nginx 可执行路径/版本，更新 `nginx_available=False`，将未标记删除的配置绑定改为 `orphaned`。节点资产中的 Nginx 主配置路径保留，供重新安装时沿用；不删除发布历史或绑定版本。
- 卸载主程序已成功移除、但备份或 unit 清理等后续步骤失败时，节点状态仍回写为 Nginx 不可用；任务保留 `failed` 和失败步骤，避免平台继续将已卸载节点当作可用节点。

## 持久化

统一任务类型为 `nginx_uninstall`，每个节点单独记录任务，共享 `UN-YYMMDD-NNNN` 批次号。迁移 v12 新增 `ngxops_nginx_uninstall_runs` 保存节点身份、来源、包名、prefix、清理选项和创建时间；迁移 v19 新增 `ngxops_nginx_operation_logs` 按节点保存脱敏远程输出。任务中心的 `ngxops_task_logs` 只保存核心操作，卸载专用详情聚合同批次远程日志。快照不保存 SSH 凭证明文。取消是协作式的，已执行的远程命令不会被强制终止，也不提供卸载回滚。

部署时备份数据库和密钥后显式执行 `python -m ngxops.database upgrade` 应用未执行迁移。
