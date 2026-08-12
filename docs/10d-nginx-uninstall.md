# 10d · Nginx 卸载（nginx_uninstall）

## 1. 模块目标与范围

运维工具下与「Nginx 升级 / 安装 / 启停」同级的独立入口：对已装 Nginx 的节点卸载（源码删树或包管理器 remove），并回写节点 Nginx 状态（对齐 Q150 orphan）。

- 独立应用 [`apps/nginx_uninstall`](../apps/nginx_uninstall)，独立流水线 `run_uninstall_batch`。
- **不调用、不修改**升级/安装执行流水线。

**不做**：物理删除平台库发布历史 / BindingVersion；远程 `kill -9`；卸载回滚；删除 `/lib`、`/usr/lib` 下发行版 systemd unit 文件；`apt-get purge`（仅 `remove`）。

**包安装（Q154）**：若 `rpm -qf` / `dpkg -S` 能归属 nginx 二进制到软件包，则 `dnf`/`yum`/`apt-get remove -y <pkg>`，**不**对 `--prefix` / `-V` 路径做 `rm`。

**systemd（Q153）**：源码安装且 systemctl 托管时 `disable` + 删 `/etc/systemd/system` 可管 unit；包安装后仅清理仍残留的平台自写 unit；发行版 unit 交由包卸载。

## 2. 角色与权限

| 能力 | 权限 |
|------|------|
| 菜单与页面 | `nodes.read` |
| 执行卸载 | `nodes.update` |

不新增独立 RBAC 资源码。

## 3. 页面与路由

| 路径 | 说明 |
|------|------|
| `/nginx-uninstall/` | 运维台首页（统计 + 最近卸载任务） |
| `/nginx-uninstall/center/` | 三步向导 + 右栏执行进度（对齐升级中心） |
| `/nginx-uninstall/history/` | 卸载历史 |
| `/nginx-uninstall/task/<id>/log/` | 卸载任务详情（完整日志，对齐升级任务详情样式） |
| `GET /nginx-uninstall/api/task/<id>/log/` | 任务日志轮询 API |
| `POST /nginx-uninstall/api/preview/` | 探测 `nginx -V`、包归属、托管方式与路径清单 |
| `POST /nginx-uninstall/api/create/` | 创建批次并异步执行（按节点 `selected_paths`） |
| `GET /nginx-uninstall/api/batch-progress/?batch=` | 批次进度（含 `task_id` / `log_url`→任务详情） |

中心布局：左向导三步，右 sticky「卸载执行进度」；**不使用**全局 `#asyncProgressOverlay`。右栏「查看完整日志」与历史/首页详情入口跳转 `/nginx-uninstall/task/<id>/log/`。

最近条数：`dashboard.recent_tasks_count`；批量上限：`node.batch_max_count`。

### 向导步骤

1. **选择目标**：仅 `online` + 启用凭证 + `nginx_available`；「未检测到」不可选。  
2. **探测路径**：节点可折叠；展示运行态、**安装来源（包/源码）**、托管方式；包安装时 `-V` 路径只读不勾选 rm。  
3. **确认执行**：摘要（安装来源 + 托管）+ 确认勾选 + 开始卸载；运行中 / 包安装 / 非 root+systemctl 可合并 `showConfirm`。

原型：[`docs/prototypes/nginx-uninstall-wizard-prototype.html`](../docs/prototypes/nginx-uninstall-wizard-prototype.html)。

## 4. 删除范围

进入第 2 步探测 `nginx -V`：提取 `--prefix` 与名含 `-path` 的绝对路径参数；另追加设置项。同时探测包归属（`install_origin` / `package_name`）。

| 来源 | 项 | 默认 |
|------|----|------|
| nginx -V | `--prefix`（源码必选可编辑）及 `*-path` | 源码：prefix/sbin/modules/conf/pid 默认勾选；**包安装：不勾选、不 rm** |
| 系统设置 | 发布备份 `{backup_dir}/{hostname}/` | 默认勾选（包/源码均可） |
| 系统设置 | 编译工作目录 | 默认不勾 |
| 系统设置 | `{work_dir}/nginx-modules` | 默认不勾 |

不纳入 `--add-module=` 源码路径（仍用设置项 `nginx-modules`）。源码路径执行时对位于已删 `--prefix` 下的路径去重跳过；目录 `rm -rf`、文件 `rm -f`。危险路径仅校验最终删除集合（包安装跳过 prefix 危险校验）。

### 4.1 收敛到 `…/nginx` 目录

除 `--prefix` 外，探测展示与执行前将路径收敛到最右侧段名为 `nginx` 的目录，避免只删 `nginx.conf` 而残留 `conf.d` 等：

| 原始路径 | 收敛结果 |
|----------|----------|
| `/etc/nginx/nginx.conf` | `/etc/nginx` |
| `/var/log/nginx/error.log` | `/var/log/nginx` |
| `/usr/lib64/nginx/modules` | `/usr/lib64/nginx` |
| `/usr/sbin/nginx` | **不收敛**（`sbin`/`bin` 下二进制例外） |
| `/run/nginx.pid` | 不收敛（无名为 `nginx` 的目录段） |

### 4.2 父子路径去重

最终删除集合若同时含父目录与子路径（如 `/etc/nginx` 与 `/etc/nginx/conf.d/test.conf`），只保留最外层，避免先删父目录再删子路径。

探测路径列表支持点击整行切换勾选（必选 `--prefix`、包占用路径除外）。

## 5. 执行进度

对齐升级中心右栏：批次号、总进度条、按节点步骤条、「查看完整日志」、取消（TaskCenter）。「查看完整日志」跳转卸载任务详情页（`/nginx-uninstall/task/<id>/log/`，样式对齐升级任务详情）。

步骤：连接远程 → 停止 Nginx → **包管理器卸载** 或 **删除安装目录** → 清理发布备份 → 清理额外路径 → 清理 systemd（按需）→ 更新节点状态。

## 6. 运行中与权限确认

- 运行中：流水线在 `stop_if_running=true` 时先 `stop_nginx`。  
- 包安装 / 非 root+systemctl：开始前 `showConfirm`（可与停服合并）。  
- 包 remove 与清理 unit 均需可写系统目录或 `sudo -n`；无权限 → 本节点失败。

## 7. 领域模型

`NginxUninstallTask`：批次 `UN-YYMMDD-NNNN`、节点、`resolved_prefix`、`options_json`（含 `extra_paths` / `remove_*` / `install_origin` / `package_*`）、状态/进度/日志（含 `removing_package`）、关联 `TaskCenterTask(operation_type=nginx_uninstall)`。

## 8. 执行流水线

1. 门禁；源码路径做危险路径校验，包安装跳过 prefix 危险校验  
2. 若运行中且允许：`stop_nginx`  
3. 探测包归属：  
   - **包**：`removing_package` → `dnf`/`yum`/`apt-get remove -y`；不 rm prefix  
   - **源码**：`removing_prefix` → 删除 prefix  
4. 按选项删除备份 / 工作目录 / modules / `extra_paths`（去重；包安装忽略 -V extra）  
5. systemd：源码且 systemctl → `removing_unit`；包安装仅当存在 `/etc/systemd/system/nginx.service` 时清理  
6. 清空 `nginx_path`/`nginx_version`；`apply_nginx_probe_result(False)`；清空 `main_conf_path`  
7. 写 TaskCenter 结果树；协作式取消（Q109）

## 9. 实现锚点

- 应用：`apps/nginx_uninstall`
- TaskCenter：`nginx_uninstall`
- 审计：`OPERATION_AUDIT_MAP["nginx_uninstall"]`
- 结论：见 [`AGENTS.md`](../AGENTS.md) Q152 / Q153 / Q154
