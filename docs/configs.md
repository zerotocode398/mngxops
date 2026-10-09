# 配置标签、绑定与版本历史

## 范围

NX-030 管理配置标签、标签与节点之间的独立绑定，以及每条绑定的配置正文和版本历史。配置标签只保存名称、默认路径、内容模板、来源和描述；正文、同步状态及历史版本归属于 `ConfigBinding`。

远程发现和同步向导由 NX-031 实现。NX-032 的配置列表按节点分页，支持展开查看绑定、组合筛选、统一创建配置并可选绑定节点，以及未绑定标签维护。

同步向导只按主机名/IP 搜索，多个关键词取交集；列表默认仅显示已识别 Nginx 的节点，不提供节点组或 Nginx 状态筛选。

## 页面与权限

| 方法与路径 | 权限 | 用途 |
|---|---|---|
| `GET /configs/` | `configs.read` | 节点分页列表、绑定状态筛选与未绑定标签区 |
| `GET /configs/sync/` | `configs.read` | 节点筛选、配置发现与同步向导 |
| `GET /configs/create/`、`POST /configs/create/` | `configs.create` | 新建标签，可选 0 个或多个目标节点并同时创建初始绑定 |
| `GET /configs/{id}/` | `configs.read` | 标签信息与活跃节点绑定 |
| `GET/POST /configs/{id}/edit/` | `configs.update` | 编辑标签元数据 |
| `GET/POST /configs/{id}/delete/` | `configs.delete` | 确认删除标签及关联绑定、版本 |
| `GET/POST /configs/bindings/create/` | `configs.create` | 为一个标签批量创建节点绑定 |
| `GET /configs/bindings/{id}/` | `configs.read` | 绑定状态和当前正文 |
| `GET/POST /configs/bindings/{id}/edit/` | `configs.update` | 编辑路径/正文，审阅差异后生成新版本 |
| `GET/POST /configs/bindings/{id}/delete/` | `configs.delete` | 按远程清理状态物理删除或标记删除 |
| `POST /configs/bindings/{id}/restore/` | `configs.update` | 撤销绑定的标记删除状态 |
| `GET /configs/bindings/{id}/versions/` | `configs.read` | 分页版本历史 |
| `GET /configs/bindings/{id}/versions/{version_id}/` | `configs.read` | 查看版本正文 |
| `POST /configs/bindings/{id}/versions/{version_id}/restore/` | `configs.update` | 从历史正文建立一个新的本地修改版本 |
| `GET /configs/bindings/{id}/compare/` | `configs.read` | 只读对比两个版本 |

所有表单使用全局会话与 CSRF 校验。权限与导航由 NX-011 的 RBAC 解析器提供。

## 配置列表交互

- 节点列表默认只显示活动、未锁定且 `nginx_available=True` 的节点；Nginx 开关可切换为全部未锁定活动节点。状态筛选与 Nginx 开关可组合，状态计数统计所有未逻辑删除节点上的绑定；`pending` 同时匹配 `not_synced` 和 `modified`。
- 搜索框使用全局查询标签，回车提交多个关键词，支持中英文逗号拆分；每个关键词可匹配节点主机名/IP、配置标签名或绑定远程路径，多个关键词取交集。节点组查询已移除。节点按主机名分页，每页支持 10/20/50/100 条，翻页和修改页大小保留查询条件。
- 展开节点显示该节点的绑定、版本、同步状态和最近同步时间。当前浏览器标签页以 `sessionStorage` 保存展开节点；搜索或绑定状态筛选后自动展开当前页节点。无搜索或绑定状态筛选时显示未绑定标签区；该区可查看/编辑/添加绑定/逐条删除，不提供批量删除（沿用原项目 Q5 结论）。
- 列表只提供一个新增配置入口。在同一表单中填写标签名称、默认远程路径和配置内容，可不选目标节点，或一次选择多个在线、未锁定且 Nginx 已识别的节点；节点选择器按主机名/IP/节点组关键词 AND 匹配，支持标签查询、分页和跨页保留选择。选中节点时必须填写远程路径，服务端再次校验目标后，在单一事务内创建标签、绑定和各自的 v1。已有标签仍可从标签详情页创建绑定。
- 删除标签沿用全局确认弹窗；成功或失败后的提示使用全局右上角 toast。绑定内容预览通过文本节点显示，正文按纯文本处理。解绑及恢复标记删除也由全局确认弹窗二次确认；表单提交携带 CSRF token，并返回当前列表筛选和分页上下文。

## 配置同步列表交互

- 搜索仅接受主机名/IP 关键词，多词以 AND 组合并可跨主机名和 IP 匹配；移除了节点组关键词、Nginx 状态选择和独立筛选按钮。列表固定默认筛选已识别 Nginx 的节点，分页每页大小通过公共页脚选择并保留搜索条件。
- 批量同步日志面板和可同步节点总数提示已移除。批量按钮放在节点选择状态栏右侧，名称为“全量同步”；单节点发现/同步仍在节点弹窗内显示具体任务结果和日志。

配置发现和同步 API：

| 方法与路径 | 权限 | 用途 |
|---|---|---|
| `POST /api/configs/discover` | `configs.sync` 会话与 CSRF | 异步扫描主配置及 include，结果仅返回远程路径 |
| `POST /api/configs/sync` | `configs.sync` 会话与 CSRF | 创建单节点全量或部分同步任务 |
| `POST /api/configs/sync/batch` | `configs.sync` 会话与 CSRF | 按 `node.batch_max_count` 上限创建节点全量同步任务，默认 3 |

API 返回统一任务 ID，任务进度、日志和结果树通过 `GET /api/tasks/{task_id}` 轮询。节点主配置路径由独立的 `ConfigSyncSetting` 保存，默认路径为 `/etc/nginx/nginx.conf`；它与节点导入/导出使用的 `NodeSyncSetting` 分开维护。发现结果树最多直接列出 300 条路径，更多路径通过发现任务日志补全；同步结果树最多展示每节点 75 条操作明细和 20 条错误摘要，完整计数及逐项任务日志仍保留。

## 状态和操作规则

- 创建绑定要求节点未逻辑删除、未锁定、SSH 在线且 `nginx_available=True`。多节点创建先验证全部目标、标签、路径及重复绑定，再在同一事务内创建绑定和各自的 v1 快照；任何目标无效时不写入部分结果。
- 初始绑定 `current_version=1`、`sync_status=not_synced`，v1 快照记录初始创建来源。标签模板仅用于新建绑定；以后编辑标签模板不会改写已有绑定。
- 编辑绑定只能改远程路径、正文和备注。提交先展示逐行审阅页，确认后追加 `current_version + 1` 快照并将状态改为 `modified`。提交携带预期版本号，若绑定已并发更新则拒绝覆盖并要求重新审阅。
- 恢复历史版本保留所有旧快照，新增版本号，复制目标版本正文并备注“恢复自 vN”；绑定状态设为 `modified`，仍需后续发布才与远端一致。节点门禁要求 SSH 在线、未锁定且已检测到 Nginx。
- 对比页只读，以版本快照正文生成左右行号差异；选择相同版本时提示重新选择，不写数据库。
- 节点 `nginx_available` 不是 `True` 时，解除绑定一律物理删除平台绑定和历史，不连接节点。Nginx 可用时，`not_synced`、`orphaned`、`marked_deleted` 物理删除；其余状态转为 `marked_deleted`，供 NX-031 下次同步清理远程文件。
- 恢复 `marked_deleted` 绑定要求节点满足创建/编辑门禁，成功后重置为 `not_synced`。标记删除的绑定不能编辑或恢复历史版本。
- 节点探测确认 `nginx_available=False` 时，绑定状态同步转为 `orphaned`，但保留原本 `orphaned` 和 `marked_deleted` 的状态；节点 SSH 连接失败且没有得到 Nginx 探测结果时不改变绑定状态（Q150）。
- 配置发现要求节点活动、未锁定、SSH 在线、Nginx 已确认可用且关联凭证已启用。远程扫描通过同一 SSH 会话递归读取 include，支持绝对/相对路径及 `*`、`?`、字符类 glob；引用的内置参数文件和 `/modules/` 文件不会导入。扫描深度最多三层，路径清单最多 500 个文件。
- 全量同步对成功读取的远程文件新建/更新本地标签、绑定及版本；正文变化追加版本并把绑定设为 `synced`。只有发现完整无错误时才将缺失的 `synced` 绑定设为 `orphaned`，并清理 `marked_deleted` 远程文件；扫描不完整时保留缺失判断和远程清理动作，避免把读取错误误判为删除。
- 部分同步先扫描节点，再仅写入本次发现且用户选择的路径；不对其他路径执行 orphan 标记。与参考实现一致，完整扫描成功后部分同步也会处理 `marked_deleted` 绑定。提交了本次扫描中不存在的路径时任务记录该路径错误，不应用其他未选文件。
- 单节点同步在任务内建立一条 SSH 连接，并将其复用于配置发现和 `marked_deleted` 远程文件清理；批量同步按节点各自建立一条连接，不在不同任务间共享连接。任务创建的连接在同步流程结束后关闭。
- 已知绑定的读取失败写入 `failed`、最后同步时间、任务 ID 和通用错误摘要；未绑定文件的读取失败只记录在任务结果树，不创建无正文/无有效版本的绑定。所有远程命令路径经过 shell 引用，删除只有在远程 `rm -f` 成功后才物理删除本地绑定。
- 删除标签会级联物理删除绑定与版本，不尝试连接节点或删除远程文件，与参考项目 `ConfigDeleteView` 一致。

## 持久层

迁移 v8 新建 `ngxops_configs`、`ngxops_config_bindings` 和 `ngxops_binding_versions`；迁移 v9 新建每节点唯一的 `ngxops_config_sync_settings`，同步设置随节点删除，更新用户删除时置空。标签名沿用原模型规则，不设置唯一约束；绑定唯一键为 `(config_id, node_id)`，版本唯一键为 `(binding_id, version)`。配置、绑定、版本及创建用户的删除级联关系按原 Django 模型保留。同步状态 CHECK 仅接纳当前有效状态，不包含已下线的 `conflict` 和 `syncing`。

NX-030 未包含远程 SSH 操作、漂移检测或同步任务；NX-031 使用独立的 `ConfigSyncSetting` 表保存发现/同步路径，不复用 NX-021 的节点导入/导出路径表。

## 与参考项目的实现差异

- 页面以 Jinja2 和公共表单组件实现，不迁移 Django ModelForm、消息中间件或模板过滤器；状态和权限规则保持一致。
- 批量绑定在单一 SQLite 事务中完成并拒绝重复绑定，避免目标列表部分成功。编辑的身份关系（标签、节点）固定，仅允许改路径和正文，符合“每条绑定代表一个标签在一个节点上的文件”这一数据边界。
- 使用版本号条件更新拒绝过期审阅提交；比较视图保持只读，复用原项目用户可达的版本对比行为。
- 节点筛选使用相关绑定 `EXISTS` 查询，避免一台节点因多条匹配绑定重复出现在分页中。
- 配置发现结果中的远程正文只在任务线程内处理，不放入任务参数、日志或任务结果；凭证明文在后台任务内解密，不持久化到任务表。

## 验证与后续

NX-031 验证了隔离数据库迁移 v1-v9、发现结果脱正文、全量更新/版本/远程缺失/标记删除清理、部分同步隔离、批量任务轮询、结果树大小限制、Jinja 模板渲染和 OpenAPI 请求/响应契约。NX-032 后续修正验证了配置列表查询、统一创建 0/多节点绑定、删除 toast、同步页筛选/文案及任务轮询；配置同步专项测试 10 passed，JavaScript 语法检查和 `git diff --check` 通过。未连接真实 SSH 或进行浏览器视觉验证；本次无数据库迁移。部署时须先备份并显式执行 `python -m ngxops.database upgrade`；Web 启动不会自动创建或迁移表。
