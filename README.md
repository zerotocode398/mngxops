# ngxops

ngxops 是用于管理 Nginx 多节点运维流程的 Web 平台，提供账户与权限、SSH 凭证、节点资产、配置同步、批量发布、任务跟踪、Nginx 安装/升级/启停/卸载、系统设置和操作审计。

功能细节见 [文档索引](docs/README.md)。
开发规范见 [AGENTS.md](AGENTS.md)。

## 运行要求

- Linux x86-64 或 ARM64。
- Python 3.9 或更新版本。Python 3.6、3.7 和 3.8 不支持当前代码与依赖。

## 配置环境变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `NGXOPS_HOME` | 当前工作目录 | SQLite、密钥、上传包和日志所在目录 |
| `NGXOPS_HOST` | `0.0.0.0` | 默认监听地址 |
| `NGXOPS_PORT` | `11993` | 默认监听端口 |
| `NGXOPS_DEBUG` | 开启（源码运行） | 调试模式 |
| `NGXOPS_RELOAD` | 开启（源码运行） | 服务自动重载 |
| `NGXOPS_LOG_LEVEL` | `info` | 日志级别 |
| `NGXOPS_SECRET_KEY` | 自动生成并保存到 `.secret_key` | 会话与 CSRF 签名；固定部署密钥通过此变量指定 |
| `NGXOPS_HTTPS` | `0` | 为会话与 CSRF Cookie 启用 Secure 属性 |
| `NGXOPS_CSRF_TRUSTED_ORIGINS` | 空 | 逗号分隔的额外可信来源 |
| `NGXOPS_RELEASE_BACKUP_DIR` | `/opt/ngxops/backups` | 设置表不可用时的发布备份目录后备值 |
| `NGXOPS_UPGRADE_PACKAGE_MAX_SIZE_MB` | `20` | 设置表不可用时的包上传限制后备值，单位 MiB |


## 命令行参数
```bash
usage: run_server.py [-h] [--home HOME] [--host HOST] [--port PORT] [--log-level LOG_LEVEL] [--reload [true|false]]

启动 ngxops Web 服务

optional arguments:
  -h, --help            show this help message and exit
  --home HOME           数据存储目录
  --host HOST           监听地址 (default: 0.0.0.0)
  --port PORT           监听端口 (default: 11993)
  --log-level LOG_LEVEL
                        日志级别 (default: info)
  --reload [true|false]
                        自动重载；无参数时开启，未指定时配置默认值为开启。

数据目录默认为 NGXOPS_HOME；未设置时使用当前工作目录（./）。可用 --home PATH 覆盖。
```

## 源码部署

在仓库根目录执行。以下使用 `/opt/app/ngxops` 作为服务数据目录，确保运行服务的账户对此目录有读写权限：

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p /opt/app/ngxops
```

### 初始化与启动

`python -m ngxops` 是源码运行时的数据库、管理员和服务的 CLI。查看命令及参数：

```bash
python -m ngxops --help
python -m ngxops serve --help
python -m ngxops start --help
python -m ngxops stop --help
python -m ngxops database --help
python -m ngxops admin --help
```

#### 初始化数据库 & 创建管理员

先创建数据库并应用所有已登记的数据库版本，再创建首位超级管理员：

```bash
python -m ngxops --home /opt/app/ngxops database init
python -m ngxops --home /opt/app/ngxops admin create
```

#### 启动服务
```bash
python -m ngxops --home /opt/app/ngxops start
```

## 二进制部署

### 编译二进制

```bash
python3 --version
python3 -m venv .venv
source .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python tools/build_binary.py --print-target
python tools/build_binary.py

```

### 初始化与启动

`ngxops` 是二进制运行时的数据库、管理员和服务的 CLI。查看命令及参数：

```bash
/opt/ngxops/bin/ngxops --help
/opt/ngxops/bin/ngxops serve --help
/opt/ngxops/bin/ngxops start --help
/opt/ngxops/bin/ngxops stop --help
/opt/ngxops/bin/ngxops database --help
/opt/ngxops/bin/ngxops admin --help
```

#### 初始化数据库 & 创建管理员
```bash
/opt/ngxops/bin/ngxops --home /opt/app/ngxops database init
/opt/ngxops/bin/ngxops --home /opt/app/ngxops admin create
```

#### 启动服务
```bash
/opt/ngxops/bin/ngxops --home /opt/app/ngxops start
/opt/ngxops/bin/ngxops --home /opt/app/ngxops status
```
