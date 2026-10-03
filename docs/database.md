# SQLite 数据层约定

## 持久层

- 使用 SQLAlchemy 2.x 同步 Engine/Session，连接 SQLite 文件 `db.sqlite3`；文件位置为 `Settings.database_path`，沿用 `NGXOPS_HOME` 数据目录约定。
- 应用工厂只创建惰性 Engine，不打开连接、不建表、不改 schema。服务启动准备可写数据目录、会话签名密钥和凭证加密密钥；仅当数据库文件已存在时检查任务表并恢复遗留任务。数据库升级由显式命令执行。
- FastAPI 请求通过 `get_session` 各自创建并关闭 Session。后台线程必须通过 `session_scope` 新建自己的 Session；不得跨请求/线程共享 Session。
- 同步 Session 应在同步路由/服务中使用；`async` 路由不得直接执行阻塞式 ORM 调用，需要交由线程池或同步路由处理。
- Session 不自动提交。写操作在服务层以事务上下文或显式 `commit/rollback` 明确边界；异常回滚，Session 最终关闭。
- SQLite 每条连接启用 `PRAGMA foreign_keys = ON` 和 30 秒 `busy_timeout`。SQLite 同时只允许一个写事务；写事务应尽量短，锁等待超时后由接口按可恢复错误处理。暂不启用 WAL，避免在没有数据备份/性能基线前改变数据库文件运行模式。

## 初始化与升级

- 初始化/升级入口：`python -m ngxops.database upgrade`。Web 启动绝不自动迁移；执行升级前应停止服务并备份数据库。
- 迁移通过 `ngxops_schema_migrations` 记录版本，使用 `BEGIN IMMEDIATE` 串行化升级，并在同一事务内执行迁移及写入版本记录。当前版本 14 增加系统设置表；迁移只在显式升级命令中执行。
- 禁止 `metadata.create_all()`、自动重建表、删除库或启动时批量改写数据。表结构变化必须加入有序迁移，保留旧数据并给出回滚/备份说明。
- 若数据库含有业务表但没有本项目的迁移台账，升级命令拒绝写入。特别是原 `mngxops` SQLite 文件不会被自动接管；需要先对照完整 schema 并显式登记兼容基线。空库与已有库路径不得混用。
- 原迁移计划引用的 `mngxops/docs/fastapi-migration-baseline.md` 当前不存在，因此本项目以 `mngxops/apps/*/models.py`、全部 Django migrations 和模块文档交叉确认，不把缺失报告视为已经核实的 schema。

## 原项目数据约束清单

下列是从当前模型代码确认的约束。后续映射旧数据或创建兼容迁移时必须保持；不在模型中声明的唯一约束不得凭空增加。

| 数据 | 必须保持的规则 | 原项目实现位置 |
|---|---|---|
| 用户与权限 | `auth_user.username`、`PermissionItem.code`、`UserGroup.name`、`UserTeam.name` 唯一；`UserProfile.user_id` 唯一 | `apps/users/models.py`、Django auth 用户模型 |
| 认证基表 | `auth_user` 保留 Django User 的核心身份字段及用户名唯一约束；登录名长度 150、密码摘要长度 128；角色和个人资料由后续阶段迁移，登录锁定单独存储 | `apps/users/models.py`、Django auth 用户模型 |
| 登录锁定/登录日志 | `ngxops_login_failure_states.user_id` 外键级联删除且失败计数非负；`ngxops_login_logs` 约束状态和失败原因枚举，并按创建时间、用户名建索引 | `apps/accounts/login_lock.py`、`apps/audit/models.py` |
| RBAC | 权限编码、角色名和用户组名唯一；角色/用户组/个人权限关系使用复合主键；删除关联用户、角色、用户组或权限项时级联清理关系 | `apps/users/models.py`、`apps/users/perm_defs.py` |
| 服务端会话 | `ngxops_sessions.session_key` 唯一；可选 `user_id` 外键删除级联；过期时间有索引；会话 JSON 不含密码或凭证明文 | `django.contrib.sessions`、`apps/accounts/views.py` |
| SSH 凭证 | `(name, created_by)` 唯一；用户删除时级联删除；密码/私钥保存 Fernet 密文；认证方式与最近测试结果受枚举约束 | `apps/credentials/models.py` |
| 节点 | `NodeGroup.name`、`ip` 唯一；`is_deleted` 有索引；凭证与删除人可置空；软删除保留历史主键 | `apps/nodes/models.py` |
| 节点组成员 | `(node_id, group_id)` 复合主键；节点或组物理删除时级联移除关联 | `apps/nodes/models.py` |
| 节点同步路径 | `node_id` 唯一；物理删除节点时级联移除 | `apps/configs/models.py` |
| 配置绑定 | `(config_id, node_id)` 唯一；配置或节点删除级联 | `apps/configs/models.py` |
| 配置标签 | 标签名不唯一；用户删除级联；默认路径、模板、来源和描述只属于标签元数据 | `apps/configs/models.py` |
| 配置同步设置 | `node_id` 唯一；节点删除时按原规则处理 | `apps/configs/models.py` |
| 绑定版本 | `(binding_id, version)` 唯一；绑定删除级联 | `apps/configs/models.py` |
| 源码/第三方模块包 | 源码包 `(version, uploaded_by)`、第三方包 `(name, version, uploaded_by)` 唯一 | `apps/upgrade/models.py` |
| Nginx 升级记录 | 每条升级运行关联一个统一任务；节点或源码包删除时置空，回滚源记录删除时关联置空；保存 configure 参数快照和旧二进制备份路径，不保存凭证 | `apps/upgrade/models.py` |
| Nginx 安装记录 | 每条单节点安装运行关联一个统一任务；节点或源码包删除时置空；保存安装参数、阶段、批次、配置路径和自动同步摘要，不保存凭证 | `apps/nginx_install/models.py` |
| Nginx 卸载记录 | 每条单节点卸载运行关联一个统一任务；节点删除时置空；保存节点身份、批次、安装来源、包名、prefix 和清理选择，不保存凭证 | `apps/nginx_uninstall/models.py` |
| 发布与运维任务 | 批次号有索引但不是唯一键；节点、操作者、包、Task Center 外键按各模型的 `CASCADE` / `SET_NULL` 规则处理 | `apps/releases/models.py`、`apps/upgrade/models.py`、`apps/nginx_install/models.py`、`apps/nginx_uninstall/models.py` |
| 系统设置 | `SystemSetting.key` 唯一；修改人可置空 | `apps/settings/models.py` |
| 审计 | 操作人 ID 可空并在删除账户时置空；保留操作人名称快照、任务 ID 和批次号索引 | `apps/audit/models.py` |

## 统一任务表

- `ngxops_tasks` 保存操作类型、`pending/running/success/failed/cancelled` 状态、进度、步骤说明、JSON 结果树、批次、目标摘要、触发人及开始/完成时间。状态和进度由 SQLite `CHECK` 约束；触发用户删除时置空。
- `ngxops_task_logs` 按任务追加日志，以 `(task_id, id)` 支持增量轮询；删除任务时级联删除日志。任务结果树上限 1 MiB，单条日志上限 4,000 字符。
- 数据库只保存状态与业务结果，不保存执行函数或执行参数。业务模块通过 `ngxops.tasks.executor.create_task` 注册可调用任务；后台执行函数必须使用 `TaskContext` 更新进度、结果、日志，并在可取消的业务检查点调用 `check_cancelled()`。
- 任务启动和每次写入使用独立 SQLAlchemy Session。应用启动只在数据库文件已存在时检查任务表；有表时将遗留 `pending/running` 任务标成 `failed` 并记为进程重启中断，不会自动重跑，也不会伪报成功。数据库未初始化或仅升级到版本 1 时不创建表、不执行迁移。
- SQLAlchemy 迁移版本 2 使用 `ngxops_schema_migrations` 管理；使用前仍须显式执行 `python -m ngxops.database upgrade`。

## 账户表

- `ngxops_login_failure_states` 按用户主键保存连续失败次数和临时锁定时间；用户删除时级联清除。成功登录、改密和管理员提前解锁都会清除状态。
- `ngxops_login_logs` 只记录用户名、IP、User-Agent、成功/失败、失败原因和时间；表内没有密码、会话句柄或凭证明文。
- 登录锁定阈值默认 5 次、时长 15 分钟；由 `auth.login_fail_lock_count` 与 `auth.login_fail_lock_minutes` 控制。

## RBAC 表

- `ngxops_permission_items` 以 `code` 唯一约束保存 `resource.action` 权限项。NX-011 迁移按 `ngxops.rbac.permission_defs` 中的定义写入，不由 Web 启动过程补种。
- `ngxops_roles`、`ngxops_user_teams` 分别唯一约束角色名和用户组名；创建人外键按原模型在用户删除时级联删除。
- `ngxops_user_profiles` 每个账户最多一行，保存备注。`ngxops_profile_roles`、`ngxops_profile_permissions`、`ngxops_team_members`、`ngxops_team_roles` 和 `ngxops_role_permissions` 均以双方 ID 组成主键并使用级联外键。
- RBAC 解析顺序：超级管理员全允许；用户直授权限始终生效；若用户存在个人角色，仅合并个人角色；没有个人角色时才合并所有所属用户组的角色权限；最终拒绝。个人角色最多 3 个由管理表单校验，团队角色不占此上限。
- 页面路由、成员 API、字段校验和与原实现的权限继承差异见 [rbac.md](rbac.md)。

## SSH 凭证表

- 迁移版本 5 创建 `ngxops_credentials`，保留 `(name, created_by)` 唯一约束及创建用户删除时级联清理。认证类型限 `password/key`，最近测试状态限 `success/partial/failed/unknown`。
- 迁移版本 6 创建 `ngxops_nodes`、`ngxops_node_groups`、成员关联及 `ngxops_node_sync_settings`。节点 IP 在活跃与软删除记录间共用唯一约束；凭证删除时节点外键置空，节点软删除保留主键、凭证与分组历史。
- 迁移版本 7 为 `ngxops_tasks` 增加 `subject_type/subject_id` 及索引，供业务模块稳定定位关联资源任务；该关联只保存资源类型和数据库 ID，不保存凭证材料。
- 迁移版本 8 创建 `ngxops_configs`、`ngxops_config_bindings` 和 `ngxops_binding_versions`。配置标签名称不唯一；绑定保留 `(config_id, node_id)` 唯一约束和配置/节点删除级联；版本保留 `(binding_id, version)` 唯一约束及绑定删除级联。标签来源不增加原模型未声明的数据库 CHECK；绑定同步状态拒绝已下线的 `conflict`、`syncing` 值。
- 迁移版本 9 创建 `ngxops_config_sync_settings`，每节点至多一条配置发现路径设置；节点删除时级联，更新人删除时置空。它与 v6 的 `ngxops_node_sync_settings`（节点表格导入/导出路径）保持独立，避免两种工作流互相覆盖。
- `password` 与 `private_key` 列只保存 Fernet 密文或空字符串。应用启动时在数据目录读取或创建 `.fernet_key`；它与 `.secret_key` 分离，必须与数据库一起备份，丢失后旧凭证无法解密。
- 迁移版本 10 创建 `ngxops_nginx_source_packages`、`ngxops_nginx_module_packages` 和 `ngxops_nginx_upgrade_runs`。包记录保留上传人级版本唯一约束；升级记录关联统一任务，记录节点/源码包快照、configure 参数、运行阶段、备份路径及回滚时间。源码和离线模块归档存放在数据目录 `nginx_packages/`，必须与数据库共同备份。
- 迁移版本 11 创建 `ngxops_nginx_install_runs`，每个统一任务对应一个安装快照；节点和源码包删除时外键置空，任务删除时级联清理。快照包含源码版本、节点身份、configure 参数、模块 JSON、监听端口、执行阶段、Nginx 路径和配置同步结果，不包含 SSH 凭证。迁移为 `nginx_install.read/create` 补种缺失权限项，以兼容早期 RBAC 种子。
- 迁移版本 12 创建 `ngxops_nginx_uninstall_runs`，每个统一任务对应一个卸载快照；节点删除时外键置空，任务删除时级联清理。快照包含来源、软件包归属、prefix、批次、节点身份和清理选项，不包含 SSH 凭证。迁移补种 `nginx_uninstall.read/execute` 并将现有 `nodes.read`、`nodes.update` 的角色及个人直授分别复制到卸载查看和执行权限。
- 迁移版本 13 创建 `ngxops_audit_logs`，保存操作人 ID/名称快照、模块、动作、来源 IP、结果、摘要、可选任务 ID、批次号和时间；用户删除时仅清空 ID，不删除历史。任务 ID 不设外键，以便任务保留策略清理后操作记录仍保留。
- 迁移版本 14 创建 `ngxops_system_settings`，以唯一 key 保存已接线设置的字符串值、类型、分组和展示元数据；修改人删除时置空，分组/排序字段有索引。默认值按需补齐且不覆盖已有 value，清理与运行设置见 [settings.md](settings.md)。
- NX-052 启停批次直接复用 `ngxops_tasks` 与 `ngxops_task_logs`，不新增业务表或迁移；动作存放在 `target_configs`，`source_batch` 保存批次号，目标摘要和结构化结果分别存放在任务摘要字段与结果树中。
- 凭证迁移不会读取或接管 mngxops 数据库中的旧记录。升级仍须显式停止服务、备份后执行 `python -m ngxops.database upgrade`。

SQLite 兼容时还需保留原表名、列名、整数主键、空值语义、布尔整数和 Django 日期时间文本格式。完整字段及关系以 `mngxops/apps/*/models.py` 与对应 `migrations/` 为准；本文件只列跨模块关键约束。

当前 `auth_user` 是后续账户与 RBAC 阶段共用的最小身份表，不等同于完整 Django auth schema。`ngxops_sessions` 存放 JSON 会话，与 Django `django_session.session_data` 的签名/序列化格式不兼容；未经 schema 与数据核对不得直接复用原项目数据库。
