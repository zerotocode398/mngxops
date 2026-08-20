# 解决计划：多标签查询 / 凭证 Nginx / 列表局部刷新 / 配置列表性能

- 日期：2026-08-19
- 状态：待实施
- 关联台账：实施完成后写入 AGENTS.md（建议 Q162）

---

## 一、调整后的问题点（相对原始反馈）

### P1 · 凭证启用后节点列表 Nginx 未更新

| 项 | 说明 |
|----|------|
| **原表述** | 凭证启用更新后，节点列表「Nginx」版本未更新 |
| **根因** | 启用任务 `_run_credential_enable_task` 仅做 SSH `test_ssh_connection` + `mark_node_probe_success`，**未**调用 `get_nginx_version` + `apply_nginx_probe_result`；与节点单测/批测/解锁路径不一致（Q150 双维度） |
| **非根因** | 节点列表缺轮询：列表本就服务端渲染；DB 未写 Nginx 字段时刷新也无效 |
| **预期** | SSH 成功后同步探测 Nginx；`nginx_available` / `nginx_version` / `last_nginx_probe_at` 落库；结果树可含 Nginx 项（探测失败不否定 SSH 成功） |

### P2 · 节点管理查询与「节点组」双入口

| 项 | 说明 |
|----|------|
| **原表述** | 查询像单标签；主机名+IP 多标签无果；标签文案含节点组，又有节点组下拉，是否可删一个 |
| **根因** | `filter_node_list_queryset` 的 `search`：**整串** `hostname\|ip` icontains，**不拆逗号**、**不匹配组名**；多标签 AND 与组名匹配写在 `group_search` 上；模板另有 `select name=group_search` |
| **产品结论** | **删除节点组下拉**；标签搜索支持多标签 AND，字段含主机名 / IP / **节点组名**；节点组列表「查看该组」改为 `?search=组名` |
| **兼容** | 导出/API 仍可读历史 `group_search`，合并进与 `search` 相同的 AND 逻辑，避免旧书签失效 |

### P3 · 配置管理多标签无果

| 项 | 说明 |
|----|------|
| **原表述** | 配置管理查询像单查询，多标签无果 |
| **根因** | `ConfigListView` 将整个 `search` 字符串一次 `icontains`（逗号成子串的一部分） |
| **预期** | 多标签 AND；单标签内 OR：主机名 / IP / 配置名 / 远程路径；**保留** `group_id` 精确下拉（与节点「删组下拉」策略不同） |
| **另见** | 配置列表「慢、一条条出来」见 P6，与「能否搜到」是两条线 |

### P4 · 全站 tag 查询口径不统一

| 项 | 说明 |
|----|------|
| **原表述** | 所有查询检查一遍，是否还有单标签 |
| **结论** | UI 多用 `initTagSearch`（逗号拼 hidden），**后端未统一拆标签** |
| **已支持多标签 AND** | 凭证列表、节点组、用户组、审计/登录、任务中心；发布历史（缺中文逗号 `，`）；部分节点 API 的 `group_search` |
| **仍为整串/弱多标签** | 节点 `search`、配置 `search`、用户、角色、升级/安装/启停/卸载历史 |
| **统一规则** | `split_search_tags`（`,` / `，`）→ **标签间 AND**，**标签内字段 OR** + 必要时 `distinct()`；凡 tag-input 必接此后端 |

### P5 · 查询时整页刷新发顿

| 项 | 说明 |
|----|------|
| **原表述** | 单标签查询也感觉整页（含侧栏）刷新很顿 |
| **根因** | `initTagSearch` → `form.submit()` **全文档 GET**，侧栏/壳层一并重载；非「只刷新表格」 |
| **范围（已确认）** | **主要列表局部刷新**：节点、配置、凭证、任务中心、发布历史（运维历史可同组件后续接） |
| **做法** | `fetch` + `X-Partial: 1` → 仅替换主内容区；`pushState`；失败整页回退；**不**上整站 SPA |
| **非目标** | 本期不引入 HTMX/Turbo 依赖（可用原生 fetch 等价实现） |

### P6 · 配置管理打开后数据「逐条慢慢展示」

| 项 | 说明 |
|----|------|
| **原表述** | 18 节点 / 170 配置时，点配置管理像逐条慢慢展示 |
| **根因（主）** | 服务端：`get_queryset` 全量节点 + context **按节点再查 bindings**（prefetch 浪费）、**先算全量再分页**、状态 chip **多次 COUNT**、大 HTML 一次吐出 → TTFB/首屏慢，观感像「慢慢出来」 |
| **根因（次）** | 返回列表时 `sessionStorage` 恢复多节点 Bootstrap Collapse，展开动画加重「陆续出现」感 |
| **非根因** | **没有**前端按行 API 流式加载；侧栏也不是逐条拉配置数据 |
| **预期** | 先分页节点 → 仅当前页一次拉 bindings；状态汇总一次聚合；避免双重全量 `get_queryset`；展开恢复可减弱动画（可选） |

---

## 二、原始 6 点 ↔ 调整后编号

| 原 # | 调整后 | 一句话 |
|------|--------|--------|
| 1 | P1 | 凭证启用缺 Nginx 探测写入 |
| 2 | P2 | 节点 search 未多标签/未含组 + 删组下拉 |
| 3 | P3 | 配置 search 未多标签 AND |
| 4 | P4 | 全站 tag 后端对齐 AND |
| 5 | P5 | 核心列表 partial，侧栏不重载 |
| 6 | P6 | 配置列表查询/分页性能，非前端逐条渲染 |

---

## 三、实施要点（摘要）

1. **公共** `utils/search.py`：`split_search_tags`；列表筛选尽量复用。
2. **P1** `apps/credentials/services.py`：SSH 成功后对齐节点测的 Nginx 路径。
3. **P2–P4** 节点/配置/用户/角色/各运维历史 + 发布历史中文逗号；删节点 `group_search` 下拉；测例迁移。
4. **P6** `ConfigListView` 页内 bindings + 聚合 count。
5. **P5** `base.html` 主区 + 五列表 partial 模板/响应头与 JS 重绑。
6. **文档** `docs/13-ui-conventions.md` 多标签约定；完成后 **AGENTS.md Q162**。

### 建议顺序

1. `split_search_tags` + P2/P3/P4 后端 + 删下拉  
2. P1 凭证 Nginx  
3. P6 配置列表性能  
4. P5 局部刷新  
5. 台账与手测  

### 风险

- Partial 后列表 JS 需可重入初始化。  
- 节点导出/旧 `group_search` 书签需兼容。  
- 凭证启用多一次 `nginx -V`，耗时与节点 SSH 测同级。  

### 产品默认（已确认）

- 节点：**删**节点组下拉，tag 含组名。  
- 配置：**保留** `group_id` 下拉。  
- 刷新：核心列表局部，非整站。  

---

## 四、关键文件（实施索引）

| 主题 | 路径 |
|------|------|
| 凭证启用 | `apps/credentials/services.py` |
| 节点筛选 | `apps/nodes/views.py` `filter_node_list_queryset`；`nodes/list.html` |
| 配置列表 | `apps/configs/views.py` `ConfigListView`；`configs/list.html` |
| Tag 组件 | `templates/base.html` `initTagSearch` |
| 已有多标签样板 | `apps/releases` 任务中心；`apps/audit/views.py` |
| Nginx 写入样板 | `apps/nodes/services.py` 单/批测、`apply_nginx_probe_result` |
