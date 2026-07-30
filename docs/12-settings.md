# 12 · 系统设置（settings）

## 1. 模块目标与范围

GitLab 式分组设置页；仅维护**已接线**的 `PRESET_SETTINGS`；保存即时/按说明时机生效；数据保留清理。

**不做**：任意动态增删未接线键的产品化配置中心（防「改了不生效」）。

## 2. 角色与权限

`settings.read|update`（及 create/delete 项）。

## 3. 领域模型

`SystemSetting`：`key` 唯一、`value`、`type`、`group`、`label`、`description`、`placeholder`、`options`、`is_required`、`sort_order`、审计字段。

启动：`apps/settings/apps.py` → `seed_default_settings()` 写入元数据，**不覆盖**已有 value。

读取：`utils/setting_service.get_setting` + cache；保存刷新缓存。

## 4. 页面与路由

| 路径 | 说明 |
|------|------|
| `/settings/` | 左分组导航 + 右 side-by-side（Q72/Q74） |
| `save/` | JSON 保存 |
| `api/group/`、`api/all/` | 分组/全量 |

未保存离开确认；`?group=` + localStorage 记住分组（Q72）。全宽无 max-width 居中浪费（Q74）。

## 5. PRESET 全表（已接线）

### 仪表盘

| key | 生效 |
|-----|------|
| `dashboard.recent_tasks_count` | 刷新仪表盘（默认 20；任务中心最近记录） |

### 节点管理

| key | 生效 |
|-----|------|
| `node.batch_max_count` | 后端立即；勾选上限需刷新列表 |
| `node.ssh_connect_timeout` | 下次 SSH |
| `node.ssh_default_port` | 仅新建/导入默认 |
| `node.detect_retries` | 下次连接 |

### 凭证

| key | 生效 |
|-----|------|
| `credential.test_max_concurrency` | 下次启用测试 |

### 配置管理

| key | 生效 |
|-----|------|
| `config.discover_max_depth` | 下次发现/同步 |
| `config.default_nginx_path` | 仅新建/空同步设置 |
| `config.default_nginx_bin` | 新建/导入默认 Nginx 路径 |
| `config.sync_max_concurrency` | 后端立即；向导勾选上限需刷新 |

### 发布管理

| key | 生效 |
|-----|------|
| `release.max_parallel_tasks` | 下次发布 |
| `release.backup_dir` | 下次发布；实际 `{dir}/{hostname}/` |

### 系统

| key | 生效 |
|-----|------|
| `system.task_progress_poll_interval` | 刷新页面 |
| `system.dashboard_refresh_interval` | 刷新页面 |
| `system.retention_task_center_days` | 次日清理；0=不清理 |
| `system.retention_release_history_days` | 同上 |
| `system.retention_audit_log_days` | 同上 |
| `system.retention_login_log_days` | 同上 |

### Nginx 升级

| key | 生效 |
|-----|------|
| `upgrade.default_work_dir` | 升级中心默认；与 sessionStorage baseline（Q81） |
| `upgrade.make_jobs_default` | 同上 |
| `upgrade.package_max_size_mb` | 上传校验立即 |

## 6. 数据保留

- 中间件：`DataRetentionMiddleware` 触发 `maybe_run_daily_purge`。  
- 命令：`manage.py purge_expired_data`。  
- 实现：[`utils/data_retention.py`](../utils/data_retention.py)。  
- **跳过** pending/running 任务。

## 7. 实现要点

| 能力 | 路径 |
|------|------|
| PRESET | `apps/settings/models.py` |
| 读写服务 | `utils/setting_service.py` |
| 模板注入 | `context_processors.system_runtime_settings` |
| 视图 | `apps/settings/views.py` |

原型曾位于 `docs/prototypes/settings-gitlab-prototype.html`（仓库可能未纳入版本库）。

## 8. 前后端约定

- description 必须写清生效时机（Q75/Q81）。  
- 禁止再堆「未接线」展示项。

## 9. 异常与边界

- cache TTL 内多进程可能短暂读到旧值；保存侧有 refresh。  
- 保留 0 天表示关闭清理。

## 10. 关联模块

几乎全部通过 `get_setting` 消费；upgrade 表单 baseline；dashboard 刷新。

## 11. 已落地优化索引

Q40、Q61、Q72–Q75、Q81。

## 12. 待确认缺口

无单独 Q；若新增设置必须同时改 PRESET + 调用点 + 本文档。
