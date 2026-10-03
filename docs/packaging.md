# 单文件程序与命令行

## 构建

项目使用 PyInstaller one-file 模式打包，构建环境要求 Python 3.9。`requirements.txt` 同时包含运行和构建依赖。

```console
python -m pip install -r requirements.txt
python tools/build_binary.py --help
python tools/build_binary.py --print-target
python tools/build_binary.py
```

构建入口只在当前构建主机上执行 PyInstaller，并按系统和 CPU 自动命名：

| 构建主机 | 输出文件 |
|---|---|
| Windows x86-64 | `dist/ngxops-win10-amd.exe` |
| Linux x86-64 / amd64 | `dist/ngxops-linux-amd64` |
| Linux ARM64 / aarch64 | `dist/ngxops-linux-arm64` |
| macOS x86-64 / amd64 | `dist/ngxops-macos-amd64` |
| macOS ARM64 / Apple Silicon | `dist/ngxops-macos-arm64` |

Windows 示例（Python 3.9 x86-64）：

```powershell
python -m pip install -r requirements.txt
python tools/build_binary.py
```

Linux amd64 示例：

```bash
python3.9 -m venv .venv-build
. .venv-build/bin/activate
python -m pip install -r requirements.txt
python tools/build_binary.py
```

Linux ARM64 使用同一组命令，但必须在 ARM64/aarch64 Linux 主机或虚拟机上运行 ARM64 的 Python 3.9。macOS 也须在对应架构的 macOS 主机上构建。PyInstaller 不支持跨操作系统或跨 CPU 架构交叉生成文件。Linux 程序使用系统 glibc；应在准备支持的最旧发行版上构建，并在目标发行版验证。构建入口会拒绝未声明支持的平台/架构。二进制包含 Web 应用依赖、`templates/` 和 `static/`；可写数据不会打入程序包。

## 命令

统一 CLI 提供英文帮助和交互提示。根帮助列出所有命令，具体子命令也支持 `--help`：

```text
ngxops --help
ngxops start --help
ngxops database --help
ngxops admin --help
```

| 命令 | 行为 |
|---|---|
| `ngxops serve` | 在当前终端前台运行单 worker Web 服务；按 `Ctrl+C` 停止 |
| `ngxops start` | 后台启动服务，等待 `/health` 成功后返回 PID |
| `ngxops stop` | 校验服务记录与进程身份，优雅停止并等待任务/应用清理 |
| `ngxops status` | 显示受管服务状态、PID 和监听地址 |
| `ngxops database init` | 创建 SQLite 数据库并执行全部待应用迁移 |
| `ngxops database upgrade` | 对已有 ngxops 数据库应用新增迁移 |
| `ngxops admin create [username]` | 创建首个超级管理员；省略用户名时使用 `admin` |
| `ngxops admin reset [username]` | 本机重置超级管理员密码；省略用户名时重置 `admin`，并清除登录锁定、撤销其全部旧会话 |

数据目录可通过 `NGXOPS_HOME` 指定，也可将全局 `--home PATH` 放在子命令之前，例如 `ngxops --home D:\ngxops-data database init`。迁移、管理员命令、启停和服务运行必须使用同一数据目录。不要将数据目录指向 mngxops 的 Django 数据库。

Windows 初始化和启停示例：

```powershell
$env:NGXOPS_HOME = 'D:\ngxops-data'
.\dist\ngxops-win10-amd.exe database init
.\dist\ngxops-win10-amd.exe admin create
.\dist\ngxops-win10-amd.exe start --host 127.0.0.1 --port 8000
.\dist\ngxops-win10-amd.exe status
.\dist\ngxops-win10-amd.exe stop
```

管理员命令用系统隐藏输入读取密码，不接受密码命令参数。新密码至少 8 个字符，不得全为数字、与用户名过于相似或命中常见弱密码集合。创建和重置命令省略用户名时均使用 `admin`，也可附加其他用户名；重置只接受超级管理员用户名。重置不会要求旧密码，所以必须限制可访问程序数据目录的操作系统账户。

`start` 写入 `NGXOPS_HOME/run/service.json` 并使用 `start.lock` 防止并发重复启动。停止操作会校验 PID、进程创建时间、程序路径和随机启动标记。Linux/macOS 发送 `SIGTERM`；Windows 通过独立进程组发送 `CTRL_BREAK_EVENT`。等待超时后才强制结束。升级迁移前仍应先运行 `stop` 并备份数据目录。

服务退出不会等待所有长任务完成。进程重启后，遗留的 `pending/running` 任务会标记为失败，不会自动重跑；已发往远端的 SSH 命令可能继续运行。强制停止或异常退出后，重试安装、发布、升级或卸载前应先检查远端状态。

## 数据与日志

源码和单文件程序未设置 `NGXOPS_HOME` 时，数据目录都是启动命令所在的当前工作目录。

设置 `NGXOPS_HOME` 或使用 `--home` 可覆盖默认位置。`logs/ngxops.log` 保存 Web 请求、已提交业务操作、应用/任务异常和调用位置；`logs/ngxops-cli.log` 保存命令行管理操作。日志按 10 MiB 分片，最多保留 10 个历史分片。Web 请求日志记录方法、路径、状态、耗时、用户 ID 和来源 IP，不记录查询参数、请求正文、Cookie 或认证头。异常堆栈不包含局部变量；SQLAlchemy 隐藏绑定参数，日志格式会遮蔽常见密码、令牌和私钥字段。

运行日志包含账户标识、来源地址和异常文本，应按敏感运维资料限制文件访问并纳入备份/清理策略。数据库 `/audit/` 操作记录和 `/audit/logins/` 登录记录继续按系统设置中的保留周期清理；文件日志由大小轮转控制。

## 发布建议

- 对外发布时为每个受支持的操作系统和 CPU 架构分别构建，并记录 Python、依赖和构建提交版本。
- 发布二进制时附带 SHA-256 校验值；正式环境建议对 Windows 可执行文件进行代码签名。
- 使用 `--host 127.0.0.1` 将管理页面限制为本机访问；需要远程访问时应配置反向代理 TLS、防火墙和可信 CSRF 来源。
- `start/stop` 管理单个应用进程，不负责开机自启。生产环境如需自动恢复，应使用 systemd、Windows 服务或计划任务管理该命令，并仍保持单 worker。
- 备份时停止服务，并将 `db.sqlite3`、`.secret_key`、`.fernet_key`、`nginx_packages/` 和必要日志作为同一数据目录处理。
