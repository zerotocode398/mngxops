# 节点资产

## 页面

- `/nodes/` 提供活跃节点筛选、分页、当前筛选范围导出、批量导入、锁定、解锁和逻辑删除。批量锁定时，若所选节点均已锁定则显示警告提示；混合选择时仅锁定未锁定节点并提示跳过数量。搜索采用逗号分隔的查询标签；节点组、环境和 SSH 状态选择器变更时自动提交。
- `/nodes/create/` 与 `/nodes/{id}/edit/` 以分区卡片维护主机名、IP、SSH 端口、凭证、最多 3 个节点组、环境、Nginx 路径和备注。新增节点默认 IP 为 `127.0.0.1`，端口范围为 1～65535。
- `/nodes/groups/`、`/nodes/groups/create/`、`/nodes/groups/{id}/edit/` 和 `/nodes/groups/{id}/delete/` 管理节点组及成员。
- 节点列表和节点组列表搜索支持回车提交、查询标签移除和搜索焦点恢复；分页与每页条数变更保留当前查询条件。
- 新增或编辑节点时，节点组选择弹窗按名称/描述查询标签并分页；新增或编辑节点组时，成员弹窗按主机名/IP 查询标签并分页。选择弹窗在已加载的记录中分页，翻页和筛选期间保留已选项；成员选择仍受节点最多关联 3 个组的规则约束。
- 新节点的 Nginx 可执行路径默认读取 `config.default_nginx_bin`（`/usr/sbin/nginx`），主配置路径默认读取 `config.default_nginx_path`（`/etc/nginx/nginx.conf`）；二者按 `docs/settings.md` 的设置语义对应。

## 数据规则

- 节点和节点组使用数据库自增主键。`ip` 在活跃及已删除节点之间保持唯一；删除是软删除，保留凭证、节点组和历史外键。用相同 IP 再创建时恢复原行并更新提交字段，历史关联仍使用原主键。
- 每个节点最多关联 3 个节点组。节点组名唯一；物理删除节点组只解除成员关联。
- 节点凭证可以为空。表单只允许选择已启用凭证，导入凭证名称时优先选当前用户同名记录；否则只在全局匹配唯一时接受。凭证本身始终只保存 Fernet 密文。
- 锁定会设置 `is_locked=true` 且将 SSH 状态改为 `offline`；重复锁定会跳过，不重复写审计。解锁清除锁、将状态暂置为 `unknown`，并自动创建 SSH/Nginx 探测任务，任务完成后更新 SSH 状态、最近探测时间和 Nginx 版本。启用凭证测试只检查未锁定的活动节点。
- SSH `status`、Nginx `nginx_available` 和 Nginx 版本分别保存。SSH 成功记录 `last_probe_at`；Nginx 检测单独记录 `last_nginx_probe_at`。Nginx 命令失败只将 Nginx 标记为不可用，不覆盖 SSH 在线状态；SSH 连接失败会标记离线，且不清除上次成功探测时间。
- `ngxops_node_sync_settings` 保存节点 xlsx 导入/导出使用的 `Nginx主配置路径`。配置发现/同步使用 NX-031 的独立 `ngxops_config_sync_settings`，两个路径设置互不覆盖。

## 导入与导出

- `GET /api/nodes/import-template` 下载带说明页和环境下拉值的 `.xlsx` 模板。
- `POST /api/nodes/import` 限制文件为 `.xlsx` 且最多 8 MiB。表头必须与模板一致；端口、IP、环境、组名和启用凭证引用会整份校验，任何行有误时不会写入任何节点。相同校验原因按错误文本合并；页面只显示去重后的异常信息，不显示行号。API 保留 `row`/`row_range` 元数据供调用方定位原始 Excel 行。
- `GET /api/nodes/export` 带 `ids` 时按勾选顺序导出；没有 `ids` 时按当前搜索、组、环境和状态筛选导出。导出字段与模板相同，凭证列只有凭证名称。
- 成功的批量导入写一条数量摘要；导出写入范围和行数。节点及节点组 ORM 变更由 NX-060 操作审计记录。批量探测、锁定、解锁和逻辑删除使用 `node.batch_max_count` 上限，默认 3，页面选择和后端门禁一致。

## API 与权限

| 接口 | 权限 | 行为 |
|---|---|---|
| `GET /api/nodes` | `nodes.read` | 查询非敏感节点字段，最多返回 500 条，并按筛选条件报告总数。 |
| `GET /api/nodes/{node_id}` | `nodes.read` | 查询节点详情、凭证标识、SSH/Nginx 状态及探测时间，不返回凭证明文。 |
| `GET /api/nodes/groups` | `nodes.read` | 查询分组名、描述和活跃成员数。 |
| `POST /api/nodes/lock` | `nodes.lock` 或 `nodes.unlock` | 按 `node.batch_max_count` 上限对活动节点锁定或解锁；已锁定节点跳过，解锁响应带 `task_id` 并自动探测 SSH/Nginx。 |
| `POST /api/nodes/batch-delete` | `nodes.delete` | 按 `node.batch_max_count` 上限逻辑删除活跃节点。 |
| `POST /api/nodes/{node_id}/probe` | `nodes.ssh_test` | 对未锁定且关联启用凭证的节点异步探测 SSH 和 Nginx。 |
| `POST /api/nodes/probe` | `nodes.ssh_test` | 按 `node.batch_max_count` 上限对活动节点异步探测；锁定、无凭证或禁用凭证会作为失败项记录，不尝试连接。 |
| `POST /api/nodes/{node_id}/system-info` | `nodes.ssh_test` | 异步采集 OS、内核、CPU、内存、磁盘和运行时间。 |
| `POST /api/nodes/{node_id}/nginx-probe` | `nodes.ssh_test` | 单独异步检测 Nginx 版本和可用状态。 |
| `POST /api/nodes/import` | `nodes.create` | 上传 `.xlsx` 并执行全文件校验。 |

JSON API 使用 `/api/` 前缀、服务端会话和全局 CSRF；OpenAPI 已登记请求/响应与错误模型。节点列表导出需要 `nodes.read`，模板/导入需要 `nodes.create`。

上述异步接口返回 `task_id`，可用 `GET /api/tasks/{task_id}` 轮询真实进度、日志和结果树。节点列表的 SSH 操作在任务创建后立即提供详情链接，完成时再提示结果；解锁任务由 `nodes.unlock` 用户本人可查看。离线或未知节点允许发起探测以恢复状态；节点锁定阻止单节点 SSH 探测，批量探测将锁定节点列为失败且不改变其状态。SSH 成功后更新为在线并记录最近成功时间；SSH 失败标记为离线。系统信息采集只更新 SSH 维度；独立 Nginx 探测在 SSH 连接成功后才更新 Nginx 维度。节点列表和详情中的探测时间按北京时间展示，列名为“探测时间”，数据库仍以 UTC naive 时间保存。

## 与参考实现的差异

- 节点启用测试复用 NX-005 的持久化任务与结果树；`node.batch_max_count` 控制并发 worker 数，默认 3。所有阻塞 SSH 都在线程池执行。
- 节点表单和分组管理复用公共 Jinja2 布局；节点筛选、导入、导出和批量操作保留参考项目的范围语义，并由 NX-060 记录重要写入和批次摘要。
- NX-022 将原 Django POST 详情查询改为 `GET /api/nodes/{node_id}`，任务复用统一执行器和结果树；详情弹窗打开后会采集系统信息并检测 Nginx，所有 SSH I/O 在线程池执行。批量探测上限由 `node.batch_max_count` 控制。
