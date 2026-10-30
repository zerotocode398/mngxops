# Linux 部署

## 运行边界

- Python 3.9 或更新版本；依赖版本见 [requirements.txt](../requirements.txt)。
- SQLite 数据库位于 `NGXOPS_HOME/db.sqlite3`。应用启动不会自动创建表或应用迁移。
- 任务执行器、包管理锁和取消句柄驻留在单个进程中，生产环境固定使用单个 Uvicorn worker。
- 进程重启时遗留的 `pending`/`running` 任务会标记为失败，不自动重跑。已发送到远程节点的命令可能仍在执行。
- `/health` 仅检查 Web 进程是否可响应，不验证数据库升级状态、凭证解密或远程节点连通性。
- 浏览器页面依赖 Bootstrap、Bootstrap Icons 和 jQuery CDN。隔离网络需为这些静态资源提供可访问来源。
- Nginx 运维功能通过 SSH 连接 Linux 节点；安装、升级、启停和卸载可能要求远程 root 或免密 sudo。

## 安装与初始化

在部署主机准备 Python 环境和稳定的数据目录。运行服务的系统账户必须能够读写 `NGXOPS_HOME`：

```bash
python3 --version
python3 -m venv /opt/ngxops/.venv
source /opt/ngxops/.venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

export NGXOPS_HOME=/var/lib/ngxops
mkdir -p "$NGXOPS_HOME"
python -m ngxops database init
python -m ngxops admin create
```

命令会通过隐藏提示交互输入管理员密码。服务启动后访问 `/login/` 登录。CLI 数据库、管理员和服务命令必须使用相同的 `NGXOPS_HOME`。

## 使用 systemd

生产环境可由 systemd 管理前台 CLI 进程。按实际服务用户、安装目录、数据目录和监听端口调整配置：

```ini
[Unit]
Description=ngxops Web service
After=network.target

[Service]
Type=simple
User=ngxops
Group=ngxops
WorkingDirectory=/opt/ngxops
Environment=NGXOPS_HOME=/var/lib/ngxops
Environment=NGXOPS_DEBUG=0
Environment=NGXOPS_RELOAD=0
ExecStart=/opt/ngxops/.venv/bin/python -m ngxops serve --host 127.0.0.1 --port 11993 --no-reload
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

服务应只启动一个进程。上面的 systemd 示例显式将监听地址限制为本机；需要对外提供页面时，将反向代理指向 `127.0.0.1:11993`，配置 TLS、防火墙和可信访问来源；不要直接把管理端口开放到不可信网络。若由反向代理终止 HTTPS，将 `NGXOPS_HTTPS=1` 并正确配置 `NGXOPS_CSRF_TRUSTED_ORIGINS`。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `NGXOPS_HOME` | 当前工作目录 | 数据、密钥、包和日志目录；CLI 也可使用全局 `--home PATH` |
| `NGXOPS_HOST` | `0.0.0.0` | 默认监听地址 |
| `NGXOPS_PORT` | `11993` | 默认监听端口 |
| `NGXOPS_DEBUG` | 源码运行开启；单文件运行关闭 | 调试模式，生产环境关闭 |
| `NGXOPS_RELOAD` | 跟随调试模式 | 前台服务自动重载，生产环境关闭 |
| `NGXOPS_LOG_LEVEL` | `info` | 日志级别，可用 `--log-level` 覆盖 |
| `NGXOPS_SECRET_KEY` | 自动生成并保存至 `.secret_key` | 会话与 CSRF Cookie 签名 |
| `NGXOPS_HTTPS` | `0` | HTTPS 终止后启用 Secure Cookie 时设为 `1` |
| `NGXOPS_CSRF_TRUSTED_ORIGINS` | 空 | 逗号分隔的可信来源，如 `https://ops.example.com` |
| `NGXOPS_RELEASE_BACKUP_DIR` | `/opt/ngxops/backups` | 远程发布备份目录默认值 |
| `NGXOPS_UPGRADE_PACKAGE_MAX_SIZE_MB` | `20` | 设置表不可用时的包上传限制，单位 MiB |

安装和升级后可通过系统设置调整发布备份目录与上传大小。设置范围和生效时间见 [settings.md](settings.md)。

## 升级与备份

升级应用代码前停止服务并备份完整数据目录。部署新代码或新二进制后，以相同数据目录运行：

```bash
export NGXOPS_HOME=/var/lib/ngxops
python -m ngxops database upgrade
```

随后由服务管理器启动新版本。需要重设密码时，单独运行 `python -m ngxops admin reset`。数据库升级不会在 Web 启动时自动执行；发现未知结构或无效版本记录时会拒绝修改。

数据目录包含 `db.sqlite3`、`.secret_key`、`.fernet_key`、`nginx_packages/` 和 `logs/`。停止服务后一起备份。恢复时保持目录内容完整并设置正确的属主和权限。详细数据库约束见 [database.md](database.md)，构建与命令行见 [packaging.md](packaging.md)。

## 业务操作边界

1. 添加 SSH 凭证并关联节点，完成 SSH 和 Nginx 状态探测。
2. 从节点发现配置并同步到平台；配置同步不会向远端发布。
3. 在发布中心选择配置绑定和版本，确认后执行发布；发布历史支持版本回滚。
4. 根据节点状态执行 Nginx 安装、编译升级、启停或卸载。

异步任务的进度、状态和结果在任务中心查看。进程异常退出后应先检查远程节点实际状态，再决定是否重新操作。
