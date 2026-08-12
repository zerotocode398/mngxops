# 10d · Nginx 卸载（nginx_uninstall）

## 1. 模块目标与范围

运维工具下与「Nginx 升级 / 安装 / 启停」同级的独立入口：对已装 Nginx 的节点删除 `--prefix` 安装树及相关可选目录，并回写节点 Nginx 状态（对齐 Q150 orphan）。

- 独立应用 [`apps/nginx_uninstall`](../apps/nginx_uninstall)，独立流水线 `run_uninstall_batch`。
- **不调用、不修改**升级/安装执行流水线。

**不做**：apt/yum 包卸载；删除 systemd unit；物理删除平台库发布历史 / BindingVersion；远程 `kill -9`；卸载回滚。

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
| `/nginx-uninstall/center/` | 两步：选节点 → 确认路径与删除范围 |
| `/nginx-uninstall/history/` | 卸载历史 |
| `POST /nginx-uninstall/api/preview/` | 预览每节点 prefix / 备份路径 / 是否运行中 |
| `POST /nginx-uninstall/api/create/` | 创建批次并异步执行 |
| `GET /nginx-uninstall/api/batch-progress/?batch=` | 批次内各卸载任务进度 |

进度反馈：全局 `#asyncProgressOverlay` 轮询 TaskCenter；「完整日志」跳任务中心详情。

最近条数：`dashboard.recent_tasks_count`；批量上限：`node.batch_max_count`。

## 4. 删除范围

| 项 | 默认 | 说明 |
|----|------|------|
| `--prefix` 安装树 | 必选 | `rm -rf <prefix>` |
| 远程发布备份子目录 | 默认勾选 | `{release.backup_dir}/{safe_hostname}/`，仅本节点 |
| 编译工作目录 | 默认不勾 | 如 `upgrade.default_work_dir` |
| 第三方模块目录 | 默认不勾 | `{work_dir}/nginx-modules` |

危险路径（空、`/`、`/usr`、`/opt`、`/tmp` 等过短根）后端硬拒绝。

## 5. 运行中处理

检测到 Nginx 运行中时，前端 `showConfirm`：「停止并继续卸载」。请求带 `stop_if_running=true` 时流水线先 `stop_nginx` 再删目录；未确认则该节点失败。

## 6. 领域模型

`NginxUninstallTask`：批次 `UN-YYMMDD-NNNN`、节点、`resolved_prefix`、选项 JSON、状态/进度/日志、关联 `TaskCenterTask(operation_type=nginx_uninstall)`。

## 7. 执行流水线

1. 门禁 + 危险路径校验  
2. 若运行中且允许：`stop_nginx`  
3. 删除 prefix  
4. 可选：删除发布备份子目录 / 工作目录 / nginx-modules  
5. 清空 `nginx_path`/`nginx_version`；`apply_nginx_probe_result(False)`；清空 `ConfigSyncSetting.main_conf_path`  
6. 写 TaskCenter 结果树；支持协作式取消（Q109）

## 8. 实现锚点

- 应用：`apps/nginx_uninstall`
- TaskCenter：`nginx_uninstall`
- 审计：`OPERATION_AUDIT_MAP["nginx_uninstall"]`
- 结论：见 [`AGENTS.md`](../AGENTS.md) Q152
