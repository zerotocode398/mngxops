# 统一异步任务

## 执行协议

业务模块调用 `ngxops.tasks.executor.create_task` 持久化任务并提交可调用函数。可调用函数接收 `TaskContext`，通过以下方法报告执行过程：

- `update_progress(progress, detail)` 更新 0 到 99 的单调进度和当前步骤说明；终态统一写为 100。
- `append_log(message, level)` 追加一条脱敏日志。
- `set_result_tree(value)` 持久化 JSON 结果树。
- `check_cancelled()` 在安全检查点查询取消状态；收到取消时抛出 `TaskCancelled`。
- `register_cancel_callback(callback)` 登记关闭本地 SSH 客户端等资源的回调；取消时执行器会调用已登记回调。

函数返回的非空值会作为最终 JSON 结果树保存。凭证、私钥和会话令牌不得作为普通任务参数、结果或日志传递；已知敏感字段会按键名脱敏，常见敏感赋值与 PEM 私钥块也会在持久化前清理。异常只记录异常类型，不保存异常文本或堆栈。

任务状态沿用参考项目的 `pending`、`running`、`success`、`failed`、`cancelled`。任务进度、当前步骤、结果树、日志、批次、目标摘要、触发人及关联资源类型/ID 都持久化。关联资源 ID 不包含密钥或凭证明文。进度更新单调递增；只有 `pending/running` 任务可以继续更新或转成终态。

## 线程和进程边界

- 每个 Web 进程使用容量为 4 的 `ThreadPoolExecutor`，阻塞 SSH 与其他同步 I/O 必须由任务函数在线程池执行，不得直接放进 async 路由。
- 当前执行队列和 Future 取消句柄驻留进程内；SQLite 是状态持久层，不是跨进程队列。部署必须使用单个 Uvicorn worker，不支持多进程重复消费、跨进程取消或任务自动接管。
- 停止进程时不等待长任务，也不杀远端命令。下次启动会将遗留 `pending/running` 任务置为 `failed`，详情记为“服务进程重启，任务已中断”；任务不会自动重跑。
- 取消接口立即将活跃任务置为 `cancelled`，阻止后续进度/结果覆盖终态，并执行已登记的本地资源关闭回调。运行中的本地函数要在检查点协作退出；已经发往远端的命令可能继续运行，取消不会强制终止它。

## JSON API

所有路由都要求登录，应用全局 CSRF 校验适用于取消 POST。

| 方法与路径 | 行为 |
|---|---|
| `GET /api/tasks` | 分页查询可见任务；支持 `status`、`operation_type`、`source_batch`、逗号分隔 `search`、`page`、`page_size`。每个 search 词按批次号/主机名/IP 做 OR，词与词之间做 AND。 |
| `GET /api/tasks/{task_id}` | 读取进度、结果树与日志；以 `after_log_id` 和 `log_limit` 增量读取日志，响应的 `next_log_id` 可供下次轮询。 |
| `POST /api/tasks/{task_id}/cancel` | 协作式取消活跃任务；终态返回 400，取消期间状态竞态返回 409。 |

## 任务中心页面

- `GET /tasks/` 提供统一任务列表，支持多关键词搜索、任务类型/状态筛选、每页 10/15/30/50 条和分页；筛选项自动提交。
- `GET /tasks/{task_id}/` 展示触发人、批次、目标、进度、结构化结果树及持久化日志。活跃任务每 2 秒轮询状态、结果和增量日志；首屏载入 50 条日志，超过首屏日志量时可继续读取。节点结果摘要单行省略，展开后查看完整数据；日志把目标 IP 显示为“主机名 (IP)”，不显示日志级别。
- 目标节点默认显示前三台，更多目标可展开；节点列表 SSH 操作完成提示可直接跳转到对应任务详情。
- `releases.read` 和超级管理员可查看全部任务。其他具备相关业务权限的用户仅查看自己触发且操作类型匹配的任务；详情和取消均隐藏不可见任务。
- 发布/回滚批次链接跳转发布历史；其他批次可回到任务中心按批次搜索。
- `config_discover` 是 ngxops 配置同步流程实际创建的操作类型，因此任务中心提供该筛选项。旧文档中未启用的 `config_drift_check` 与 `config_glob_preview` 保留兼容但隐藏。

具有 `releases.read` 的用户可读全部任务；其他用户只能读取本人且其业务权限允许的操作类型，其中 `releases.publish` 可读取本人发布/回滚任务。详情和取消使用相同的任务可见范围；不可见任务按 404 隐藏。认证、权限、资源、状态冲突和参数校验错误统一使用 `success: false` 与 `message`；422 可附 `errors` 字段路径列表，不回显提交值。详见 [JSON API 约定](api.md)。

NX-005 提供任务基础设施和轮询 API，不提供任意函数提交 API。凭证启用测试由 NX-020 接入；单/批量节点探测、系统信息采集和 Nginx 版本检测由 NX-022 接入。其余同步、发布和 Nginx 生命周期任务由后续模块接入。

NX-031 配置任务使用 `config_discover` 执行只读远程发现，使用 `config_batch_sync` 执行单节点或批量同步。具备 `configs.sync` 权限的用户可轮询本人创建的任务。发现结果只包含路径和错误摘要，不包含配置正文；同步正文只用于数据库绑定和版本写入。批量同步并发由 `node.batch_max_count` 控制，默认最多三个节点；任务结果保存每节点新增、更新、跳过、远程缺失、远程清理和失败路径的完整计数；为遵守结果树 1 MiB 上限，明细树只展示有限路径，完整逐项信息保存在可增量读取的任务日志中。

NX-040 发布使用 `release_publish` 任务。具备 `releases.publish` 的触发人可轮询本人任务；批次号保存在 `source_batch`，结果树按节点和绑定保存版本、远程路径摘要、阶段和摘要，不保存配置正文或 SSH 凭证。发布前先写节点级备份，再经 SFTP 上传临时文件，校验大小和 MD5、复制并校验目标文件；同节点所有绑定通过 `nginx -t` 后统一 reload。`marked_deleted` 绑定先备份再删除，节点统一 reload 成功后物理删除本地绑定。任一绑定准备失败或 reload 失败时，会恢复本节点本批已替换或删除的文件。取消会关闭已登记 SSH 客户端并在检查点恢复尚未 reload 的文件；已经发往远端的命令可能无法中止。远程命令原始输出不会保存，避免 `nginx -t` 回显配置行。详见 [releases.md](releases.md)。

节点探测任务类型为 `node_ssh_test`、`node_batch_test`、`node_system_info` 和 `node_nginx_version`。具备 `nodes.ssh_test` 权限的用户可轮询本人创建的这些任务；解锁后自动创建的 `node_batch_test` 也允许 `nodes.unlock` 用户轮询本人任务。结果树只包含节点标识、SSH/Nginx 状态、系统信息及摘要，不包含凭证明文或私钥。NX-040 发布任务使用 `release_publish` 类型；详情见 [releases.md](releases.md)。

NX-050 升级和二进制回滚分别使用 `nginx_upgrade`、`nginx_rollback`。具备 `upgrade.execute` 的触发人可轮询本人任务，具备 `upgrade.read` 的触发人也可读取本人升级任务；结果树和日志沿用统一任务协议。取消限升级任务的参数获取和源码包上传阶段，回滚任务不可取消；升级记录、配置差异与回滚入口见 [upgrade.md](upgrade.md)。

NX-051 全新安装使用 `nginx_install` 任务，但权限与编译升级共用 `upgrade.*`。具备 `upgrade.execute` 的触发人可轮询和取消本人安装任务，具备 `upgrade.read` 的用户可查看安装历史和全部安装批次；取消仅允许 pending、工具检查、源码上传、解压和模块准备阶段。任务保留安装阶段、configure 参数及启动模式结果；SSH 凭证不进入参数、日志或结果树。安装成功后的完整配置同步作为同一任务的后续阶段运行，失败摘要单独保存且不改变安装成功状态。详情见 [nginx-install.md](nginx-install.md)。

NX-052 启停使用 `nginx_service_control` 任务，记录动作、`OP-YYMMDD-NNNN` 批次、目标摘要、逐节点状态、真实进度、结果树和增量日志。触发人通过启停操作台查看任务，`nginx_service.read` 可查看历史和任务日志，`nginx_service.operate` 可创建批次；统一任务中心也按启停操作权限控制本人任务。任务只保留非敏感节点信息，不保存 SSH 密码或私钥。远程动作遵守协作式取消边界。详情见 [nginx-service.md](nginx-service.md)。

NX-053 卸载使用 `nginx_uninstall` 任务，每节点独立任务、同批次号，结果树记录来源、删除范围和摘要，不保存凭证。具备 `nginx_uninstall.read` 的用户可查看历史，具备 `nginx_uninstall.execute` 的触发人可轮询和取消本人任务；取消关闭本地 SSH 客户端并在安全检查点退出，不强制中断远程命令。卸载主程序后即使可选目录或 systemd 清理失败，也会回写节点 Nginx 不可用和配置绑定 orphaned。详情见 [nginx-uninstall.md](nginx-uninstall.md)。
