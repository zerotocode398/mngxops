# 系统设置与数据保留

## 页面与权限

`/settings/` 按功能分组显示已接线设置。查看要求 `settings.read`；只有超级管理员可以修改。普通有权用户看到只读值。设置表单通过 `POST /api/settings/group` 保存，使用全局会话与 CSRF 校验。

读取 API：

| 方法与路径 | 权限 | 行为 |
|---|---|---|
| `GET /api/settings/group?group=节点管理` | `settings.read` | 返回该组已登记项目的值、说明和校验范围；未知分组返回空列表 |
| `GET /api/settings/all` | `settings.read` | 返回已登记设置的键值映射 |
| `POST /api/settings/group` | 超级管理员与 CSRF | 原子校验并保存分组值，返回实际变更的 key 列表 |

保存请求格式为 `{"group":"节点管理","values":{"node.batch_max_count":"3"}}`。不支持的 key、跨分组 key、空必填值、非整数和越界整数会拒绝整组写入。每个整数预置项的范围由代码元数据提供，不接受任意动态设置。操作审计只记录分组、变更 key 和数量，不记录设置值。

设置读取在数据库行缺失时使用代码中的预置值。页面或设置 API 读取时会补齐缺失行并更新展示元数据，但保留已有 value；这使新版本可补充默认项而不覆盖管理员保存的值。

`NGXOPS_RELEASE_BACKUP_DIR` 与 `NGXOPS_UPGRADE_PACKAGE_MAX_SIZE_MB` 仅在 v14 设置表不可用时作为兼容默认值；v14 迁移完成后，相应系统设置为运行时权威值。

## 已接线设置

| 分组 | key | 默认值 | 生效范围 |
|---|---|---:|---|
| 仪表盘 | `dashboard.recent_tasks_count` | 20 | 仪表盘及升级、安装、启停、卸载首页的最近任务列表；刷新页面后生效 |
| 节点管理 | `node.batch_max_count` | 3 | 节点探测、锁定、解锁、删除，凭证关联节点探测，配置同步、发布、升级、安装、启停和卸载的后端上限及并发数；页面勾选上限刷新后生效 |
| 节点管理 | `node.ssh_connect_timeout` | 10 秒 | 节点探测、凭证测试、配置发现/同步、发布、升级、安装、启停和卸载的下次 SSH 连接 |
| 节点管理 | `node.ssh_default_port` | 22 | 新建节点和导入时的默认端口，不改写已有节点 |
| 节点管理 | `node.detect_retries` | 1 次 | 上述 SSH 连接失败后的额外重试次数，不含首次连接 |
| 配置管理 | `config.discover_max_depth` | 3 层 | 下次配置发现或同步 |
| 配置管理 | `config.default_nginx_path` | `/etc/nginx/nginx.conf` | 新节点或没有已保存路径的配置同步 |
| 配置管理 | `config.default_nginx_bin` | `/usr/sbin/nginx` | 新建/导入节点没有填写 Nginx 二进制路径时 |
| 发布管理 | `release.backup_dir` | `/opt/app/mascloud/ansible/mngxops` | 下次发布；每个节点写入其 hostname 子目录 |
| 登录 | `auth.login_fail_lock_count` | 5 次 | 下次登录；允许 3 至 30 次，不能关闭 |
| 登录 | `auth.login_fail_lock_minutes` | 15 分钟 | 下次登录；允许 1 至 1440 分钟 |
| 系统 | `system.task_progress_poll_interval` | 2 秒 | 通用任务中心、节点、配置、发布、升级、安装、启停和卸载任务进度；刷新页面后生效 |
| 系统 | `system.dashboard_refresh_interval` | 30 秒 | 仪表盘统计卡片自动刷新；允许 5 至 3600 秒，刷新页面后生效 |
| 系统 | `system.retention_task_center_days` | 90 天 | 非发布/升级终态任务与关联日志；0 关闭清理 |
| 系统 | `system.retention_release_history_days` | 90 天 | 发布/回滚终态任务与关联日志；0 关闭清理 |
| 系统 | `system.retention_audit_log_days` | 90 天 | 操作审计记录；0 关闭清理 |
| 系统 | `system.retention_login_log_days` | 90 天 | 登录记录；0 关闭清理 |
| 系统 | `system.retention_upgrade_task_days` | 90 天 | Nginx 升级/回滚终态任务及升级运行记录；0 关闭清理 |
| Nginx 升级 | `upgrade.default_work_dir` | `/tmp/nginx-upgrade` | 升级和安装向导的默认远程工作目录 |
| Nginx 升级 | `upgrade.make_jobs_default` | 4 | 升级和安装向导的默认 make 并行数 |
| Nginx 升级 | `upgrade.package_max_size_mb` | 20 MB | 源码和第三方模块包上传大小限制；保存后立即生效 |
| 安装管理 | `install.default_user` | `root` | 新打开的安装向导默认用户 |
| 安装管理 | `install.default_group` | `root` | 新打开的安装向导默认组 |
| 安装管理 | `install.default_prefix` | `/opt/app` | 新打开的安装向导默认安装路径 |
| 安装管理 | `install.default_listen_port` | 80 | 新打开的安装向导默认监听端口 |

## 数据保留

认证用户请求每天最多启动一次后台清理；清理在独立线程及数据库 Session 中执行，不占用 FastAPI 事件循环。每日日期锁按 UTC 日期记录在当前进程中，部署按单个 Uvicorn worker 运行。清理以记录创建时间和当前 UTC 时间计算截止点，保留天数为 0 时跳过对应类别。

统一任务表将原项目分开的任务中心、发布和升级任务分类清理。任务中心策略排除发布/回滚和升级/回滚类型；发布策略只处理发布/回滚；升级策略只处理升级/回滚。所有策略都跳过 `pending`、`running`。升级记录还会排除 `pending`、`fetching_config`、`uploading_package`、`downloading_modules`、`configuring`、`compiling`、`backing_up`、`replacing_binary`、`upgrading` 和 `verifying` 阶段，即使任务状态已进入终态也不删除运行中的升级阶段。删除任务时由 SQLite 外键级联清除任务日志和关联安装/卸载/升级运行快照；审计记录没有任务外键，因此由自己的保留策略独立清理。

可使用 `python -m ngxops.settings purge` 立即执行一次清理。命令使用当前 `NGXOPS_HOME` 数据库，不自动升级 schema；执行前应确认目标数据库并备份。输出为各类别实际删除行数的 JSON 对象。

数据库迁移版本 14 新建 `ngxops_system_settings`。首次部署和升级仍需停止服务、备份后显式运行 `python -m ngxops.database upgrade`；见 [database.md](database.md)。
