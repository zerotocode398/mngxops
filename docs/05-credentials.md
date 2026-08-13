# 05 · SSH 凭证（credentials）

## 1. 模块目标与范围

集中管理 SSH 密码/私钥凭证，加密存储；启用时对关联节点并发连通性测试；禁用时可将关联节点置离线；支持列表明文导出（Q158）。

**不做**：凭证自动轮换、Vault 对接。

## 2. 角色与权限

`credentials.read|create|update|delete`。

## 3. 领域模型

[`apps/credentials/models.py`](../apps/credentials/models.py)

### Credential

| 字段 | 说明 |
|------|------|
| `name` | 与 `created_by` 唯一 |
| `username` | SSH 用户 |
| `auth_type` | password / key |
| `password` / `private_key` | Fernet 密文（`gAAAAA` 前缀识别） |
| `is_enabled` | 启用开关 |
| `last_test_time` / `last_test_result` | success/partial/failed/unknown |

保存时明文自动加密；读取用 `get_password()` / `get_private_key()`。

### CredentialEnableTask

启用批次进度：total/completed/success/failed/skipped + `task_center_id`。

## 4. 页面与路由

| 路径 | 说明 |
|------|------|
| `/credentials/` CRUD | 列表/表单 |
| `/credentials/export/` | 勾选或筛选全量 xlsx 导出（含明文） |
| `import/template/`、`import/` | Excel 模板与导入 **JSON** |
| `toggle-enable/` | 启用/禁用 |
| `decrypt/` | 眼睛图标解密 **JSON** |
| `related-nodes/` | 关联节点 |
| `enable-progress/` | 进度 **JSON** |
| `api/list/` | 选择器用列表 |

模板：`credentials/list|create|edit|delete.html`；眼睛相对输入框垂直居中（Q68）。

## 5. 用例

### 5.1 CRUD

创建/编辑分区表单；删除前确认；列表展示启用态与最近测试结果。

密钥认证：支持粘贴私钥，或前端选择本地文件读入 textarea（不上传独立接口）；格式由服务端 paramiko 校验（RSA/DSA/ECDSA/Ed25519）。**不支持**带口令的加密私钥，需先在本地解密后再导入。

### 5.2 启用

1. 打开启用 → 创建 `CredentialEnableTask` + `TaskCenterTask(operation_type=credential_enable_test)`。  
2. 线程池按 `node.batch_max_count` 并发测**全部**关联节点（不截断台数）。  
3. 更新凭证 `last_test_*` 与任务结果树。  
4. 前端 `#asyncProgressOverlay` 轮询（Q39）。

### 5.3 禁用

设置 `is_enabled=False`；关联节点状态置 `offline`（以实现为准）。

### 5.4 解密查看

有权限用户请求 decrypt 接口获取明文用于表单回显（不落审计明文细节为佳）。

### 5.5 Excel 导出

1. 列表页「导出」（`credentials.read`）：有勾选仅导勾选；未勾选则确认后按筛选全量导出。  
2. 勾选/全量导出均含明文密码或私钥；确认弹窗提示妥善保管。  
3. 审计记「导出凭证」与条数/名称/勾选或全量，**不写**密码或私钥。

### 5.6 Excel 导入

1. 下载模板 `import/template/`；上传 `import/`（`credentials.create`）。  
2. 表头与导出一致；整文件校验，失败整批不写。  
3. 当前用户下同名则更新，否则新建；私钥经 paramiko 校验。  
4. 审计记「导入凭证」摘要（新建/更新条数）。

## 6. 实现要点

- 加密：[`utils/crypto.py`](../utils/crypto.py)。
- 异步与进度：[`apps/credentials/views.py`](../apps/credentials/views.py)。
- 导出/导入：[`apps/credentials/services.py`](../apps/credentials/services.py)。
- 设置键：跨节点并发复用 `node.batch_max_count`（Q156）。

## 7. 前后端约定

- 启用走全局进度遮罩；完整日志进任务中心。
- 表单 `.form-section` 分区（Q68）。

## 8. 异常与边界

- 无关联节点时启用测试 success/skipped 口径以实现为准。
- 密钥文件丢失导致历史密文不可解密——运维需备份 `.fernet_key`。

## 9. 关联模块

nodes（FK credential）、task center、audit、settings。

## 10. 已落地优化索引

| Q | 摘要 |
|---|------|
| Q39 | 启用测试真实轮询 |
| Q40 | 并发配置化 |
| Q68 | 表单与眼睛居中 |
| Q97 | 无关联节点启用跳过测试任务 |
| Q98 | 密钥认证切换修复 + 私钥文件导入 |
| Q158 | 列表 xlsx 导出（明文密码/私钥 + 确认 + 审计不落密） |
| Q159 | 导出勾选/全量确认；凭证批量导入 |

## 11. 待确认缺口

无单独 Q（导出/导入见 Q158/Q159）。
