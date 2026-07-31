# mngxops

Nginx 多节点配置运维平台（MngxOps）。

面向运维人员，统一管理主机节点与 SSH 凭证，发现/编辑远程 Nginx 配置，按绑定版本发布与回滚，并支持源码编译升级 Nginx。技术栈为 Django 服务端渲染（Bootstrap 5）+ Paramiko SSH；长任务通过进程内线程与「任务中心」统一追踪（无 Celery）。

---

## 主要功能

| 能力 | 说明 |
|------|------|
| 节点 / 节点组 | 主机库存、锁定、SSH 探测、系统信息与 Nginx 版本采集、Excel 批量导入、逻辑删除与同 IP 恢复 |
| SSH 凭证 | 密码/私钥加密存储、启用时对关联节点并发连通性测试 |
| 配置管理 | 配置标签、节点绑定、绑定级版本、远程发现/同步向导 |
| 发布中心 | 按节点×绑定勾选发布；同节点复用 SSH、统一 reload；失败可重试 |
| 发布历史 | 按批次查看；单条 / 勾选回滚 |
| 任务中心 | 发布、同步、节点探测、升级等异步任务的进度与结果树 |
| Nginx 升级 | 源码包管理、多节点四步向导（编译参数 / 模块 / 安装 / reload） |
| 用户与权限 | 用户、角色（权限矩阵）、用户组；`resource.action` RBAC |
| 审计 | 操作日志、登录日志；可软链到任务中心 |
| 系统设置 | 分组配置（SSH 超时、并发、备份目录、保留天数等），所见即所得 |

---

## 运行环境

| 项 | 要求 |
|----|------|
| 语言 | **Python 3.9.6**（项目开发约定） |
| Web 框架 | Django 4.2.x（当前锁定见 `requirements.txt`，如 4.2.30） |
| 数据库 | 默认 **SQLite**（`db.sqlite3`）；可按需改为其他 Django 支持的库 |
| 主要依赖 | Paramiko（SSH）、cryptography（凭证加密）、openpyxl（节点导入） |
| 浏览器 | 现代 Chromium / Firefox / Edge |
| 目标节点 | Linux + SSH；已安装或可编译安装 Nginx |

完整依赖列表：[`requirements.txt`](requirements.txt)。

---

## 快速开始

以下以本仓库常用 Windows 虚拟环境为例；Linux / macOS 请改用对应 venv 的 `bin/activate`。

### 1. 激活虚拟环境

```powershell
D:\PyCharm\venv3\Scripts\activate
```

也可使用项目本地自建的 venv，只要 Python 版本符合要求即可。

### 2. 安装依赖

在项目根目录（含 `manage.py`）执行：

```powershell
cd D:\PyCharm\mngxops
pip install -r requirements.txt
```

### 3. 初始化数据库

```powershell
python manage.py migrate
python manage.py createsuperuser
```

首次启动后，系统设置会按预置项自动 seed（不覆盖已有值）。

### 4. 启动服务

```powershell
python manage.py runserver
```

浏览器访问：

- 首页：`http://127.0.0.1:8000/`
- 登录：`http://127.0.0.1:8000/login/`

使用超级用户登录后可配置角色权限并开始录入凭证与节点。

---

## 功能模块与 URL 速查

| 模块 | 路径 | 说明 |
|------|------|------|
| 仪表盘 | `/` | 统计与告警 |
| 登录 / 个人中心 | `/login/`、`/profile/` | 账户 |
| 节点 | `/nodes/` | 节点与节点组 |
| 凭证 | `/credentials/` | SSH 凭证 |
| 配置 | `/configs/`、`/configs/sync/` | 配置列表与同步向导 |
| 发布中心 | `/releases/center/` | 勾选发布 |
| 任务中心 | `/releases/history/` | 异步任务列表（注意与发布历史区分） |
| 发布历史 | `/releases/list/` | 发布/回滚历史 |
| Nginx 升级 | `/upgrade/`、`/upgrade/center/` | 升级首页与向导 |
| 用户 / 角色 / 用户组 | `/users/`、`/users/roles/`、`/users/teams/` | RBAC |
| 审计 | `/audit/`、`/audit/login/` | 操作与登录日志 |
| 系统设置 | `/settings/` | 运行参数 |

更细的接口与业务规则见 [`docs/`](docs/README.md)。

---

## 仓库目录结构

```text
mngxops/
├── apps/           # 业务应用（accounts、nodes、configs、releases、upgrade 等）
├── ngxops/         # Django 项目配置（settings、urls、wsgi）
├── utils/          # 共享工具（SSH、nginx 启停、设置读取、加密、分页、数据保留）
├── templates/      # 全局布局与错误页
├── media/          # 上传文件（源码包、头像等）
├── docs/           # 软件需求设计文档
├── manage.py
├── requirements.txt
├── AGENTS.md       # 优化点结论台账（唯一来源）
└── README.md       # 本手册
```

---

## 配置与数据提示

- **项目配置**：[`ngxops/settings.py`](ngxops/settings.py)（语言 `zh-hans`、时区 `Asia/Shanghai`、`MEDIA` 等）。
- **业务运行参数**：优先在系统设置页修改（SSH 超时、发布并行度、备份目录、数据保留天数等），由 `get_setting` 读取。
- **凭证加密**：Fernet 密钥文件位于 `utils/.fernet_key`（勿提交到公开仓库；丢失将无法解密已存密文）。
- **发布备份**：远程路径形如 `{backup_dir}/{hostname}/…`，备份根目录可在系统设置中配置。
- **数据清理**：可配置保留天数；亦可执行 `python manage.py purge_expired_data`（会跳过进行中的任务）。

生产环境请自行调整 `DEBUG`、`SECRET_KEY`、`ALLOWED_HOSTS` 与数据库，并做好密钥与 `media/` 备份。

---

## 延伸阅读

| 文档 | 说明 |
|------|------|
| [docs/README.md](docs/README.md) | 软件需求设计文档索引（架构、各模块、API、非功能） |
| [docs/00-overview.md](docs/00-overview.md) | 产品定位、术语表、模块地图 |
| [AGENTS.md](AGENTS.md) | 优化点结论台账（Q1–Q118+；唯一来源） |
