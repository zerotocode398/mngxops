# 09 · 任务中心（TaskCenter）

## 1. 模块目标与范围

统一展示与追踪平台异步任务：列表摘要、详情结果树、进度轮询 API、与审计/发布批次软链。

**路由注意**：列表 URL 为 `/releases/history/`（历史命名）；**发布历史**为 `/releases/list/`。

## 2. 角色与权限

- 列表/详情：`releases.read` **或** `nodes.update`。  
- 仅有 `nodes.update` 时：列表与详情均可见本人触发的 `node_batch_test` 与 `config_batch_sync`（Q88 已统一）。

## 3. 领域模型

`TaskCenterTask` 字段见 [14-data-model.md](14-data-model.md) / [01-architecture.md](01-architecture.md)。

### operation_type

| 类型 | 实际创建方 |
|------|----------|
| `release_publish` / `release_rollback` | 发布/回滚 |
| `credential_enable_test` | 凭证启用 |
| `node_ssh_test` / `node_batch_test` | 节点测试 |
| `node_system_info` / `node_nginx_version` | 采集 |
| `config_batch_sync` | 配置同步（发现也走此类型） |
| `config_discover` / `config_drift_check` / `config_glob_preview` | 枚举保留兼容；筛选下拉已隐藏；业务不新建（Q86/Q87） |
| `nginx_upgrade` / `nginx_rollback` | 升级 |
| `other` | 兜底 |

## 4. 页面与路由

| 路径 | View | 模板 |
|------|------|------|
| `/releases/history/` | `TaskCenterListView` | `task_center.html` |
| `/releases/tasks/<pk>/` | `TaskCenterDetailView` | `task_detail.html` |
| `/releases/tasks/progress/` | `TaskCenterProgressAPIView` | JSON |

## 5. 结果树协议

实现：[`apps/releases/task_result.py`](../apps/releases/task_result.py)（Q71）。

标准文本结构：

```text
执行完成：成功 N，失败 M，共 T
[节点] IP (hostname)
  [成功] label
  [失败] label - 失败原因: ...
```

辅助函数：`node_header`、`item_success`、`item_failed`、`build_tree_result`、`format_task_center_summary`、`targets_from_release_tasks`。

详情页解析：按 `[节点]` / `  [成功]` / `  [失败]` 切分；**失败置顶并默认展开**，成功折叠；展示总耗时（Q33/Q34）。无「原始日志」大段折叠区（Q35）；发布过程弹窗也不再堆 SSH 折叠（Q80）。

列表摘要：主/次两行，避免空白过大（Q28）；去掉行内展开（Q29）。

## 6. 进度轮询

### 6.1 全局 Overlay

`base.html`：`showAsyncProgressOverlay` + 轮询 progress API；间隔 `system.task_progress_poll_interval` → `sys_poll_interval_ms`。  
完成：成功/失败态；链「完整日志」到任务详情或按批次打开发布历史（Q45）。

### 6.2 Progress API 字段（典型）

`status`、`progress`、`detail`、`result`、`log_output`；发布进行中可含 `current_steps`（实时树）。

### 6.3 模块自有进度

升级中心 batch-progress、凭证 enable-progress 等可并存，但长操作统一 TaskCenter 可追踪（Q39）。

## 7. 用例

1. 任意模块触发异步作业 → 写 TaskCenter → 前端 overlay。  
2. 用户打开任务中心：筛选/搜索（多词 + batch/node_ip，Q35）；发布类显示批次链发布历史（Q30）；批次超链 `target="_blank"`（Q36）。  
3. 进入详情查看结果树与日志；审计「查看任务」跳此（Q70）。

## 8. 实现要点

| 能力 | 路径 |
|------|------|
| 列表/详情/进度 | `apps/releases/views.py` TaskCenter* |
| 摘要协议 | `task_result.py` |
| 审计挂钩 | `apps/audit/utils.py` |

## 9. 前后端约定

- 详情字体统一 `.task-detail-body`（Q30）。  
- 非发布类型不显示多余「发布历史」按钮。  
- 升级等详情入口区分（Q71）。

## 10. 异常与边界

- 进程重启后内存实时步骤丢失，以 DB 字段为准。  
- 窄权限账号列表与详情可见类型已对齐（Q88）。

## 11. 关联模块

几乎全部业务写任务模块；audit、settings（轮询/保留）。

## 12. 已落地优化索引

Q28–Q36、Q39、Q45、Q70、Q71、Q80。

## 13. 相关优化结论

Q87/Q88 已落地，见 [90-gap-and-optimization.md](90-gap-and-optimization.md)。
