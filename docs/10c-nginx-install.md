# 10c · Nginx 全新安装（nginx_install）

## 1. 模块目标与范围

运维工具下与「Nginx 升级」「Nginx 启停」同级的独立入口：对**尚无可用 Nginx**的在线节点做源码编译安装。

- 独立应用 [`apps/nginx_install`](../apps/nginx_install)，独立流水线 `run_install_task`。
- **不调用、不修改** [`apps/upgrade/services.py`](../apps/upgrade/services.py) 的 `run_upgrade_task`。
- 只读复用升级模块的源码包 / 第三方模块包。

**不做**：apt/yum 包安装编排；修改升级平滑路径逻辑；安装回滚。

## 2. 角色与权限

复用 `upgrade.read|create|update|delete`（与编译安装同一能力域）。

## 3. 与升级的差异

| | Nginx 升级 | Nginx 安装 |
|--|--|--|
| 目标机 | 已有 nginx | 无可用 nginx |
| 基线 | 拉 `nginx -V` | 无；用户填 `--prefix` / `--user` / `--group` + 模块 |
| 备份旧二进制 | 有 | 无 |
| 装完动作 | `nginx -t` + reload | `nginx -t` + **start** |
| 节点回写 | 仅 `nginx_version` | `nginx_version` + `nginx_path` + `mark_node_probe_success` |
| 配置 | 无自动同步 | 成功后**自动配置同步** |

升级向导已隐藏「全新安装」选项，引导至本模块。

## 4. 页面与路由

| 路径 | 说明 |
|------|------|
| `/nginx-install/` | 运维台首页（最近安装任务条数 = `dashboard.recent_tasks_count`） |
| `/nginx-install/center/` | 三步安装向导 + 右栏执行进度 |
| `/nginx-install/history/` | 安装历史 |
| `/nginx-install/task/<id>/log/` | 安装任务详情 / 完整执行日志（可轮询刷新） |
| `POST /nginx-install/api/create/` | 创建批次 |
| `GET /nginx-install/api/batch-progress/?batch=` | 批次进度 |
| `GET /nginx-install/api/task/<id>/log/` | 单任务日志 JSON（日志页轮询） |

## 5. 安装向导

中心页左右栏：左向导、右 sticky「安装执行进度」。

1. **选择目标**：多选在线节点 + 源码包。  
2. **编译参数**（纵向）：
   - 顶部紧凑基础参数：`--prefix` / `--user` / `--group`、工作目录、`make -j`（缺省读「安装管理」设置）。  
   - 支持模块：摘要「已选 N」+「调整模块」弹窗（左右栏绝对选集，对齐升级交互；默认 `DEFAULT_INSTALL_MODULES`）；可选 `#extraOpts` 自定义行（无标签文案）。  
   - 全宽第三方模块：在线 Git / 离线包（对齐升级；离线包共用模块包管理 `?nav=nginx_install`）。  
3. **确认安装**：KV 摘要、`./configure` 预览（含 `--add-module`）、节点清单、确认勾选。

开跑后右栏按节点展示步骤；轮询重建时保留用户已展开的节点块。失败信息对齐升级（最多 5 行 +「失败:」前缀）。「查看完整日志」新窗口打开任务日志页（编译参数展示含 `./configure \`）。不使用全局进度遮罩。

## 6. 系统设置（安装管理）

| key | 默认 | 说明 |
|-----|------|------|
| `install.default_user` | `root` | 向导 `--user` 缺省 |
| `install.default_group` | `root` | 向导 `--group` 缺省 |
| `install.default_prefix` | `/opt/app` | 向导 `--prefix` 缺省 |

详见 [12-settings.md](12-settings.md)。

## 7. 领域模型

`NginxInstallTask`：批次号 `IN-YYMMDD-NNNN`（当日自增）、节点、源码包、prefix、configure、`added_modules` / `added_third_party`、进度/日志、`sync_ok`/`sync_detail`、关联 `TaskCenterTask(operation_type=nginx_install)`。首页「最近安装任务」与任务中心详情「来源批次」可跳转安装历史并按批次过滤。

## 8. 执行流水线

实现：`apps/nginx_install/services.py` `run_install_task`。

1. gcc/make 预检  
2. 上传/解压源码包、准备第三方模块（复用 upgrade 助手）  
3. configure → make → make install  
4. `nginx -t` → `start_nginx`  
5. 回写版本/路径，`mark_node_probe_success`，写入 `main_conf_path`  
6. 自动配置同步（失败不否定安装、不回滚）

## 9. 实现锚点

- 应用：`apps/nginx_install`
- 官方模块：`apps/upgrade/builtin_modules.py`
- TaskCenter：`nginx_install`
- 结论：Q132 / Q133 / Q134 / Q136 / Q139 / Q140 / Q141 / Q142 / Q143 / Q144（见 [`AGENTS.md`](../AGENTS.md)）
