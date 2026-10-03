# 启动与部署

## 运行边界

- 服务端要求 Python 3.9，依赖版本见 [requirements.txt](../requirements.txt)。
- 当前数据层为 SQLite，数据库文件固定为数据目录中的 `db.sqlite3`。应用启动不会创建表或自动应用迁移。
- 任务执行器、包管理锁和取消句柄驻留在单个进程中。生产部署只运行一个 Uvicorn worker，不启用多 worker；任务状态写入数据库，但进程重启后遗留的 `pending`/`running` 任务会标记为失败，不会自动重跑。已发往远端的 SSH 命令可能继续运行。
- `/health` 只表示 Web 进程可响应，不检查数据库迁移状态、凭证解密或远程节点。
- 页面依赖 jsDelivr 提供的 Bootstrap、Bootstrap Icons，以及 `code.jquery.com` 提供的 jQuery；浏览器需能访问这些 CDN。
- 运维目标为 Linux 主机和 SSH。安装、升级、启停及卸载所需的系统目录、包管理器和 systemd 操作，可能要求远端 root 或免密 sudo。

## 首次初始化

先为 ngxops 选择一个独立、可写的数据目录。不要把同级 Django 项目的数据库目录直接设为 ngxops 数据目录；ngxops 不会自动迁移或接管 mngxops 的表和 session。没有 ngxops 迁移标记的非空 SQLite 数据库会被迁移命令拒绝修改。

在 PowerShell 中，以下命令以项目旁的专用数据目录为例。首次初始化、管理员命令和 Web 服务必须使用相同的 `NGXOPS_HOME`：

```powershell
$env:NGXOPS_HOME = 'D:\ngxops-data'
..\venv3\Scripts\activate
python -m pip install -r requirements.txt
python -m ngxops.database upgrade
python -m ngxops admin create
python run_server.py
```

数据库升级会应用当前代码登记的全部迁移（当前为 `v14`）。升级已有环境前，先停止 Web 服务并备份数据目录，再运行迁移。首个管理员命令仅在数据库完成 `v1-v3` 迁移且没有用户时可用；密码通过隐藏提示输入，并遵守账户密码规则。

开发环境默认监听 `127.0.0.1:8000`，并启用调试和自动重载。成功启动后可访问 `/login/`、`/docs` 和 `/openapi.json`。初始化数据库和首个管理员后才能登录。

## 配置

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `NGXOPS_HOST` | 源码运行 `127.0.0.1`；冻结运行 `0.0.0.0` | Uvicorn 监听地址；也可用 `--host` 覆盖 |
| `NGXOPS_PORT` | 源码运行 `8000`；冻结运行 `11993` | Uvicorn 监听端口；也可用 `--port` 覆盖 |
| `NGXOPS_DEBUG` | 源码运行开启；冻结运行关闭 | FastAPI 调试模式 |
| `NGXOPS_RELOAD` | 跟随调试模式 | Uvicorn 自动重载；生产关闭 |
| `NGXOPS_LOG_LEVEL` | 调试时 `debug`，否则 `info` | Uvicorn 日志级别；也可用 `--log-level` 覆盖 |
| `NGXOPS_HOME` | 当前工作目录 | SQLite、上传文件、密钥和日志所在的可写数据目录；CLI 也支持 `--home` |
| `NGXOPS_SECRET_KEY` | 未设置时在数据目录生成 `.secret_key` | CSRF Cookie 签名密钥；固定配置或备份该文件以保留密钥 |
| `NGXOPS_HTTPS` | `0` | 设为 `1` 后会话与 CSRF Cookie 使用 Secure 属性；仅在外部请求使用 HTTPS 时启用 |
| `NGXOPS_CSRF_TRUSTED_ORIGINS` | 空 | 逗号分隔的额外可信来源，例如 `https://ops.example.com` |
| `NGXOPS_RELEASE_BACKUP_DIR` | `/opt/app/mascloud/ansible/mngxops` | `release.backup_dir` 的兼容默认值；迁移 v14 后由系统设置控制远程备份根目录 |
| `NGXOPS_UPGRADE_PACKAGE_MAX_SIZE_MB` | `20` | 设置表不可用时的兼容上传限制，取值 1 至 1024 MiB；迁移 v14 后由 `upgrade.package_max_size_mb` 控制，系统设置范围为 1 至 2048 MiB |

`run_server.py` 接受 `--host`、`--port`、`--log-level`、`--reload` 和 `--no-reload`。生产环境应设置 `NGXOPS_DEBUG=0`、`NGXOPS_RELOAD=0`，并按实际 TLS 终止方式配置 Secure Cookie。部署在反向代理后时，`NGXOPS_CSRF_TRUSTED_ORIGINS` 使用浏览器访问的完整来源。

`NGXOPS_*` 是当前配置前缀；为兼容旧部署，同名 `MNGXOPS_*` 变量仍可用，二者同时设置时 `NGXOPS_*` 优先。单文件程序的构建、英文 CLI、后台 `start/stop/status`、数据库初始化、管理员密码重置和日志位置见 [packaging.md](packaging.md)。源码和单文件程序未指定数据目录时，都使用启动命令所在的当前工作目录。

迁移 `v14` 后，备份目录和上传大小以系统设置页中的值为准。运行参数、范围和生效时机见 [settings.md](settings.md)。

## 数据与密钥

数据目录包含：

- `db.sqlite3`：账户、权限、节点、配置、任务、审计和设置数据。
- `.secret_key`：未配置 `NGXOPS_SECRET_KEY` 时自动生成的 CSRF 签名密钥。
- `.fernet_key`：Web 服务首次启动时自动生成的 SSH 凭证加密密钥。
- `nginx_packages/`：Nginx 源码包和第三方模块归档。

备份和恢复时必须将数据库、两个密钥文件和 `nginx_packages/` 作为一组处理。丢失 `.fernet_key` 后，已有 SSH 密文无法解密；更换 `.secret_key` 会使现有 CSRF 签名失效。备份时先停服务以获得一致的数据副本。切换 `NGXOPS_HOME` 会切换数据库、密钥和归档位置，升级、管理员初始化和服务启动都要指向同一路径。

SQLite 结构和迁移保护见 [database.md](database.md)。平台不会将 Django session 序列化数据转换为 ngxops 会话；在建立兼容 schema 基线和接管流程前，不应对原 mngxops 数据库执行 ngxops 迁移。

## 升级与清理

代码升级前停止服务、备份数据目录，然后在新代码目录和原 `NGXOPS_HOME` 下执行：

```powershell
python -m ngxops.database upgrade
python run_server.py --no-reload
```

迁移由显式命令执行，Web 启动不会自动更改 schema。若迁移发现未知表或不匹配的迁移版本，会拒绝继续，应先核对数据库来源和备份。手动执行数据保留清理使用 `python -m ngxops.settings purge`；该命令不会自动升级 schema，清理范围和跳过条件见 [settings.md](settings.md)。

## 业务闭环

1. 创建或导入 SSH 凭证，关联到节点并完成 SSH/Nginx 探测；SSH 在线和 Nginx 可用是两个独立状态。
2. 从符合门禁的节点发现并同步配置。同步将远程配置拉入平台，不会发布回远端。
3. 在发布中心选择绑定和版本，检查预览后执行发布；发布历史支持配置版本回滚。发布前备份目录是远程节点路径。
4. 按节点当前状态选择生命周期操作：没有可用 Nginx 时使用全新安装；已有 Nginx 时使用编译升级、启停或卸载。卸载没有平台内回滚；升级回滚恢复备份的二进制，发布回滚恢复配置版本。

入口、权限门禁、失败处理、结果和界面行为见 [credentials.md](credentials.md)、[nodes.md](nodes.md)、[configs.md](configs.md)、[releases.md](releases.md) 及各 Nginx 生命周期文档。

## 已知限制

- 单进程、单 worker 部署；不支持多 worker 共享任务队列、跨进程取消或自动接管执行中的远端命令。
- 进程重启时遗留活动任务转为失败，不会重新执行。重试前应先检查远端实际状态，避免重复安装、发布或卸载。
- 数据库为 SQLite；迁移必须显式运行。ngxops 与 Django 的表结构、会话序列化不兼容，没有自动从 mngxops 导入用户、凭证或业务数据的流程。
- Web 页面使用外部 CDN 资源；隔离网络中的浏览器需要能访问 CDN，或部署方自行提供等价的本地静态资源。
- 本项目文档和本地模拟任务不替代真实节点验收；SSH 权限、发行版差异、包管理器行为和 systemd 能力需要在目标环境核对。
