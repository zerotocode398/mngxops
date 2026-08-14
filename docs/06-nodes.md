# 06 · 节点与节点组（nodes）

## 1. 模块目标与范围

管理 Nginx 主机库存：CRUD、节点组、锁定、SSH 连通性测试、系统信息/Nginx 版本异步采集、Excel 批量导入/导出、逻辑删除与同 IP 恢复。

**不做**：Agent 常驻采集、自动发现整网主机。

## 2. 角色与权限

`nodes.read|create|update|delete`。  
任务中心部分入口对仅有 `nodes.update` 的用户开放受限视图（见 [09-task-center.md](09-task-center.md)、Q88）。

## 3. 领域模型

[`apps/nodes/models.py`](../apps/nodes/models.py)

### NodeGroup

`name` 唯一；`description`；`created_by`。

### Node

| 字段 | 说明 |
|------|------|
| `hostname`, `ip`(unique), `port` | SSH 目标；默认端口可来自 `node.ssh_default_port` |
| `groups` M2M | 多节点组 |
| `credential` | 可空 FK |
| `environment` | dev/test/prod |
| `nginx_version`, `nginx_path` | 版本与二进制；默认路径可来自设置 |
| `nginx_available` | null=未探测 / True=已检测到 / False=确认不可用（与 SSH `status` 独立，Q150） |
| `last_nginx_probe_at` | 上次 Nginx `-v` 探测时间 |
| `status` | online/offline/unknown（**仅 SSH 连通**） |
| `last_probe_at` | 上次 SSH/采集探测成功时间；失败不清除（Q125） |
| `is_locked` | 锁定 |
| `is_deleted` 等 | 逻辑删除 |
| Managers | `objects`=活跃；`all_objects`=含已删 |

方法：`soft_delete(user)`、`restore()`、`nginx_status_label`、`allows_nginx_ops()`、`allows_install()`。

**双维度展示**：列表 SSH 徽标与 Nginx 徽标分开展示；不会把「无 Nginx」写成 `offline`。

**门禁（两维 AND，Q150）**：

| SSH | Nginx | 同步/发布/回滚/升级/启停 | 安装 |
|-----|-------|--------------------------|------|
| offline/unknown | 任意 | 禁 | 禁 |
| online | true | 允 | 允（弱提示可能覆盖） |
| online | false | 禁 | 允 |
| online | null | 禁 | 允 |

同步主配置路径存 `ConfigSyncSetting.main_conf_path`（configs 应用）。

## 4. 页面与路由

见 [15-api-catalog.md](15-api-catalog.md) 节点节。主模板：`nodes/list|create|edit|delete.html`、`group_*`。  
列表：批量导入/删除文案（Q77）；**探测时间**列展示 `last_probe_at`（Q125）；导出 `GET /nodes/export/`（Q158）。

## 5. 用例 / 业务流程

### 5.1 创建节点

1. 表单填写主机信息、凭证、环境、组、Nginx 路径等。  
2. `create_or_restore_node`（[`apps/nodes/services.py`](../apps/nodes/services.py)）：若同 IP 存在已删记录则 **restore 原主键** 并更新字段，历史发布仍关联。  
3. 写入/更新 `ConfigSyncSetting`（`save_sync_path`），默认主配置来自 `config.default_nginx_path`。

### 5.2 编辑 / 锁定

- 编辑更新字段。  
- 锁定：`is_locked=True`，状态可置 offline。  
- 解锁：触发异步 SSH 测试 TaskCenter（`node_ssh_test`）。

### 5.3 删除

- 单删/批量：`soft_delete`，非物理删（Q76/Q77）。  
- 发布历史展示「已删除」并禁用回滚勾选。

### 5.4 SSH 测试

- 单节点 `test/`、批量 `batch-test/`（上限 `node.batch_max_count`）。  
- 创建 `TaskCenterTask`（`node_ssh_test` / `node_batch_test`），线程执行，更新 `Node.status`。  
- 探测成功统一经 `mark_node_probe_success`：置 `online` 并写 `last_probe_at`（解锁测、系统信息/Nginx 版本采集、凭证启用测同路径）。  
- **SSH 成功后一并执行 `nginx -v`**，经 `apply_nginx_probe_result` 写入 `nginx_available`/`nginx_version`；失败清空版本并触发绑定 orphan（Q150）。  
- 进度：全局 overlay 轮询（Q39/Q40）。  
- **不做**周期性自动 SSH（Q126）。

### 5.5 系统信息 / Nginx 版本

- 详情弹窗内静默请求（Q43），异步 `node_system_info` / `node_nginx_version`。  
- Nginx 版本任务成功/失败均走 `apply_nginx_probe_result`（失败不改 SSH `status`）。  
- 结果写入 TaskCenter `result`（JSON/文本），列表可轮询刷新 DOM（Q40）。

### 5.6 Excel 导入

1. 下载模板 `import/template/`。  
2. 上传整文件校验（openpyxl），失败则整单拒绝。  
3. `apply_node_import`：同 IP 走恢复；默认环境/路径取系统设置（Q77）。

### 5.7 Excel 导出

1. 列表页「导出」：`GET /nodes/export/`（`nodes.read`）。  
2. 有勾选仅导勾选 ID；未勾选则确认后按当前筛选全量导出。  
3. 表头与导入模板一致，便于回流；凭证列仅写凭证名称；审计记条数与勾选/全量。

### 5.8 节点组

CRUD；`manage-nodes` 维护组成员。列表按钮与 `nodes.*` 对齐：新增=`create`，管理节点/编辑=`update`，删除=`delete`；仅 `read`/`create` 时操作列为空。

## 6. 实现要点

| 能力 | 锚点 |
|------|------|
| 导入/恢复/导出 | `apps/nodes/services.py` |
| SSH/采集 View | `apps/nodes/views.py` |
| SSH 底层 | `utils/ssh.py` |
| 设置 | `node.*`、`config.default_nginx_*` |

## 7. 前后端约定

- 节点行展示 `.node-info-cell`（Q32/Q69）。  
- 选择器 API：`api/search-nodes/`（含 `groups__name`）、`api/list/`、`api/groups/`。  
- 弹窗多选行点击勾选（Q66）。

## 8. 异常与边界

- IP 唯一：活跃与已删共用唯一约束，故同 IP 只能恢复不能新建第二行。  
- 无凭证时测试失败。  
- 已锁定节点限制发布等操作（以实现校验为准）。

## 9. 关联模块

credentials、configs（同步路径）、releases、upgrade、task center、settings、audit。

## 10. 已落地优化索引

| Q | 摘要 |
|---|------|
| Q39/Q40 | 测试/采集异步与配置化 |
| Q43 | 详情静默采集 |
| Q66 | 弹窗表勾选 |
| Q68 | 表单分区 |
| Q76 | 逻辑删除与同 IP 恢复 |
| Q77 | 批量导入/删除与文案 |
| Q125 | 列表探测时间（上次探测成功） |
| Q126 | 周期性自动 SSH 不做 |
| Q150 | SSH/Nginx 双维度状态与门禁 |
| Q158 | 列表 xlsx 导出（对齐导入表头） |
| Q159 | 导出勾选/全量确认 |

## 11. 待确认缺口

任务中心对 `nodes.update` 可见性不对称（Q88）。
