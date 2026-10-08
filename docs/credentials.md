# SSH 凭证

## 页面

- `/credentials/` 按名称/SSH 用户搜索，支持回车提交的查询标签、逗号分隔 AND 关键词、认证方式、启用状态和分页；筛选值在翻页和修改每页条数时保留。
- `/credentials/create/`、`/{credential_id}/edit/` 提供凭证新增和修改页面；单条删除及 `/bulk-delete/` 批量删除均使用全局确认弹窗和 CSRF 表单。
- 新增/编辑表单分为基本信息和 SSH 认证两张分区卡片，提交字段及认证方式切换行为保持不变。
- 凭证列表在操作标题栏下直接显示查询控件和数据表格；更新时间将 UTC 存储值转换为北京时间（UTC+8）。
- 关联节点数量沿用节点组成员节点的表格字号和链接样式；点击后打开分页弹窗，显示主机名、IP、SSH 状态、Nginx 版本和北京时间 SSH 探测时间，数据由 `GET /api/credentials/{credential_id}/nodes` 提供。
- 最近测试列只显示测试结果，不展示测试时间。
- 认证方式为密码或私钥。私钥文件由浏览器读取到文本框，文件本身不单独上传；支持未加密的 RSA、ECDSA、Ed25519，不支持带口令私钥。mngxops 文档还列 DSA，但当前锁定的 Paramiko 5.0.0 已移除 `DSSKey`，无法校验或使用 DSA。
- 编辑页不把现有密钥填入 HTML。留空表示保留原密文；眼睛操作通过受 RBAC 保护的解密 API 读取明文。

## 权限与数据

- 页面列表、凭证选择 API、密钥解密 API 使用 `credentials.read`；创建、编辑和删除分别使用 `create/update/delete`；开关使用 `credentials.enable`。
- `(name, created_by)` 唯一。密码/私钥列为空或 Fernet 密文；密钥文件为数据目录 `.fernet_key`，不可与数据库分离备份。
- 创建和修改校验名称、用户名、字段长度、认证材料及未加密私钥格式。保存凭证明文只在请求内存和授权解密响应中短暂出现。
- 选项 API `GET /api/credentials` 仅返回启用凭证的非敏感摘要；解密 API `GET /api/credentials/{id}/secret?field=password|private_key` 需要 `credentials.read` 并禁止缓存。
- 超级管理员可从列表按勾选或当前筛选范围下载明文 xlsx；持有 `credentials.create` 权限的用户可批量导入。工作簿内重名或与当前用户已有凭证重名都会拒绝，并提示重复名称及来源；其他校验提示具体字段和修正要求，不回显密码或私钥。任一行无效时整批不写入，限制 `.xlsx` 和 8 MiB。导入弹窗只显示去重后的具体错误，不显示行号；API 响应保留行号元数据。关闭导入弹窗会清空错误并重置文件。
- 凭证明文导入/导出只在请求内存中解密或加密；审计只写数量、范围和最多 20 个名称，不记录密码或私钥。导出响应设置 `Cache-Control: no-store`。
- 批量删除要求所有选中凭证仍存在后才在单个事务中删除，并只写一条汇总审计；关联节点沿数据库外键规则解除凭证关联。
- 导入保留密码中的有效首尾空格（仅移除单元格外围换行），避免改变原始认证材料。

## 启停与节点联调

- `POST /api/credentials/{id}/toggle-enable` 需要 `credentials.enable` 与 CSRF。
- 与参考项目相同，禁用时将活动关联节点置离线，重新启用时只测试未锁定节点；无活动关联节点时只启用，不创建空任务。
- 启用任务经 NX-005 执行器运行，`node.batch_max_count` 控制最大并发 worker 数，默认 3；进度可由 `GET /api/credentials/{id}/enable-progress` 轮询。SSH 连接成功后单独执行 Nginx `-v`，SSH 状态和 Nginx 可用状态分开写回；锁定节点不计入连接测试结果。
- 有关联节点的启用测试在任务创建后立即显示任务详情链接，完成后提示汇总结果；禁用只更新关联节点离线状态，不创建 SSH 任务。
- 凭证启用测试的操作审计摘要显示任务编号和目标摘要；可查看权限范围内的 `#任务ID` 在新标签页打开任务详情。
- 任务结果仅保存节点/IP、SSH 成功状态、Nginx 可用性/版本及汇总，不保存凭证明文或 Paramiko 异常文本。无可测试节点但有锁定关联节点时会创建完成任务并把凭证测试状态记为 `unknown`。

## 与参考实现的范围差异

- 凭证导出仍限超级管理员；批量导入按 `credentials.create` 门禁，批量删除按独立的 `credentials.delete` 门禁，不因开放导入授予删除能力。导入权限按用户确认区别于参考项目的超级管理员限制。私钥支持范围保持为当前 Paramiko 版本可校验的 RSA、ECDSA 和 Ed25519。
- 页面通过公共 Jinja 布局、CSRF 字段、RBAC 导航、Toast 和分页组件实现；相关 JSON API 统一位于 `/api/` 并登记 OpenAPI。
