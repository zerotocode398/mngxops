# 10c · Nginx 全新安装（nginx_install）

## 1. 模块目标与范围

运维工具下与「Nginx 升级」「Nginx 启停」同级的独立入口：对**尚无可用 Nginx**的在线节点做源码编译安装。

- 独立应用 [`apps/nginx_install`](../apps/nginx_install)，独立流水线 `run_install_task`。
- **不调用、不修改** [`apps/upgrade/services.py`](../apps/upgrade/services.py) 的 `run_upgrade_task`。
- 只读复用升级模块的源码包 / 第三方模块包。

**不做**：apt/yum 包安装编排；修改升级平滑路径逻辑。

## 2. 角色与权限

复用 `upgrade.read|create|update|delete`（与编译安装同一能力域）。

## 3. 与升级的差异

| | Nginx 升级 | Nginx 安装 |
|--|--|--|
| 目标机 | 已有 nginx | 无可用 nginx |
| 基线 | 拉 `nginx -V` | 无；用户填 `--prefix` + 模块 |
| 备份旧二进制 | 有 | 无 |
| 装完动作 | `nginx -t` + reload | `nginx -t` + **start** |
| 节点回写 | 仅 `nginx_version` | `nginx_version` + `nginx_path` + `mark_node_probe_success` |
| 配置 | 无自动同步 | 成功后**自动配置同步** |

升级向导已隐藏「全新安装」选项，引导至本模块。

## 4. 页面与路由

| 路径 | 说明 |
|------|------|
| `/nginx-install/` | 运维台首页 |
| `/nginx-install/center/` | 三步安装向导 |
| `/nginx-install/history/` | 安装历史 |
| `POST /nginx-install/api/create/` | 创建批次 |
| `GET /nginx-install/api/batch-progress/?batch=` | 批次进度 |
| `GET /nginx-install/api/task/<id>/log/` | 任务日志 |

## 5. 领域模型

`NginxInstallTask`：批次号 `NI-YYMMDD-XXXX`、节点、源码包、prefix、configure、进度/日志、`sync_ok`/`sync_detail`、关联 `TaskCenterTask(operation_type=nginx_install)`。

## 6. 执行流水线

实现：`apps/nginx_install/services.py` `run_install_task`。

1. gcc/make 预检  
2. 上传/解压源码包、准备第三方模块（复用 upgrade 助手函数，不改其行为）  
3. configure → make → make install  
4. `nginx -t` → `start_nginx`  
5. 回写 `nginx_version` / `nginx_path`，`mark_node_probe_success`，写入 `ConfigSyncSetting.main_conf_path={prefix}/conf/nginx.conf`  
6. `discover_nginx_configs` + `sync_discovered_configs`  

**同步失败口径**：安装仍算成功；结果/历史标同步失败；引导手动同步；不回滚 nginx。

## 7. 实现锚点

- 应用：`apps/nginx_install`
- 侧栏：`templates/base.html` → 运维工具 → Nginx 安装
- TaskCenter 类型：`nginx_install`
