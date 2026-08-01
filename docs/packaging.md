# MngxOps 二进制打包与部署说明

本文说明：如何把源码打成**一个可执行文件**，以及在 Windows / Linux x86_64 / Linux ARM64 上分别怎么操作。日常改业务仍用 `python manage.py runserver`，与打包是两条线。

**常规交付假定：新环境、空目录、现场初始化数据库**——不携带旧的 `db.sqlite3`，也不用事先准备密钥文件。

---

## 1. 一页看懂整条链路

```text
开发机（有 Python）                 客户机 / 生产机（可不装 Python）
─────────────────                 ─────────────────────────────
改源码、manage.py runserver   →    只放一个 mngxops 二进制
         │                                    │
         │  pyinstaller（在本机构建）           │  migrate → createsuperuser → 启动
         ▼                                    ▼
   dist/mngxops(.exe)  ──拷贝──►   自动生成 db.sqlite3、.fernet_key、media/
```

| 阶段 | 你在干什么 | 用什么 |
|------|------------|--------|
| 开发 | 改功能、调试 | 源码 + `manage.py runserver` |
| 打包 | 在**目标系统**上把项目打成单文件 | `pyinstaller mngxops.spec` |
| 运行 | 客户机空目录初始化并启动 | `mngxops` / `mngxops.exe` |

**硬规则：不能交叉编译。**  
在 Windows 上只能打出 Windows 包；要 Linux ARM64 包，必须在 ARM64 机器（如麒麟 ARM）上执行同样的打包命令。

| 你要的包 | 必须在哪台机器上打包 |
|----------|----------------------|
| Windows x86_64 | Windows 64 位 + Python 3.9.6 |
| Linux x86_64（amd64） | Linux amd64 + Python 3.9.6 |
| Linux ARM64（aarch64） | Linux aarch64 + Python 3.9.6 |

---

## 2. 数据目录里有什么

二进制跑起来后，**可写数据**写在「数据目录」（首次运行自动创建，无需事先准备）：

- 未设置环境变量时：**可执行文件所在目录**
- 设置了 `MNGXOPS_HOME` 时：**该目录**

| 文件/目录 | 作用 | 常规交付 |
|-----------|------|----------|
| `db.sqlite3` | 业务数据库 | `migrate` / 首次启动时自动建 |
| `media/` | 上传文件（源码包等） | 用到上传功能时自动出现 |
| `.fernet_key` | 加密 SSH 凭证用的本地密钥 | **首次运行自动生成，不用拷贝** |

模板、页面等只读资源已打进二进制，一般不用单独拷。

`.fernet_key`：库里的 SSH 密码/私钥会加密存放，靠这个本地文件加解密。新环境空库时程序自己生成即可；开发态源码则写在 `utils/.fernet_key`（与二进制交付无关）。

---

## 3. 打包前公共准备

三平台共用同一套源码与规格文件：

- 入口脚本：[`run_server.py`](../run_server.py)
- 打包规格：[`mngxops.spec`](../mngxops.spec)
- 业务依赖：[`requirements-3.9.6.txt`](../requirements-3.9.6.txt)
- 打包依赖：[`requirements-packaging.txt`](../requirements-packaging.txt)（含 `waitress`、`pyinstaller`）

Python 版本：**3.9.6**（与项目约定一致）。

在项目根目录（含 `manage.py`、`mngxops.spec` 的目录）执行后续命令。

---

## 4. Windows x86_64 打包步骤

### 4.1 环境

- Windows 10/11 x64
- 已安装 Python 3.9.6（64 位）
- 本仓库常用 venv 示例：`D:\PyCharm\联动优势\works\django-labs\venv3`

### 4.2 命令（PowerShell）

```powershell
# 进入项目根
cd D:\PyCharm\联动优势\works\django-labs\mngxops

# 激活虚拟环境（按你本机路径改）
D:\PyCharm\联动优势\works\django-labs\venv3\Scripts\Activate.ps1

# 若 pip 走坏代理报错，可先：
# $env:NO_PROXY='*'
# python -m pip install --proxy="" -r requirements-3.9.6.txt
# python -m pip install --proxy="" -r requirements-packaging.txt

python -m pip install -r requirements-3.9.6.txt
python -m pip install -r requirements-packaging.txt

# 打包（约数分钟）
pyinstaller --noconfirm mngxops.spec
```

### 4.3 产物

- 原始：`dist\mngxops.exe`
- 建议重命名：`dist\mngxops-windows-amd64.exe`

### 4.4 本机冒烟（空目录初始化）

```powershell
$env:MNGXOPS_HOME = "$env:TEMP\mngxops-win-smoke"
New-Item -ItemType Directory -Force -Path $env:MNGXOPS_HOME | Out-Null

.\dist\mngxops.exe migrate
.\dist\mngxops.exe createsuperuser
.\dist\mngxops.exe serve --host 127.0.0.1 --port 8000
```

浏览器打开：`http://127.0.0.1:8000/login/`

（本仓库已在 Windows x64 上验证过 `migrate` 与登录页可访问。）

---

## 5. Linux x86_64（amd64）打包步骤

### 5.1 环境

- x86_64 的 Linux（`uname -m` 应为 `x86_64`）
- Python 3.9.6 + venv
- 构建依赖：一般需 `gcc`、`make`（部分 wheel 需编译时）

```bash
uname -m   # 确认是 x86_64
```

### 5.2 命令

```bash
cd /path/to/mngxops

python3.9 -m venv .venv
source .venv/bin/activate

pip install -r requirements-3.9.6.txt
pip install -r requirements-packaging.txt

pyinstaller --noconfirm mngxops.spec
```

### 5.3 产物

- 原始：`dist/mngxops`
- 建议重命名并赋权：

```bash
cp dist/mngxops dist/mngxops-linux-amd64
chmod +x dist/mngxops-linux-amd64
```

### 5.4 本机冒烟（空目录初始化）

```bash
export MNGXOPS_HOME=/tmp/mngxops-linux-amd64-smoke
mkdir -p "$MNGXOPS_HOME"

./dist/mngxops-linux-amd64 migrate
./dist/mngxops-linux-amd64 createsuperuser
./dist/mngxops-linux-amd64 serve --host 127.0.0.1 --port 8000
```

浏览器：`http://127.0.0.1:8000/login/`

---

## 6. Linux ARM64（aarch64，含麒麟 ARM）打包步骤

### 6.1 环境

- **必须在 ARM64 机器上打包**（`uname -m` 应为 `aarch64`）
- 不能拿 Windows / x86_64 Linux 打出来的包到 ARM 上跑
- Python 3.9.6 + venv

```bash
uname -m   # 确认是 aarch64
```

### 6.2 命令

与 Linux amd64 **相同**，只是机器架构不同：

```bash
cd /path/to/mngxops

python3.9 -m venv .venv
source .venv/bin/activate

pip install -r requirements-3.9.6.txt
pip install -r requirements-packaging.txt

pyinstaller --noconfirm mngxops.spec

cp dist/mngxops dist/mngxops-linux-arm64
chmod +x dist/mngxops-linux-arm64
```

### 6.3 本机冒烟（空目录初始化）

```bash
export MNGXOPS_HOME=/tmp/mngxops-linux-arm64-smoke
mkdir -p "$MNGXOPS_HOME"

./dist/mngxops-linux-arm64 migrate
./dist/mngxops-linux-arm64 createsuperuser
./dist/mngxops-linux-arm64 serve --host 0.0.0.0 --port 8000
```

---

## 7. 客户机首次启动（标准路径，推荐）

适用：**新机器、空目录、没有旧库**（本项目常规交付方式）。

1. 放入对应平台的二进制（可单独一个文件夹）
2. 初始化库并创建管理员
3. 启动服务，浏览器打开登录页

| 步骤 | Windows | Linux（amd64 / arm64） |
|------|---------|-------------------------|
| 建库 | `mngxops-windows-amd64.exe migrate` | `./mngxops-linux-amd64 migrate`（ARM 用 `arm64` 文件名） |
| 建管理员 | `...\createsuperuser` | `./... createsuperuser` |
| 启动 | 直接运行 exe，或 `... serve --host 0.0.0.0 --port 8000` | 同上 |

说明：

- **无参数**直接运行 = 启动 Web（默认先自动 `migrate --noinput`），再另开终端做一次 `createsuperuser` 也可
- `createsuperuser` 需要在真实终端里交互输入用户名/密码
- `.fernet_key` / `db.sqlite3` **不用事先准备**，首次运行会生成
- 可用 `--no-migrate` 跳过启动前自动迁移
- 启动后终端默认输出 **HTTP 访问日志**（类似 Apache combined）；可用 `--no-access-log` 关闭
- 启动别名：`serve` / `run` / `runserver`（不是 `server`）

```text
# Windows 示例（空目录）
mngxops-windows-amd64.exe migrate
mngxops-windows-amd64.exe createsuperuser
mngxops-windows-amd64.exe serve --host 0.0.0.0 --port 8000

# Linux 示例
./mngxops-linux-arm64 migrate
./mngxops-linux-arm64 createsuperuser
./mngxops-linux-arm64 serve --host 0.0.0.0 --port 8000
```

登录页：`http://<主机>:8000/login/`

### 环境变量（可选）

| 变量 | 含义 |
|------|------|
| `MNGXOPS_HOME` | 数据目录（库、media、`.fernet_key`）；不设则用 exe 所在目录 |
| `MNGXOPS_HOST` / `MNGXOPS_PORT` | 默认监听（也可用 `serve --host/--port`） |
| `MNGXOPS_DEBUG` | `1` 打开 / `0` 关闭 DEBUG |
| `MNGXOPS_SECRET_KEY` | Django `SECRET_KEY` |

### 二进制支持的管理命令

`migrate`、`createsuperuser`、`showmigrations`、`collectstatic`  
（不是完整 `manage.py`，其它子命令默认不开。）

---

## 8. 常见问题

**Q：能在 Windows 上打出 ARM / Linux 包吗？**  
A：不能。PyInstaller 绑定当前 OS 与 CPU，三平台要打三次。

**Q：打包时报 pip / 代理 SSL 错误？**  
A：可尝试清空代理后再装：

```powershell
$env:NO_PROXY='*'
python -m pip install --proxy="" -r requirements-packaging.txt
```

**Q：要不要先准备 db 或密钥文件？**  
A：常规新环境不需要。放二进制 → `migrate` → `createsuperuser` → 启动即可。

**Q：交付物里还有 `.py` 吗？**  
A：客户机上看不到源码树；包内主要是字节码/依赖。这不等于军事级防逆向（`.pyc` 仍可能被还原）。

**Q：开发还要不要用打包入口？**  
A：不要。开发继续：

```bash
python manage.py runserver
```

---

## 9. 相关文件速查

| 文件 | 作用 |
|------|------|
| [`run_server.py`](../run_server.py) | 二进制入口（Waitress + 白名单 manage） |
| [`mngxops.spec`](../mngxops.spec) | PyInstaller 规格 |
| [`ngxops/runtime_paths.py`](../ngxops/runtime_paths.py) | 冻结/源码下资源与数据目录 |
| [`utils/crypto.py`](../utils/crypto.py) | 凭证加密与 `.fernet_key` 路径 |
| [`requirements-packaging.txt`](../requirements-packaging.txt) | 仅打包用依赖 |

---

## 附录：极少数情况——迁移已有旧库

**本项目常规交付不涉及。** 仅当必须把**已有** `db.sqlite3`（且库内已有加密凭证）搬到二进制环境时：

1. 拷贝 `db.sqlite3` → 数据目录  
2. 拷贝源码侧 `utils/.fernet_key` → 数据目录，文件名为 `.fernet_key`（与库同级）  
3. 如需历史上传文件，再拷 `media/`  
4. 执行 `migrate` 后启动  

若只拷库不拷钥匙，二进制会生成新 `.fernet_key`，旧凭证密文将无法解密。
