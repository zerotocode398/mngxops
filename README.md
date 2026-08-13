# mngxops

## 项目介绍

**mngxops（MngxOps）** 是一套面向运维人员的 **Nginx 多节点配置运维平台**。

多台 Linux 主机上的 Nginx，往往各自改配置、各自编译升级，缺少统一入口。本平台通过 SSH 纳管节点：先把远程配置同步到平台，再按绑定版本批量发布与回滚，并覆盖安装、升级、启停、卸载。目标是把「改配置、发配置、管进程」收拢到同一套工作台，而不是在每台机器上单独操作。

典型使用路径：录入 SSH 凭证与节点 → 探测连通性 → 同步远程配置 → 在平台编辑并发布 → 按需安装/升级/启停/卸载。异步作业统一进入任务中心，操作可审计。

技术形态：Django 服务端渲染（Bootstrap 5）+ 部分 JSON API；远程操作用 Paramiko；长任务为进程内线程，不依赖 Celery。默认 SQLite，适合单机部署与验收。

能力主线是 **配置管理 → 批量发布 → 生命周期管理**。节点与凭证是前置资产，任务中心、权限与审计是配套能力。下文按这三条主线说明。

---

## 配置管理

工作对象不是「某一台机器上的一份 `nginx.conf`」，而是 **配置标签 + 节点绑定**：

- **配置标签**：配置类型（名称、默认远程路径、模板），不含某台机器上的实际正文。
- **绑定**：某标签在某节点上的实例（远程路径、当前内容、同步状态、绑定级版本）。
- **同步向导**：从远程发现/拉取配置并写入绑定；同步不是发布。
- **发布**：把绑定的指定版本推到远端并校验/reload。

绑定支持版本历史与对比。常见状态包括未同步、已修改、已同步；远程 Nginx 被卸掉或探测失败后，绑定会标为未检测到（orphaned）。SSH 离线或未知的节点禁止同步；未检测到 Nginx 时，配置列表仅可查看或解除绑定。

入口：`/configs/`、`/configs/sync/`。

---

## 批量发布

发布是 **多节点 × 多配置** 的批次作业，不是在单机上保存即生效：

- **发布中心**按节点展开绑定勾选；粒度支持全量、本节点、单条配置。
- 同一节点复用一条 SSH：备份 → 上传 → `nginx -t`，通过后统一 reload（进程未运行则改为 start）。
- 每次发布有批次号；进度与结果树在任务中心；发布历史按批次查看。
- 回滚会 **新建任务**（不改写原发布记录状态）；成功或失败的发布均可回滚。
- 仅 SSH 在线且 Nginx 可用的节点可发布/回滚；已标记删除的绑定不进入发布勾选。

入口：发布中心 `/releases/center/`，发布历史 `/releases/list/`（与任务中心 `/releases/history/` 不是同一页）。

---

## 生命周期管理

不假定目标机上已经有一套可用的 Nginx。运维工具按阶段分开：

| 阶段 | 适用 | 批次前缀 | 说明 |
|------|------|----------|------|
| 安装 | 在线、尚无可用 Nginx | `IN-` | 源码编译安装；能管理 systemd 则写 unit 并 enable/start |
| 升级 | 在线且已有 Nginx | `UG-` | 四步向导：选节点与源码包 → 编译环境 → 编译参数/模块 → 确认；无 Nginx 时引导去安装 |
| 启停 | 在线且 Nginx 可用 | `OP-` | start / stop / reload / restart |
| 卸载 | 在线且 Nginx 可用 | `UN-` | 源码安装删树并可清 systemd unit；yum/apt 包走 remove。卸载后节点标 Nginx 不可用，绑定 orphaned |

源码包与第三方模块包在升级模块中统一管理，安装向导只读复用。

---

## 配套能力

| 能力 | 说明 |
|------|------|
| 节点 / 节点组 | 主机库存、锁定、SSH 探测、系统信息与 Nginx 版本采集、Excel 导入导出、逻辑删除与同 IP 恢复 |
| SSH 凭证 | 密码/私钥加密存储；启用时可对关联节点做连通性测试；支持明文导出与批量导入 |
| 任务中心 | 发布、同步、探测、安装/升级/启停/卸载等异步任务的进度与结果树 |
| 用户与权限 | 用户、角色（权限矩阵）、用户组；`resource.action` RBAC |
| 审计 | 操作日志、登录日志；可软链到任务中心 |
| 系统设置 | 分组配置（SSH 超时、批量勾选上限、备份目录、保留天数等），所见即所得 |

---

## 运行环境

| 项 | 要求 |
|----|------|
| 语言 | **Python 3.9.6**（项目开发约定） |
| Web 框架 | Django 4.2.x（当前锁定见 `requirements.txt`，如 4.2.30） |
| 数据库 | 默认 **SQLite**（项目根目录 `db.sqlite3`）；可按需改为其他 Django 支持的库 |
| 主要依赖 | Paramiko（SSH）、cryptography（凭证加密）、openpyxl（节点/凭证导入导出） |
| 浏览器 | 现代 Chromium / Firefox / Edge |
| 目标节点 | Linux + SSH；可已安装 Nginx，或通过本平台源码安装 |

完整依赖列表：[`requirements.txt`](requirements.txt)。

---

## 快速开始

以下以仓库旁 Windows 虚拟环境为例；Linux / macOS 请改用对应 venv 的 `bin/activate`。

### 1. 激活虚拟环境

在项目根目录（含 `manage.py`）执行：

```powershell
..\venv3\Scripts\activate
```

也可使用本机其他 venv，只要 Python 版本符合要求即可。

### 2. 安装依赖

```powershell
pip install -r requirements.txt
```

### 3. 初始化数据库

开发态默认使用项目根目录的 `db.sqlite3`。

- **已有库、只补迁移**：直接 `migrate`。
- **要从零验证**：先停掉正在跑的 `runserver`，备份或删除根目录 `db.sqlite3`，再执行下面两条。不要在服务占用数据库时删库。

```powershell
python manage.py migrate
python manage.py createsuperuser
```

首次启动后，系统设置会按预置项自动 seed（不覆盖已有值）。超级用户可进入全部菜单；普通用户需先在「用户 / 角色」中分配权限。

### 4. 启动服务

```powershell
python manage.py runserver
python manage.py runserver 0.0.0.0:1988
```

浏览器访问：

- 首页：`http://127.0.0.1:8000/`
- 登录：`http://127.0.0.1:8000/login/`

建议从零走通顺序：登录 → 新增凭证 → 新增节点并 SSH 探测 → 配置同步 → 发布中心 → 按节点情况做安装 / 升级 / 启停 / 卸载。

---

## 功能模块与 URL 速查

| 模块 | 路径 | 说明 |
|------|------|------|
| 仪表盘 | `/` | 统计与最近任务 |
| 登录 / 个人中心 | `/login/`、`/profile/` | 账户 |
| 节点 | `/nodes/` | 节点与节点组 |
| 凭证 | `/credentials/` | SSH 凭证 |
| 配置 | `/configs/`、`/configs/sync/` | 配置列表与同步向导 |
| 发布中心 | `/releases/center/` | 勾选发布 |
| 任务中心 | `/releases/history/` | 全平台异步任务列表与详情 |
| 发布历史 | `/releases/list/` | 发布/回滚历史（按批次，与任务中心不同页） |
| Nginx 升级 | `/upgrade/`、`/upgrade/center/` | 升级首页与向导；源码包/模块包 |
| Nginx 安装 | `/nginx-install/` | 全新源码安装 |
| Nginx 启停 | `/nginx-service/` | start / stop / reload / restart |
| Nginx 卸载 | `/nginx-uninstall/` | 卸载与路径清理 |
| 用户 / 角色 / 用户组 | `/users/`、`/users/roles/`、`/users/teams/` | RBAC |
| 审计 | `/audit/`、`/audit/login/` | 操作与登录日志 |
| 系统设置 | `/settings/` | 运行参数 |

更细的接口与业务规则见 [`docs/`](docs/README.md)。

---

## 仓库目录结构

```text
mngxops/
├── apps/           # 业务应用（accounts、nodes、configs、releases、upgrade、nginx_* 等）
├── ngxops/         # Django 项目配置（settings、urls、wsgi）
├── utils/          # 共享工具（SSH、nginx 启停、设置读取、加密、分页、数据保留）
├── templates/      # 全局布局与错误页
├── media/          # 上传文件（源码包、头像等）
├── docs/           # 软件需求设计文档
├── run_server.py   # 二进制入口（Waitress + migrate/createsuperuser）
├── mngxops.spec    # PyInstaller 规格（目标本机构建，产物名含平台/glibc/架构）
├── manage.py
├── requirements.txt
├── AGENTS.md       # 优化点结论台账（唯一来源）
└── README.md       # 本手册
```

二进制交付见 [`docs/packaging.md`](docs/packaging.md)。

---

## 配置与数据提示

- **项目配置**：[`ngxops/settings.py`](ngxops/settings.py)（语言 `zh-hans`、时区 `Asia/Shanghai`、`MEDIA` 等）。
- **业务运行参数**：优先在系统设置页修改（SSH 超时、批量勾选上限、备份目录、数据保留天数等），由 `get_setting` 读取。
- **凭证加密**：Fernet 密钥文件位于 `utils/.fernet_key`（勿提交到公开仓库；丢失将无法解密已存密文）。
- **发布备份**：远程路径形如 `{backup_dir}/{hostname}/…`，备份根目录可在系统设置中配置。
- **数据清理**：可配置保留天数；亦可执行 `python manage.py purge_expired_data`（会跳过进行中的任务）。

生产环境请自行调整 `DEBUG`、`SECRET_KEY`、`ALLOWED_HOSTS` 与数据库，并做好密钥与 `media/` 备份。

---

## 延伸阅读

| 文档 | 说明 |
|------|------|
| [docs/packaging.md](docs/packaging.md) | PyInstaller 单二进制构建与三平台交付 |
| [docs/README.md](docs/README.md) | 软件需求设计文档索引（架构、各模块、API、非功能） |
| [docs/00-overview.md](docs/00-overview.md) | 产品定位、术语表、模块地图 |
| [AGENTS.md](AGENTS.md) | 优化点结论台账（Q1–Q159；唯一来源） |
| [.cursor/rules/](.cursor/rules/) | Cursor 项目规则（开发规范；可随仓库共享） |
