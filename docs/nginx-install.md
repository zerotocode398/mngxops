# Nginx 全新安装

## 页面与权限

- `/nginx-install/` 展示安装统计和最近任务，任务表显示操作人；`/nginx-install/center/` 提供三步安装向导。
- `/nginx-install/history/` 支持逗号分隔的节点、IP、版本、安装路径、批次、操作人关键词（词间 AND）和状态筛选，使用升级历史相同的查询标签和分页控件；`/nginx-install/task/{task_id}/log/` 聚合同批次逐节点的完整远程操作日志，支持按节点筛选，并展示参数快照、增量日志和任务状态，不提供任务中心跳转按钮。
- 安装与升级共用 `upgrade.read/create/delete/execute` 权限：查看历史及下载包使用 `read`，上传源码/模块包使用 `create`，删除包使用 `delete`，安装或升级任务的执行与取消使用 `execute`。两个向导页面都允许 `read` 或 `execute` 进入；只读用户不能提交安装任务。安装页和 API 不再定义独立的 `nginx_install.*` 授权。
- 任务批次与统一任务中心仍使用 `nginx_install` 操作类型，以区分具体工作流。`upgrade.execute` 可读取和取消本人安装任务；`upgrade.read` 可查看安装历史和全部安装批次。

## 向导与门禁

1. 安装首页沿用升级首页的统计卡片和最近任务布局，并提供开始安装、历史、源码包和模块包入口。向导复用升级页的摘要标签、紧凑节点弹窗、查询标签、AND 搜索、分页和每页条数交互；节点列表仅展示 SSH 在线节点，按“主机名 / IP、SSH 用户、环境、Nginx、节点组”排列。检测到 Nginx 时只显示版本号；锁定或无有效凭证的在线节点仍显示不可选原因。源码包使用与升级页一致的可搜索单选表格、分页和每页条数控件，缺失文件的包不可选。安装门禁允许 Nginx 未探测或不可用的在线节点，已检测到 Nginx 时在确认页提示覆盖风险。每批上限由 `node.batch_max_count` 控制，默认 3；保存设置后，刷新向导更新选择上限，创建任务时由后端按最新值校验。离线、未知、锁定或无有效凭证的节点不可选。创建时再次检查门禁，并跳过并说明已变化或已有安装任务的节点。
2. 设置 `--prefix`、`--user`、`--group`、监听端口、远程工作目录、`make -j`、官方模块、第三方 Git/离线模块和额外 configure 参数。编译参数表单采用两列布局；已选官方模块保留绿色终端逐项展示，仅删除重复的备注文案，完整 configure 参数在确认步骤查看。选择离线模块包后会自动带入包名，手工修改的名称会保留。默认模块为 SSL、HTTP/2、realip、stub_status、stream 和 stream_ssl。额外参数不能覆盖受保护的安装路径、二进制路径或运行账户。
3. 进入确认步骤前，服务端使用每个节点当前 SSH 用户检查安装路径和远程工作目录是否可写；检查会短暂创建目标目录（仅当原路径不存在）及随机探测目录，并清理本次创建的空目录。路径不可写、节点不可用或 SSH 检查失败时展示节点、用户、路径和原因，并阻止继续；只有 `upgrade.execute` 用户触发远端检查。通过结果显示在确认页。远端权限可能在预检后变化，安装任务仍会按实际执行结果报告失败。
4. 查看服务端生成的 configure 参数、节点状态和覆盖风险后确认。目标选择和编译参数步骤不展示 SSH 用户到监听端口的映射；端口低于 1024 且 SSH 用户非 root 时，离开参数步骤会以主机/IP、SSH 用户和监听端口表格提示风险。每次从参数步骤进入确认步骤都会重新提示；确认摘要按“键: 值”排列源码版本、安装路径、监听端口、临时安装目录和编译并发，并用颜色标出启动管理备注。摘要不重复展示运行账户与模块数量，configure 参数预览不显示续行说明。已有 Nginx 的节点允许继续，确认页和开始安装确认均提示目标路径或 `nginx.service` 可能被覆盖，开始安装确认也会再次说明低端口权限风险及 systemd 与二进制启动分流。

安装批次号格式为 `IN-YYMMDD-NNNN`。已被 pending/running 安装引用的源码包或离线模块包不能覆盖或删除；安装批次创建和升级包管理共用进程内锁。相同节点已有活动安装时不会重复排队。

## 执行与结果

每个节点由 NX-005 任务线程池执行独立任务：检查 gcc/make、上传并校验源码、解压、准备第三方模块、configure、make、make install、按需改写主配置默认 `listen 80`、执行 `nginx -t`，最后启动并读取版本。在线第三方模块的 clone/fetch 超时读取系统设置 `upgrade.git_clone_timeout_minutes`（“Git clone 超时”，默认 2 分钟，范围 1–10 分钟），与 Nginx 升级共用。

- 有 `systemctl` 且当前 SSH 身份可写 systemd unit 目录或使用免密 sudo 时，写入 `/etc/systemd/system/nginx.service`，执行 daemon-reload、enable 和 start。unit 的 `User`/`Group` 使用向导值；注册、启用或启动失败会使安装失败，不静默改用二进制启动。
- 无 systemd 管理能力时直接启动编译出的 Nginx 二进制，并在任务日志记录分流原因。
- 成功后写回节点在线/Nginx 可用状态、版本、二进制路径和主配置路径，并更新节点导入导出路径与配置同步路径。随后复用安装阶段的 SSH 连接执行完整配置发现/同步；配置同步失败单独记录，不回滚或否定已启动的安装。
- 任务进度、阶段、核心日志、完整远程输出、结果树和配置同步摘要持久化。迁移 v19 新增专用完整日志表；任务中心只显示核心操作，安装历史详情聚合同批次全部节点的远程操作记录，并可选择节点查看。取消仅允许 pending、工具检查、源码上传、解压和模块准备阶段；取消为协作式操作，已发出的远程命令可能运行到当前检查点。

## JSON API

- `POST /api/nginx-install/configure-preview`：校验参数并返回 configure 字符串和目标路径；需要 `upgrade.read` 或 `upgrade.execute` 与 CSRF。
- `POST /api/nginx-install/tasks`：全量复核包、参数和节点，创建每节点 `nginx_install` 任务及批次；需要 `upgrade.execute` 与 CSRF。
- `GET /api/nginx-install/batches/{batch_number}`：读取有权查看的批次进度、节点任务和日志入口；`upgrade.execute` 仅能读取本人批次，`upgrade.read` 可读取全部批次。
- 安装专用详情通过 `GET /api/tasks/{task_id}?log_view=install_full` 逐行读取同批次完整远程日志；任务中心默认读取核心操作日志。协作取消使用 `POST /api/tasks/{task_id}/cancel`，要求 `upgrade.execute` 并额外检查安装阶段。

成功响应模型、状态码和认证方案登记在 [api.md](api.md)。参数快照、任务结果和日志不保存 SSH 密码或私钥。执行器和包锁目前位于单进程内，部署限制为单个 Uvicorn worker。最大节点数由 `node.batch_max_count` 设置控制，默认 3。

部署前备份数据库、`.fernet_key`、`.secret_key` 和 `nginx_packages/`；然后显式运行 `python -m ngxops.database upgrade` 应用未执行迁移，包括 v15 的权限合并。迁移 v15 将旧 `nginx_install.read/create` 角色和个人直授权分别转换为 `upgrade.read/execute` 并删除旧权限项。当前验证使用伪造 SSH/任务提交，未连接真实节点、运行真实编译或验证 systemd 环境。
