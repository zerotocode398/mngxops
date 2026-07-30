# 03 · 仪表盘（dashboard）

## 1. 模块目标与范围

运维首页：节点/任务等统计卡片、最近任务中心记录；支持统计卡定时刷新。

**不做**：自定义看板布局、跨系统指标聚合、失败绑定独立告警表。

## 2. 角色与权限

登录即可访问首页；卡片内跳转目标仍受各模块权限控制。侧栏「首页」对已登录用户可见。

最近任务列表可见范围与任务中心对齐：有 `releases.read` 看全部；仅有 `nodes.update` 时仅本人 `node_batch_test` / `config_batch_sync`。

## 3. 领域模型

无独立模型；聚合查询：

- `Node` 数量与在线/离线状态
- `ConfigNodeBinding` 待推送（`sync_status=modified`）
- `TaskCenterTask` 执行中、近 7 天失败、最近记录（含类型与摘要）

## 4. 页面与路由

| 路径 | 说明 |
|------|------|
| `/` | `dashboard:index` → `dashboard/index.html` |
| `/api/stats/` | **JSON** 统计，供前端轮询 |

实现：[`apps/dashboard/views.py`](../apps/dashboard/views.py)。

原型：[`docs/prototypes/dashboard-home-prototype.html`](prototypes/dashboard-home-prototype.html)。

## 5. 用例

1. 进入首页展示统计卡、页头紧凑快捷入口、最近任务表。
2. 按 `system.dashboard_refresh_interval` 自动请求 `/api/stats/` 更新数字。
3. 点击统计卡/链接进入对应模块列表（过滤条件以实现为准）。
4. 最近任务条数受 `dashboard.recent_tasks_count` 约束（默认 20）。

## 6. 实现要点

- 设置接线见 Q75 / Q108；刷新间隔经 context processor / 页面脚本使用。
- 统计卡圆角等样式对齐 Q42。
- 快捷入口对齐升级首页顶栏 `btn-sm`（Q108），不再使用大卡片「快捷操作」区。

## 7. 前后端约定

- 轮询失败静默或 Toast（以实现为准），不打断操作。
- `/api/stats/` 字段：`node_count`、`online_count`、`offline_count`、`pending_push_count`、`running_count`、`failed_7d_count`。

## 8. 异常与边界

- 无数据时 empty-state。
- 无任务中心相关权限时最近任务为空、执行中/失败计数为 0。

## 9. 关联模块

nodes、configs、releases（任务中心）、settings。

## 10. 已落地优化索引

| Q | 摘要 |
|---|------|
| Q42 | 统计卡样式 |
| Q75 | 条数与刷新间隔接线 |
| Q108 | 最近任务改 TaskCenter；删失败绑定表；紧凑快捷入口；执行中/近7天失败卡 |

## 11. 待确认缺口

无。
