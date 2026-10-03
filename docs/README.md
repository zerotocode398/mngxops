# 文档索引

## 部署与接口

| 文档 | 内容 |
|---|---|
| [deployment.md](deployment.md) | 安装、初始化、启动、配置、备份与运行限制 |
| [packaging.md](packaging.md) | 单文件构建、CLI、进程启停和运行日志 |
| [api.md](api.md) | JSON API、OpenAPI、安全方案、响应模型和错误状态 |
| [database.md](database.md) | SQLite 数据边界、迁移、表关系与备份 |
| [security.md](security.md) | 会话、CSRF、RBAC、密钥和上传边界 |

## 日常操作流程

| 顺序 | 文档 | 内容 |
|---|---|---|
| 1 | [accounts.md](accounts.md)、[rbac.md](rbac.md) | 首个管理员、账户、角色、用户组和权限 |
| 2 | [credentials.md](credentials.md)、[nodes.md](nodes.md) | SSH 凭证、节点资产、探测和导入导出 |
| 3 | [configs.md](configs.md) | 配置发现、同步、绑定、版本和状态 |
| 4 | [releases.md](releases.md) | 批量发布、发布历史和配置版本回滚 |
| 5 | [upgrade.md](upgrade.md)、[nginx-install.md](nginx-install.md)、[nginx-service.md](nginx-service.md)、[nginx-uninstall.md](nginx-uninstall.md) | Nginx 升级、安装、启停和卸载 |

## 平台管理

| 文档 | 内容 |
|---|---|
| [tasks.md](tasks.md) | 异步任务、日志、可见范围、取消和进程边界 |
| [audit.md](audit.md) | 操作审计和登录记录 |
| [settings.md](settings.md) | 已接线系统设置和数据保留 |
| [dashboard.md](dashboard.md) | 仪表盘统计、快捷入口和最近任务 |

## 核对顺序

建议先读部署边界，再按“凭证 → 节点 → 配置同步 → 发布/回滚 → Nginx 生命周期运维”走查业务。配置同步只把远程文件拉入平台；发布才会写入远程配置并执行校验和 reload。Nginx 全新安装、已有实例升级、启停和卸载有不同节点门禁，具体要求分别见对应模块文档。
