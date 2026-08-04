# 16 · 非功能需求（基于现状）

以下条目描述**当前实现所体现的约束与能力**，而非未立项的目标架构。

## 1. 性能与并发

| 项 | 现状 |
|----|------|
| 发布并行 | `release.max_parallel_tasks` 控制跨节点线程池；同节点内串行多配置后一次 reload（Q80） |
| 配置同步并发 | `config.sync_max_concurrency` |
| 凭证测试并发 | `credential.test_max_concurrency` |
| 节点批量探测 | `node.batch_max_count` |
| SSH 超时 | `node.ssh_connect_timeout`；失败可 `node.detect_retries` |
| 发布门禁 | **已知约束**：存在任意 `ReleaseTask.status=running` 时阻断新批次自动执行（Q93 结论：维持） |

进程内线程模型：单机多 Django Worker 时，线程与内存态进度不跨进程共享。

## 2. 可用性

- 长操作提供全屏进度遮罩 + TaskCenter 轮询；失败可跳任务详情。
- 节点软删除保障发布/升级历史不因删节点而消失（Q76）。
- 远程首发无文件时跳过备份，避免误失败（Q48）。
- 设置变更说明含生效时机文案（Q75/Q81）。

## 3. 安全

| 项 | 现状 |
|----|------|
| 认证 | Session；锁定用户 `is_active=False` 不可登录 |
| 授权 | 自定义 RBAC；超管绕过 |
| 凭证 | Fernet 加密存储；解密接口需登录权限 |
| CSRF | Django 默认中间件 |
| 用户名 | 限制 `[-a-zA-Z0-9_]+`，避免中文导致路由问题（Q82） |
| 审计 | CRUD 信号 + 登录日志 + 异步任务关联 |

未实现：细粒度「按节点数据范围」ACL、操作二次审批流。

## 4. 可观测性

- TaskCenter：`log_output` 增量、结果树、进度百分比。
- AuditLog：模块/动作/结果/任务软链。
- 仪表盘：执行中/近 7 天失败统计与最近任务中心记录。

## 5. 数据与运维

- 默认 SQLite，适合中小规模；生产若换库需自行迁移评估。
- 保留天数可配；管理命令 `purge_expired_data`。
- 备份目录：`{release.backup_dir}/{hostname}/`（Q61）。
- 源码包/第三方模块包大小上限：`upgrade.package_max_size_mb`（默认 20MB）。

## 6. 兼容性

- 浏览器：现代 Chromium / Firefox / Edge（依赖 Bootstrap 5）。
- 服务端 Python 3.9.6 + Django 4.2。
- 远程节点：Linux + SSH；Nginx 可为包安装或自定义路径。

## 7. 已知限制（产品层）

- 无分布式任务队列；进程重启可能丢失未落库的内存进度片段（DB 状态以 worker 最后写入为准）。
- `conflict`/`syncing`/漂移检测等能力不完整（见 [`AGENTS.md`](../AGENTS.md) Q84–Q86）。
