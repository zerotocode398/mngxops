# ngxops

ngxops 是用于管理 Nginx 多节点运维流程的 Web 平台，提供账户与权限、SSH 凭证、节点资产、配置同步、批量发布、任务跟踪、Nginx 安装/升级/启停/卸载、系统设置和操作审计。

本文是 Linux 环境的安装和日常操作手册。功能细节见 [文档索引](docs/README.md)，开发规范见 [AGENTS.md](AGENTS.md)。

## 运行要求

- Linux x86-64 或 ARM64。
- Python 3.9 或更新版本。Python 3.6、3.7 和 3.8 不支持当前代码与依赖。
- SQLite 数据库存放在可写数据目录中。
- 当前 Web 服务按单个 worker 部署；不要使用多个 Uvicorn worker。

## 源码安装

在仓库根目录执行。为服务数据选择一个稳定且仅服务账户可读写的目录；以下使用用户主目录作为示例：

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

export NGXOPS_HOME="$HOME/.local/share/ngxops"
mkdir -p "$NGXOPS_HOME"
```

`NGXOPS_HOME` 未设置时使用启动命令所在目录。数据库、密钥、上传包和日志命令必须使用同一个数据目录。CLI 也可在子命令前使用 `--home PATH` 指定目录。

## 首次初始化

先创建数据库并应用所有已登记的数据库版本，再创建首位超级管理员：

```bash
python -m ngxops --home "$NGXOPS_HOME" database init
python -m ngxops --home "$NGXOPS_HOME" admin create
```

管理员用户名默认为 `admin`，也可作为 `create` 的参数指定，例如 `admin create ops`。密码通过交互提示输入并二次确认，输入内容不会回显。不要将密码放入命令参数、环境变量或脚本。

## 启动与停止

前台启动服务，适合首次访问和查看启动错误：

```bash
python -m ngxops --home "$NGXOPS_HOME" serve --no-reload
```

服务默认监听 `0.0.0.0:11993`。在服务器本机访问 <http://127.0.0.1:11993/login/> 登录；`/health` 用于健康检查，`/docs` 提供 API 文档。前台服务使用 `Ctrl+C` 停止。

CLI 也可在后台管理单个服务进程：

```bash
python -m ngxops --home "$NGXOPS_HOME" start
python -m ngxops --home "$NGXOPS_HOME" status
python -m ngxops --home "$NGXOPS_HOME" stop
```

`start` 不启用自动重载，也不配置开机自启。生产环境应由 systemd 或等效服务管理器负责启动、停止和重启；详见 [部署说明](docs/deployment.md)。需要远程访问时，将服务置于配置了 TLS 和访问控制的反向代理之后。

## 数据库操作

Web 服务不会自动创建或升级数据库。首次安装使用 `database init`；升级已有安装前先停止服务并备份数据目录，再执行：

```bash
python -m ngxops --home "$NGXOPS_HOME" database upgrade
```

升级命令只应用尚未执行的登记版本。含有业务表但缺少本项目迁移记录的数据库会被拒绝修改。数据库结构、迁移和备份内容见 [数据库说明](docs/database.md)。

数据目录至少包含以下内容：

| 路径 | 用途 |
|---|---|
| `db.sqlite3` | 账户、凭证密文、节点、配置、任务和审计数据 |
| `.secret_key` | 自动生成的会话签名密钥 |
| `.fernet_key` | 自动生成的 SSH 凭证加密密钥 |
| `nginx_packages/` | 上传的 Nginx 源码包和模块包 |
| `logs/` | 应用与 CLI 运行日志 |

备份前停止服务：前台 `serve` 使用 `Ctrl+C` 停止，CLI `start` 启动的服务使用 CLI `stop`，由 systemd 管理的服务停止对应 unit。数据库和两个密钥文件必须一起备份；缺少凭证加密密钥将无法解密既有凭证。

```bash
tar -czf "$HOME/ngxops-backup-$(date +%F-%H%M%S).tar.gz" -C "$(dirname "$NGXOPS_HOME")" "$(basename "$NGXOPS_HOME")"
```

## 管理员密码

修改密码可以在登录后访问 `/password/change/`。无法登录时，可在服务器本地重置超级管理员密码：

```bash
python -m ngxops --home "$NGXOPS_HOME" admin reset
```

用户名默认为 `admin`，可在 `reset` 后指定其他超级管理员用户名。新密码通过隐藏输入并再次确认；操作会解除登录失败锁定并撤销该用户已有会话。此命令不要求旧密码，应限制对数据目录和服务器终端的访问。

完整账户规则见 [账户说明](docs/accounts.md)。

## 命令行参数

查看所有命令及参数：

```bash
python -m ngxops --help
python -m ngxops serve --help
python -m ngxops start --help
python -m ngxops stop --help
python -m ngxops database --help
python -m ngxops admin --help
```

常用服务选项为 `--host`、`--port` 和 `--log-level`。前台 `serve` 支持 `--reload`/`--no-reload`；后台 `start` 不支持自动重载。`stop` 支持 `--timeout`。全局 `--home PATH` 放在命令前，例如 `python -m ngxops --home /srv/ngxops database upgrade`。

## 配置环境变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `NGXOPS_HOME` | 当前工作目录 | SQLite、密钥、上传包和日志所在目录 |
| `NGXOPS_HOST` | `0.0.0.0` | 默认监听地址，可由 CLI 参数覆盖 |
| `NGXOPS_PORT` | `11993` | 默认监听端口，可由 CLI 参数覆盖 |
| `NGXOPS_DEBUG` | 源码运行开启；单文件运行关闭 | 调试模式 |
| `NGXOPS_RELOAD` | 源码运行开启；单文件运行关闭 | 前台服务自动重载默认值 |
| `NGXOPS_LOG_LEVEL` | `info` | 日志级别 |
| `NGXOPS_SECRET_KEY` | 自动生成并保存到 `.secret_key` | 会话与 CSRF 签名；固定部署密钥可通过此变量指定 |
| `NGXOPS_HTTPS` | `0` | 为会话与 CSRF Cookie 启用 Secure 属性 |
| `NGXOPS_CSRF_TRUSTED_ORIGINS` | 空 | 逗号分隔的额外可信来源 |
| `NGXOPS_RELEASE_BACKUP_DIR` | `/opt/ngxops/backups` | 发布备份目录的默认值，可由系统设置调整 |
| `NGXOPS_UPGRADE_PACKAGE_MAX_SIZE_MB` | `20` | 系统设置未初始化时的包上传限制，单位 MiB |

正式部署时，通过 systemd 的 `Environment=`、环境文件或等效方式设置变量，并确保迁移、管理员命令和服务进程读取同一组配置。

## Linux 单文件程序

在目标 Linux 主机或同架构构建机上创建 Python 3.9+ 环境并安装 `requirements.txt`，然后执行：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python tools/build_binary.py --print-target
python tools/build_binary.py
```

产物位于 `dist/`：x86-64 为 `ngxops-linux-amd64`，ARM64 为 `ngxops-linux-arm64`。构建不支持跨 CPU 架构或跨操作系统；Linux 产物依赖系统 glibc，建议在计划支持的最旧发行版上构建。数据库、密钥、包和日志仍保存在独立的数据目录，不会打入可执行文件。

使用二进制执行初始化和管理命令：

```bash
export NGXOPS_HOME="$HOME/.local/share/ngxops"
./dist/ngxops-linux-amd64 database init
./dist/ngxops-linux-amd64 admin create
./dist/ngxops-linux-amd64 start
./dist/ngxops-linux-amd64 status
```

使用 `./dist/ngxops-linux-amd64 --help` 查看参数。停止、升级数据库和重置密码分别使用 `stop`、`database upgrade` 和 `admin reset`。ARM64 主机需将文件名替换为 `ngxops-linux-arm64`。打包细节见 [发行说明](docs/packaging.md)。

## 其他文档

- [部署说明](docs/deployment.md)：服务账户、反向代理、环境配置和运行边界。
- [文档索引](docs/README.md)：账户、节点、凭证、配置、发布、任务及 Nginx 运维模块说明。
