# 03 · 仪表盘（dashboard）

## 1. 模块目标与范围

运维首页：节点/配置等统计卡片、最近发布任务、同步失败告警；支持定时刷新。

**不做**：自定义看板布局、跨系统指标聚合。

## 2. 角色与权限

登录即可访问首页；卡片内跳转目标仍受各模块权限控制。侧栏「首页」对已登录用户可见。

## 3. 领域模型

无独立模型；聚合查询：

- `Node` 数量与状态
- `ConfigNodeBinding` 按 `sync_status` 统计（含 conflict/syncing 计数，即使当前恒为 0）
- `ReleaseTask` / `TaskCenterTask` 最近记录

## 4. 页面与路由

| 路径 | 说明 |
|------|------|
| `/` | `dashboard:index` → `dashboard/index.html` |
| `/api/stats/` | **JSON** 统计，供前端轮询 |

实现：[`apps/dashboard/views.py`](../apps/dashboard/views.py)。

## 5. 用例

1. 进入首页展示统计卡、最近任务表、失败绑定列表。
2. 按 `system.dashboard_refresh_interval` 自动请求 `/api/stats/` 更新数字。
3. 点击统计卡/链接进入对应模块列表（过滤条件以实现为准）。
4. 列表条数受设置约束：
   - `dashboard.recent_tasks_count`
   - `dashboard.recent_failed_bindings_count`

## 6. 实现要点

- 设置接线见 Q75；刷新间隔经 context processor / 页面脚本使用。
- 统计卡圆角等样式对齐 Q42。

## 7. 前后端约定

- 轮询失败静默或 Toast（以实现为准），不打断操作。
- 失败绑定可链到配置列表或任务详情（与 Q8 标黄跳转一致处需对齐产品）。

## 8. 异常与边界

- 无数据时 empty-state。
- conflict/syncing 统计位存在但业务少写入（Q84/Q85）。

## 9. 关联模块

nodes、configs、releases、settings。

## 10. 已落地优化索引

| Q | 摘要 |
|---|------|
| Q42 | 统计卡样式 |
| Q75 | 条数与刷新间隔接线 |

## 11. 待确认缺口

与 Q84/Q85 相关的「冲突/同步中」卡片是否保留。
