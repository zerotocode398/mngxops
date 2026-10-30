# Linux 单文件发行与 CLI

## 构建

在目标 Linux 操作系统和 CPU 架构上安装 Python 3.9+，创建隔离环境并安装项目依赖：

```bash
python3 -m venv .venv-build
source .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python tools/build_binary.py --print-target
python tools/build_binary.py
```

构建产物位于 `dist/`：

| Linux 架构 | 文件名 |
|---|---|
| x86-64 / amd64 | `ngxops-linux-amd64` |
| ARM64 / aarch64 | `ngxops-linux-arm64` |

构建需使用与目标相同的 CPU 架构和操作系统，不支持跨架构生成。Linux 二进制依赖系统 glibc；建议在计划支持的最旧发行版上构建，并在目标发行版验证。产物包含 Python 运行时、应用依赖、模板和静态资源，不包含数据库、密钥、包文件或日志。

## CLI 命令

源码和单文件程序共用同一套 CLI。查看帮助：

```bash
python -m ngxops --help
python -m ngxops serve --help
python -m ngxops database --help
python -m ngxops admin --help
```

| 命令 | 用途 |
|---|---|
| `serve` | 在当前终端运行单 worker Web 服务，按 `Ctrl+C` 停止 |
| `start` | 后台启动受管服务 |
| `stop` | 停止受管服务，可通过 `--timeout` 设置等待秒数 |
| `status` | 查询受管服务状态、PID 和监听地址 |
| `database init` | 创建 SQLite 文件并应用所有待执行迁移 |
| `database upgrade` | 应用尚未执行的数据库迁移 |
| `admin create [username]` | 创建首个超级管理员，默认用户名为 `admin` |
| `admin reset [username]` | 重置超级管理员密码、清除登录锁并撤销旧会话 |

查看服务选项：

```bash
python -m ngxops serve --help
python -m ngxops start --help
```

服务选项包括 `--host`、`--port` 和 `--log-level`。前台 `serve` 支持 `--reload` 与 `--no-reload`；后台 `start` 不启用自动重载。CLI 支持 `--home PATH`，必须放在子命令之前。

## 初始化与运行

统一设置数据目录，然后执行初始化和服务命令：

```bash
export NGXOPS_HOME="$HOME/.local/share/ngxops"
mkdir -p "$NGXOPS_HOME"

./dist/ngxops-linux-amd64 --home "$NGXOPS_HOME" database init
./dist/ngxops-linux-amd64 --home "$NGXOPS_HOME" admin create
./dist/ngxops-linux-amd64 --home "$NGXOPS_HOME" start
./dist/ngxops-linux-amd64 --home "$NGXOPS_HOME" status
./dist/ngxops-linux-amd64 --home "$NGXOPS_HOME" stop
```

ARM64 主机将可执行文件名替换为 `ngxops-linux-arm64`。`admin create` 和 `admin reset` 通过隐藏提示输入密码并二次确认。全局 `--home` 或 `NGXOPS_HOME` 必须在数据库、管理员、启停命令间保持一致。

也可使用环境变量管理数据目录：

```bash
export NGXOPS_HOME=/var/lib/ngxops
./dist/ngxops-linux-amd64 database upgrade
```

需要重设密码时，单独运行 `./dist/ngxops-linux-amd64 admin reset admin`。

## 数据与日志

所有可写数据均位于 `NGXOPS_HOME`：

- `db.sqlite3`：业务数据库。
- `.secret_key`：会话与 CSRF Cookie 签名密钥。
- `.fernet_key`：SSH 凭证加密密钥。
- `nginx_packages/`：上传的源码包和模块包。
- `logs/ngxops.log`、`logs/ngxops-cli.log`：服务及 CLI 运行日志，按大小轮转。

数据库、密钥和包目录须一起备份。部署新版本前停止服务，备份后运行 `database upgrade`；应用启动不会自动升级数据库。
