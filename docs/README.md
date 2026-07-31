# mngxops 软件需求设计文档

本文档集基于**当前代码实现**倒推整理（实现即基线需求），按功能模块拆分。历史交互优化结论见仓库根目录 [`AGENTS.md`](../AGENTS.md)（Q1–Q118 结论台账）。

## 阅读顺序

| 顺序 | 文档 | 说明 |
|------|------|------|
| 1 | [00-overview.md](00-overview.md) | 产品定位、术语、角色、模块地图 |
| 2 | [01-architecture.md](01-architecture.md) | 技术架构、SSH、TaskCenter、设置与保留 |
| 3 | [14-data-model.md](14-data-model.md) | 跨模块数据模型 |
| 4 | [15-api-catalog.md](15-api-catalog.md) | 页面路由与 JSON API 清单 |
| 5 | 业务模块 02–12 | 按需查阅各功能需求 |
| 6 | [13-ui-conventions.md](13-ui-conventions.md) | 已落地 UI/交互约束 |
| 7 | [16-nfr.md](16-nfr.md) | 非功能需求（基于现状） |
| — | [`AGENTS.md`](../AGENTS.md) | 优化点结论台账（仓库根目录，非 docs 编号） |

## 业务模块索引

| 文档 | 模块 | 代码入口 |
|------|------|----------|
| [02-accounts.md](02-accounts.md) | 登录 / 个人中心 | `apps/accounts` |
| [03-dashboard.md](03-dashboard.md) | 运维仪表盘 | `apps/dashboard` |
| [04-users-rbac.md](04-users-rbac.md) | 用户 / 角色 / 用户组 / RBAC | `apps/users` |
| [05-credentials.md](05-credentials.md) | SSH 凭证 | `apps/credentials` |
| [06-nodes.md](06-nodes.md) | 节点 / 节点组 | `apps/nodes` |
| [07-configs.md](07-configs.md) | 配置标签 / 绑定 / 同步 | `apps/configs` |
| [08-releases.md](08-releases.md) | 发布中心 / 发布历史 / 回滚 | `apps/releases` |
| [09-task-center.md](09-task-center.md) | 任务中心 | `apps/releases`（TaskCenter） |
| [10-upgrade.md](10-upgrade.md) | Nginx 编译升级 | `apps/upgrade` |
| [11-audit.md](11-audit.md) | 操作审计 / 登录日志 | `apps/audit` |
| [12-settings.md](12-settings.md) | 系统设置 | `apps/settings` |

## 文档与 AGENTS 的关系

- **docs/**：完整软件需求设计（功能、流程、模型、接口、实现锚点）。
- **AGENTS.md**：优化点结论台账（Q1–Q118+）的**唯一**来源；已完成项沉淀为 docs 中的「已确认行为」。
- 未实现或已关闭能力：记入 `AGENTS.md`（续编 Q119+），不在模块正文写成已交付。

## 写作约定

- 以代码行为为准；未实现能力不写成已交付，统一记入 [`AGENTS.md`](../AGENTS.md)。
- 需求条目尽量带实现路径（如 `apps/releases/views.py` → `ReleaseExecutorMixin`）。
- 术语见 [00-overview.md](00-overview.md) 术语表，全文统一使用。
