# 11 · 审计与登录日志（audit）

## 1. 模块目标与范围

记录关键业务 CRUD 与异步任务创建；独立登录日志；列表筛选与任务软链。

**不做**：完整 SIEM 导出、不可篡改 WORM 存储。

## 2. 角色与权限

`audit.read`（及 create/update/delete 权限项存在，列表以 read 为主）。

## 3. 领域模型

[`apps/audit/models.py`](../apps/audit/models.py)

### AuditLog

`user`、`module`、`action`、`ip`、`result`(success/failed)、`detail`、`task_center_id`、`source_batch`、`created_at`。

### LoginLog

`username`、`ip`、`user_agent`、`status`、`fail_reason` 枚举。

## 4. 页面与路由

| 路径 | 说明 |
|------|------|
| `/audit/` | 操作日志列表/详情态 |
| `/audit/login/` | 登录日志 |

筛选：模块、结果、今天/7/30 天、多标签搜索（Q70）。

## 5. 写入来源

### 5.1 模型信号

[`apps/audit/signals.py`](../apps/audit/signals.py)：`TRACKED_MODELS` 的 post_save/post_delete → AuditLog。  
当前写入 **`result=success` 为主**（Q94 结论：表示变更已落库；失败表单/API 不一定记 failed）。

### 5.2 任务中心

创建 TaskCenter 时 `log_task_center_created`（[`apps/audit/utils.py`](../apps/audit/utils.py)），填充 `task_center_id`、`source_batch`；`OPERATION_AUDIT_MAP` 映射类型文案（含未使用的 discover/drift/glob，Q87）。

### 5.3 登录

accounts 登录成功/失败写 LoginLog（及可选 AuditLog）。

### 5.4 中间件

- `CurrentUserMiddleware`：供信号取当前用户与 IP。  
- `AjaxErrorMiddleware`：AJAX 401/403 JSON。

## 6. 用例

1. 运维查询某模块近期操作。  
2. 详情若有 `task_center_id` → 「查看任务」进任务中心详情；否则按 `MODULE_LINK_MAP` 跳模块列表。  
3. 登录审计排查锁定/错密原因。

## 7. 实现要点

- 保留天数：`system.retention_audit_log_days` / `retention_login_log_days`。  
- 视图：[`apps/audit/views.py`](../apps/audit/views.py)。

## 8. 前后端约定

- 查询布局对齐全局 tag/时间快捷（Q70）。  
- 列表 data-table / 分页规范。

## 9. 异常与边界

- 无用户上下文的系统操作可能缺少 user（信号需兜底）。  
- 信号成功口径表示落库成功，不等于覆盖全部业务失败（Q94 已关闭结论）。

## 10. 关联模块

全站写模型的应用、releases TaskCenter、accounts、settings 保留。

## 11. 已落地优化索引

Q70。

## 12. 相关优化结论

Q87/Q94 见 [90-gap-and-optimization.md](90-gap-and-optimization.md)。
