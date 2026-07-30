# 90 · 缺失需求与设计优化清单

本文档与 [`AGENTS.md`](../AGENTS.md) **Q84–Q95** 双向索引。状态均为 **待确认**：确认前不改业务代码；确认后按条目实施或关闭。

需求基线文档集见 [README.md](README.md)。下列「建议方案」供评审选用其一，实施时再落代码。

---

## 总览

| 编号 | 摘要 | 模块文档 | 类型 |
|------|------|----------|------|
| Q84 | `conflict` 状态无写入 | [07-configs](07-configs.md) | 缺失 / 死状态 |
| Q85 | `syncing` 状态无写入 | [07-configs](07-configs.md) | 缺失 / 死状态 |
| Q86 | 配置漂移检测未实现 | [07-configs](07-configs.md) | 缺失能力 |
| Q87 | TaskCenter 类型与实现不一致 | [09-task-center](09-task-center.md) | 设计不一致 |
| Q88 | 任务中心列表/详情权限不对称 | [09-task-center](09-task-center.md) | 不合理设计 |
| Q89 | `ReleaseTask.status=rollback` 未使用 | [08-releases](08-releases.md) | 死枚举 |
| Q90 | 发布中心绑定含 `marked_deleted` | [08-releases](08-releases.md) | 待明确产品规则 |
| Q91 | 版本恢复直接标 `synced` | [07-configs](07-configs.md) | 不合理设计 |
| Q92 | Glob 预览多节点只取 first | [07-configs](07-configs.md) | Bug/缺口 |
| Q93 | 全局 running 阻断新发布 | [08-releases](08-releases.md) | 不合理设计 |
| Q94 | 审计信号恒为 success | [11-audit](11-audit.md) | 观测缺口 |
| Q95 | 遗留 ConfigVersion 与双版本路由 | [07-configs](07-configs.md) | 技术债 |

---

## 明细

### Q84 · `conflict` 同步状态无写入

- **现象**：模型 choices、配置列表/发布中心/仪表盘均统计或展示「冲突」，业务代码无赋值 `sync_status=conflict`。
- **证据**：`apps/configs/models.py` choices；计数于 `apps/configs/views.py`、`apps/dashboard/views.py`、`apps/releases/views.py`；无 `sync_status="conflict"` 写入（仅 filter/count）。
- **建议**：实现「远程与本地内容不一致」检测并置 conflict；或下线 UI/统计与 choice，避免误导。
- **状态**：待确认

### Q85 · `syncing` 同步状态无写入

- **现象**：异步同步过程中绑定不进入 `syncing`，过滤标签长期为 0。
- **证据**：badge 于 `config_filters.py` / 列表模板；同步线程未更新该状态。
- **建议**：同步开始置 `syncing`、结束置终态；或移除 UI 状态。
- **状态**：待确认

### Q86 · 配置漂移检测未实现

- **现象**：存在 `remote_content_hash`、`drift_detected_at`、`operation_type=config_drift_check`，无定时/手动漂移任务；hash 主要在发布成功时写入。
- **证据**：`apps/configs/models.py`；`TaskCenterTask` / `audit/utils.py` 枚举；无 creator。
- **建议**：实现巡检任务对比远程 MD5；或删除/冻结字段与枚举并改文档。
- **状态**：待确认

### Q87 · TaskCenter 操作类型与实现不一致

- **现象**：`config_discover`、`config_glob_preview` 在枚举与审计映射中；实际发现走 `config_batch_sync`；Glob 预览为同步 HTTP、不建 TaskCenter。
- **证据**：`apps/releases/models.py` OPERATION_TYPE；`apps/audit/utils.py` OPERATION_AUDIT_MAP；`ConfigSyncBatchAPIView`；`ConfigGlobPreviewView`。
- **建议**：创建任务时改用精确类型，或收紧枚举与审计映射。
- **状态**：待确认

### Q88 · 任务中心列表与详情权限不对称

- **现象**：仅 `nodes.update` 时，列表只保留本人 `node_batch_test`；详情 queryset 还允许本人 `config_batch_sync`。`node_ssh_test` 等亦可能不一致。
- **证据**：`TaskCenterListView.get_queryset` vs `TaskCenterDetailView.get_queryset`（约 L1105–1108 与 L1164–1167）。
- **建议**：统一允许的 `operation_type` 集合与过滤条件。
- **状态**：待确认

### Q89 · `ReleaseTask.status=rollback` 未使用

- **现象**：回滚创建新 `ReleaseTask`，源任务保持 success/failed，不改为 `rollback`。
- **证据**：`STATUS_CHOICES` 含 rollback；`ReleaseRollbackView` 新建任务。
- **建议**：回滚成功后回写源任务状态；或删除无用 choice 并清理展示。
- **状态**：待确认

### Q90 · 发布中心返回 `marked_deleted` 绑定

- **现象**：`ReleaseNodeBindingsAPIView` 未排除 `marked_deleted`；状态计数含该类；是否允许发布待删除配置不清晰。
- **证据**：`apps/releases/views.py` 绑定查询与 status_counts。
- **建议**：默认排除不可发布；或允许但禁用勾选并提示。
- **状态**：待确认

### Q91 · 绑定版本恢复可直接标 `synced`

- **现象**：恢复到 `synced_version` 对应版本时本地标 `synced`，不比对远程文件。
- **证据**：`BindingVersionRestoreView`（约 L579+）。
- **建议**：恢复仅标 `modified`，或 SSH 校验远程后再标 `synced`。
- **状态**：待确认

### Q92 · Glob 预览多节点只取 `.first()`

- **现象**：API 接受多个 `node_ids`，处理仅第一个节点。
- **证据**：`ConfigGlobPreviewView`。
- **建议**：按节点循环返回；或前端限制单选并校验。
- **状态**：待确认

### Q93 · 任意 running 发布阻断新批次

- **现象**：`ReleaseTask.objects.filter(status="running").exists()` 为真则拒绝新自动执行，全局而非按操作者/节点。
- **证据**：`ReleaseCreateAPIView` 约 L1729。
- **建议**：改为按操作者、按节点集合或可配置开关；并文档化。
- **状态**：待确认

### Q94 · 审计 CRUD 信号结果多为 success

- **现象**：模型信号写 AuditLog 时结果固定成功语义；业务失败路径不一定记 failed。
- **证据**：`apps/audit/signals.py`。
- **建议**：关键 API 显式 `log(..., result=failed)`；或文档限定「仅记录落库成功的变更」。
- **状态**：待确认

### Q95 · 遗留 `ConfigVersion` 与双版本路由

- **现象**：旧模型仍在；`/configs/<pk>/versions/` 与 `/configs/bindings/<pk>/versions/` 并存，pk 语义易混（曾导致 Q44）。
- **证据**：`apps/configs/models.py` ConfigVersion 注释待废弃；`apps/configs/urls.py` 兼容路由。
- **建议**：数据迁移清理后删模型与旧路由；过渡期旧路由 301/提示。
- **状态**：待确认

---

## 维护规则

1. 新增缺口续编 **Q96+**，同步改 AGENTS 总览与本文件。  
2. 关闭时在 AGENTS 写清结论（不做 / 已完成），本文件移入「已关闭」或删除行并留链接。  
3. 需求正文（02–12）只描述**已实现**行为；缺口不写入「已确认行为」。
