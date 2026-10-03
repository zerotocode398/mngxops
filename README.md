# ngxops

基于 FastAPI、Jinja2、jQuery 和 SQLite3 重写的 Nginx 多节点管理平台。当前已实现账户/RBAC、凭证与节点、配置同步、批量发布与回滚、异步任务中心、源码包管理、Nginx 编译升级/安装/启停/卸载、操作审计、系统设置和仪表盘。阶段状态见 [AGENTS.md](AGENTS.md)，文档索引见 [docs/README.md](docs/README.md)。

## Windows 本地首次启动

以下命令在项目根目录的 PowerShell 中执行。服务端要求 Python 3.9；本机默认虚拟环境位于项目同级的 `..\venv3`。先确认 `python --version` 输出为 Python 3.9.x：

```powershell
python --version
```

如果 `..\venv3` 尚不存在，使用 Python 3.9 创建一次：

```powershell
python -m venv ..\venv3
```

每个新的 PowerShell 窗口都需要重新激活虚拟环境，然后安装项目依赖：

```powershell
& ..\venv3\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 1. 选择数据目录

为 ngxops 指定独立、可写的数据目录。不要指向同级 Django 项目 `mngxops` 的数据目录。SQLite 数据库、会话签名密钥、SSH 凭证加密密钥和上传的 Nginx 包都存放在这个目录下。

```powershell
$env:NGXOPS_HOME = 'D:\ngxops-data'
```

`NGXOPS_HOME` 只对当前 PowerShell 窗口及其启动的进程生效。数据库迁移、管理员命令和 Web 服务必须使用同一个目录；打开新窗口时要再次设置。若不设置，源码和单文件程序都使用启动命令所在的当前工作目录。

### 2. 初始化或升级数据库

先停止正在运行的 ngxops 服务，再在项目根目录执行：

```powershell
python -m ngxops.database upgrade
```

首次运行会在所选数据目录中创建 `db.sqlite3` 并应用当前代码登记的全部迁移（当前为 v14）。再次执行只会应用尚未执行的迁移。迁移不会在 Web 服务启动时自动运行；没有 ngxops 迁移标记的非空数据库会被拒绝修改，不能用同级 Django 项目的数据库进行初始化。

### 3. 创建首个超级管理员

数据库迁移成功后创建默认登录名为 `admin` 的超级管理员：

```powershell
python -m ngxops admin create
```

命令会要求输入并再次确认密码，输入时不会回显。也可在 `create` 后附加其他用户名，例如 `python -m ngxops admin create ops`；用户名只允许 ASCII 字母、数字、下划线和连字符，长度为 1 至 150 个字符。此命令只用于创建首个用户：数据库至少完成 v1 至 v3 迁移且尚无任何用户时才会成功。

新密码至少 8 个字符，不能全部为数字、不能与用户名过于相似，也不能属于程序拒绝的常见弱密码。密码不要写在命令参数或脚本中。

### 4. 启动并登录

确认当前 PowerShell 中仍设置了相同的 `NGXOPS_HOME`，然后启动服务：

```powershell
python run_server.py
```

开发模式默认监听 `127.0.0.1:8000` 并启用自动重载。服务进程保持运行时，在浏览器访问 <http://127.0.0.1:8000/login/>，使用刚创建的用户名和密码登录。按 `Ctrl+C` 停止服务。`/health` 是健康检查，`/docs` 是 FastAPI 接口文档，`/openapi.json` 提供 OpenAPI JSON。

可用启动参数临时覆盖监听地址、端口和自动重载；端口被占用时可换用其他端口：

```powershell
python run_server.py --host 127.0.0.1 --port 8001 --no-reload
```

### 5. 修改管理员密码

以管理员身份登录后，打开当前服务地址下的 `/password/change/` 页面，或从个人中心选择“修改密码”。默认端口下地址为 <http://127.0.0.1:8000/password/change/>；若启动时指定了其他端口，请相应替换 `8000`。填写当前密码、新密码和确认密码后提交。修改成功后当前浏览器会话继续有效，该账户在其他浏览器中的旧会话会失效。

修改密码要求输入正确的旧密码；新密码需两次一致，并遵守创建管理员时的密码规则。若无法登录，可在本机运行 `python -m ngxops admin reset` 重置默认 `admin` 账户；也可附加其他超级管理员用户名。该命令不要求旧密码，会撤销目标账户的旧会话，因此应限制对应用数据目录的系统账户访问。

首次部署和升级的备份要求、配置项及运行限制见 [`docs/deployment.md`](docs/deployment.md)；账户规则见 [`docs/accounts.md`](docs/accounts.md)。Nginx 全新安装流程见 [`docs/nginx-install.md`](docs/nginx-install.md)，编译升级和包管理见 [`docs/upgrade.md`](docs/upgrade.md)，启停操作台和历史见 [`docs/nginx-service.md`](docs/nginx-service.md)。

## 单文件可执行程序

在目标操作系统及目标 CPU 架构上使用 Python 3.9 构建。构建脚本会按主机自动命名：Windows x86-64 输出 `ngxops-win10-amd.exe`，Linux x86-64 输出 `ngxops-linux-amd64`，Linux ARM64 输出 `ngxops-linux-arm64`。PyInstaller 不做跨操作系统或跨 CPU 架构交叉编译：

```console
python -m pip install -r requirements.txt
python tools/build_binary.py --help
python tools/build_binary.py
```

Windows 构建须在 Windows x86-64 上执行；Linux amd64 和 arm64 分别须在相应架构的 Linux 主机或虚拟机上执行。Linux 二进制依赖目标系统提供的 glibc，建议在计划支持的最旧 Linux 发行版上构建，再到较新发行版验证运行。构建结果是带控制台的单文件程序，包含 Python 运行时、依赖、Jinja2 模板和静态资源。发布时只分发该可执行文件；数据库、密钥、上传文件和日志位于数据目录，不打进二进制。

单文件程序支持英文交互提示和标准帮助参数。先查看完整命令：

```powershell
.\dist\ngxops-win10-amd.exe --help
.\dist\ngxops-win10-amd.exe admin --help
```

Windows PowerShell 首次初始化和启动示例：

```powershell
$env:NGXOPS_HOME = 'D:\ngxops-data'
.\dist\ngxops-win10-amd.exe database init
.\dist\ngxops-win10-amd.exe admin create
.\dist\ngxops-win10-amd.exe start --host 127.0.0.1 --port 8000
.\dist\ngxops-win10-amd.exe status
```

Linux amd64 初始化和启动示例（arm64 将文件名替换为 `ngxops-linux-arm64`）：

```bash
export NGXOPS_HOME="$HOME/.local/share/ngxops"
./dist/ngxops-linux-amd64 database init
./dist/ngxops-linux-amd64 admin create
./dist/ngxops-linux-amd64 start --host 127.0.0.1 --port 8000
./dist/ngxops-linux-amd64 status
```

`admin create` 会隐藏输入并二次确认首个超级管理员密码。服务启动后访问 <http://127.0.0.1:8000/login/>。常用运维命令如下：

```powershell
.\dist\ngxops-win10-amd.exe stop
.\dist\ngxops-win10-amd.exe database upgrade
.\dist\ngxops-win10-amd.exe admin reset
```

密码重置会隐藏输入新密码、清除登录失败锁定并撤销该管理员的既有会话；无需旧密码，因此只能在有权访问数据目录的本机账户下执行。服务也可用 `serve` 在当前控制台前台运行，按 `Ctrl+C` 停止。CLI 所有子命令都有 `--help`。

查看 [`docs/packaging.md`](docs/packaging.md) 了解完整命令、后台启停行为、运行日志和发布注意事项。

## 配置

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `NGXOPS_HOST` | 开发环境 `127.0.0.1`；冻结运行 `0.0.0.0` | Uvicorn 监听地址 |
| `NGXOPS_PORT` | 开发环境 `8000`；冻结运行 `11993` | Uvicorn 监听端口 |
| `NGXOPS_DEBUG` | 开发环境开启；冻结运行关闭 | FastAPI 调试模式 |
| `NGXOPS_RELOAD` | 开发环境开启；冻结运行关闭 | Uvicorn 自动重载 |
| `NGXOPS_LOG_LEVEL` | 随调试模式为 `debug` 或 `info` | Uvicorn 日志级别 |
| `NGXOPS_HOME` | 当前工作目录 | 可写数据目录，启动时自动创建；CLI 也可在子命令前使用 `--home` 指定 |
| `NGXOPS_SECRET_KEY` | 自动生成并保存在数据目录 `.secret_key` | CSRF Cookie 签名密钥；多进程部署应共享固定密钥 |
| `NGXOPS_HTTPS` | `0` | 启用 Secure 会话与 CSRF Cookie |
| `NGXOPS_CSRF_TRUSTED_ORIGINS` | 空 | 逗号分隔的额外可信来源，例如 `https://ops.example.com` |
| `NGXOPS_RELEASE_BACKUP_DIR` | `/opt/app/mascloud/ansible/mngxops` | v14 系统设置表不可用时的发布备份目录兼容默认值；升级后由 `release.backup_dir` 控制 |
| `NGXOPS_UPGRADE_PACKAGE_MAX_SIZE_MB` | `20` | v14 系统设置表不可用时的上传限制兼容默认值，范围 1 到 1024 MiB；升级后由 `upgrade.package_max_size_mb` 控制 |

模板与静态资源从程序资源目录读取；数据库、上传文件和密钥等可写数据使用 `NGXOPS_HOME`。应用工厂本身不会连接或创建 SQLite 数据库。

`NGXOPS_*` 是当前配置前缀；为兼容旧部署，同名 `MNGXOPS_*` 变量仍可用，二者同时设置时 `NGXOPS_*` 优先。源码和单文件程序未指定数据目录时，都使用当前工作目录。

数据库约束见 [`docs/database.md`](docs/database.md)，会话、CSRF 和认证依赖见 [`docs/security.md`](docs/security.md)。

系统设置与手动数据清理命令见 [`docs/settings.md`](docs/settings.md)。Nginx 安装/升级共享的源码与模块归档位于 `NGXOPS_HOME/nginx_packages/`，应与数据库和加密密钥一起备份。应用支持页面和 JSON 接口；异步任务、包管理锁及取消句柄驻留进程内，部署必须使用单个 Uvicorn worker。
