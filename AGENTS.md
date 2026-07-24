Q1
```text
配置列表
	点击节点时，其配置会自动展开
	然后点击操作编辑、版本历史按钮，随后点击“<-返回列表”
	发现其配置未展开，需要额外再次点击主机展开。
    能否调整为点击“返回列表”时，继续保持展开？
    ✅ 已修复
```

Q2
```text
配置列表
	手动添加，手动添加配置标签后页面看不到数据。
    ✅ 已修复：创建配置标签后直接跳转到绑定创建页（config 已预选）
```

Q3
```text
配置管理
    创建配置标签节点绑定的目标节点，能否改成自定义弹窗形式；其自定义弹窗可以参考编辑节点组"添加/修改节点"为基准进行设计。其查询条件需要支持主机名、IP、节点组。自定义弹窗需要支持批量选择，用户可以同时选择多个节点进行绑定。
    自定义弹窗的样式、字体、条件标签等等等都需要和"添加/修改节点"保持一致。
    ✅ 已修复：binding_create.html 替换为弹窗选择，支持标签搜索+多选，BindingCreateView 循环创建多个绑定
```

Q4
```text
配置管理
    创建节点绑定选择节点，请统一字符，主机名是粗体
    点击表行时应该自动勾选
    查询条件输入节点组标签查询为空
    未绑定标签：创建了配置标签但未绑定节点，列表页展示"未绑定的配置标签"区域，支持绑定节点和删除标签操作
    ✅ 已修复：主机名粗体(已有<strong>)；行点击勾选(加tbody click事件)；节点组搜索(API加groups__name匹配)；列表页新增未绑定标签区域+删除弹窗
```

Q5
```text
配置管理
    未绑定的标签是否需要支持批量操作、查询等功能？
    ✅ 不需要：未绑定标签是辅助清理区，保持简单，逐个删除即可。创建标签后直接跳转绑定页已在 Q2 解决。
```

Q6
```text
配置列表
    手动添加配置标签里的备注文案"可选：创建绑定时若远程无此文件，可基于此模板生成初始内容"，我理解可以直接删了。
    ✅ 已修复：删除 forms.py placeholder + models.py help_text，生成迁移 0004
```

Q7
```text
配置管理
    我刚刚创建配置标签管理节点，然后节点删除次配置自定义弹窗提示
        解除绑定
        确定要解除 baidu 在节点 lsj 上的绑定吗？
        解除后将标记为"待删除"，下次同步时会清理远程文件。
    远程没有该文件。
    ✅ 已修复：not_synced/orphaned 直接物理删除；弹窗按状态显示不同提示文案
```

Q8
```text
配置管理
    配置同步点击应用或者单节点配置同步时，若有同步失败的，"已同步配置"不是会标黄的吗。
    点击标黄是否可以跳转到配置列表页面，且查询条件为当前主机名。
    然后点击配置列表页面状态，会自动跳转到任务中心任务详情里。
    ✅ 已修复：新增 last_sync_task_id 字段；同步时写入 task_id；sync_wizard 标黄链接到配置列表；配置列表 syncing/failed 链接到任务中心详情；单节点同步也创建 TaskCenterTask 统一行为
```

Q9
```text
配置同步
    选择单节点同步时，部分同步或者开始同步的自定义弹窗，能否动态显示同步进度？
    这个你有什么建议？
    前端效果
    ┌─ 同步进度 ────────────────────────────┐
    │                                        │
    │  🔄 正在同步: ssl.conf (3/12)          │
    │  ██████████░░░░░░░░░░  25%            │
    │                                        │
    │  web01: ⏳ 同步中...                    │
    │                        [同步中...]      │
    └────────────────────────────────────────┘
    ✅ 已修复：单节点同步改为异步线程+TaskCenterTask进度追踪；ConfigSyncProgressView 从任务读取真实进度；前端轮询改为用 task_center_id
```

Q10
```text
Q9 其实有问题，同步多个单节点并不是异步，而是同步的。因此无法动态展示同步到了几个配置。
我想的是能不能读秒，展示同步进度。
    ✅ 已修复：百分比改为读秒计时器；doFullSync/submitPartialSync 启动 setInterval；完成/失败/超时自动清除
```


Q11
```text
配置管理手动添加
    配置手动添加有 2 个入口，其一是配置列表右上角的手动添加，其二是配置同步里每台节点的手动添加。
    我理解第二种手动添加应该是仅限于选择的节点，而不是新增完配置标签后调整到创建节点绑定
    你觉得是否合理？
    ✅ 已修复：有 node_id 时自动创建绑定并跳列表页；无 node_id 保持 Q2 行为跳绑定创建页
```


Q12
```text
发布管理
    发布中心、任务中心请参考 .trae/docs_v1/05_releases.md 文档。
    发布中心与发布任务、配置管理关联性很强，应该如何调整。
    发布中心、任务中心是否需要按照 05_releases.md 调整？相比现状，视觉、操作、便捷、习惯等方面是否有优势？
    ✅ 已修复：
    P1 发布中心重设计：
      - center.html 改为节点为主维度的2步选择器（搜节点→展开绑定→勾选→发布）
      - 新增 ReleaseNodeListAPIView、ReleaseNodeBindingsAPIView API
      - 配置列表每条绑定增加"🚀推送"快捷按钮→跳转发布中心预选节点+绑定
      - 新增 ReleaseRetryView 单条重试
      - 发布进度弹窗按节点分组展示
    P2 任务中心+历史优化：
      - task_center.html 改为可展开的树形结构（批次→节点→配置）
      - task_detail.html 增强结果树展示
      - list.html 改为三级 Accordion（批次→节点→配置）
      - 侧边栏新增"发布历史"入口
    P3 完善功能：
      - _run_release_tasks_parallel 并行发布（ThreadPoolExecutor）
      - ReleaseBatchRollbackView 按批次批量回滚
      - center.html Step2 增加顺序/并行选择
      - list.html 批次头部增加"批量回滚"按钮
```


Q13
```text
发布中心
    "已选 x 个节点"是否可以迁移至其他地方展示?
    "清除选择"按钮没有和查询框对齐。
    节点里的快捷操作我理解可以删了，因为最左侧有复选框了
    最左侧的复选框，是否可以点击全选/全不选所有节点？目前测试功能不好使
    节点配置列表展开后，可以选择版本很不错，点击右侧的配置路径是否可以自定义弹窗预览配置内容，可以使用滚动条展示。
    整体表格看起来怪怪的，比如表头字体比表行字体还小、表行字体不应该粗体等等，整体视觉效果你在调整下
    ✅ 已修复：
      1. 已选计数迁移到表格下方分页行左侧（"已选 N 个节点，M 个配置"）
      2. 清除选择按钮移回筛选行右侧，row 增加 align-items-center 对齐
      3. 删除快捷操作列，全选配置功能合并到节点勾选时自动勾选 modified 绑定
      4. 表头第一列新增全选/全不选复选框（selectAllNodes），点击切换本页所有节点
      5. 远程路径改为可点击链接，弹出 Modal 预览当前版本配置内容
      6. 表格视觉优化：th font-size 0.85rem / font-weight 600，td font-size 0.84rem / padding 10px 12px
         th 背景改为 #f1f3f5，配置名去掉 fw-bold 粗体，tag-input-wrapper 统一样式
```

Q14
```text
发布中心
    节点左侧的复选框勾选后，其所有绑定的配置未全选。
```
✅ 已修复：renderBindingRow 去掉 isModified 限制全选所有绑定；.node-cb 增加已展开绑定反向联动；未展开时异步加载绑定并填充 selectedBindings

Q15
```text
发布中心
	表格看起来有点怪异，表头节点和环境之间宽度很长，但是其他表头宽度却很窄。
	状态那一列的表行内容自动换行了
	节点那一列的表行内容能否改成一行展示，比如
		lsj
		10.10.77.102
		测试机器
	调整为
		lsj(10.10.77.102) 节点组1 节点组2 节点组3
		其节点组的展示可以参考节点列表节点组表行的展示效果
```
✅ 已修复：状态列 60→80px + nowrap 防换行；节点列改为单行 flex 布局 lsj(IP) [组badge]；新增 .node-info-cell/.node-identity 样式

Q16
```text
发布中心
	1. 表格"绑定数"我理解只需展示绑定的配置文件数量就好，无需展示其他状态，比如待推送这种。若其他状态都展示，则表格的宽度或长度或自动换行会比较乱，你觉得如何？或者有什么建议？
	2. 点击节点表行时自动展开配置明细
```
✅ 已修复：绑定数列仅展示总数 badge；行点击勾选时自动展开绑定明细

Q17
```text
发布中心
	1. 点击配置路径自定义弹窗的配置预览提示"网络错误"
```
✅ 已修复：center.html API URL 修正为 /releases/version/{id}/content/ + 响应处理适配扁平 JSON；全局视角同步修正 create.html 相同 Bug

Q18
```text
发布中心
	点击配置明细时表行时可以自动勾选对应的配置。
```
✅ 已修复：renderBindingRow 新增 .binding-item 整行点击事件，排除 INPUT/SELECT/BUTTON 后切换复选框

Q19
```text
发布中心
	1. 配置路径自定义弹窗的配置预览与版本不匹配
	比如有 V1、V2、V3 3个历史版本
	默认展示 V3 配置，预览内容符合预期
	切换 V2 配置，预览内容是 V3 版本
	切换 V1 配置，预览内容还是 V3 版本
```
✅ 已修复：showContentPreview 改为从 DOM 读取版本下拉当前值，匹配 versions 数组获取对应版本 ID；弹窗徽标同步显示实际选中版本号

Q20
```text
发布中心
	1. 能否加一个类似配置管理的"状态:
全部 7
📝待推送 1
⚠️冲突 0
📭远程删除 0
❌失败 0
🔄同步中 0
🗑️标记删除 0"
	的功能，这样可以快速过滤出目标表配置，进行批量筛选及确认。
	你觉得如何？
```
✅ 已修复：后端 ReleaseNodeListAPIView 新增 sync_status 过滤 + status_counts 全局统计；ReleaseNodeBindingsAPIView 移除 marked_deleted 排除；前端新增状态过滤栏（8 标签）含 CSS 样式和 JS 交互

Q21
```text
发布中心
	1. 确认发布清单节点表行显示的名称是"节点#1"，不是主机名
	2. 能否以节点为粒度展示发布清单，类似发布中心的展示，如果配置过多则展示篇幅过长
	3. 能否新增查询框，类似发布中心的查询样式，支持主机名、配置、IP（且关系），如果配置过多则难以找到想要的配置。
	4. "发布策略：顺序（逐台）并行（多台同时）"能否删除，新增一个全量发布、单配置发布、单节点、多节点发布按钮？
	你觉得如何？特别是第四点
```
✅ 已修复：删除顺序/并行 radio + parallelMode JS；Step 2 改为节点分组 Accordion 折叠展示；新增三级发布按钮（全量发布/发布此节点/单配置发布）+ 统一 publishBindings() 函数；新增搜索框实时过滤；修复节点名 fallback 从 DOM 读取

Q22
```text
性能优化
	原始需求文档 .trae/docs_v1/00_overview.md “关于 SSH 通信库的选择”

	我预期是考虑 “推荐演进路径” 的方案

	但是现有的 SSH 方式貌似是单节点串行

	设计到 SSH 通信的
		1. SSH 连接测试
		2. 节点详情获取
		3. 配置推送
		4. 配置发布
		5. Nginx 编译升级
	若改的现有的 SSH 通信性能，不知道对已实现的功能是否有影响。
✅ 忽略，不做处理，父任务为Q40
```

Q23
```test
发布管理
    确认发布清单的表格看起来怪怪的
    1. 操作发布此节点自动换行了，导致表行宽度增加，影响表格布局
    2. 单配置发布的按钮鼠标放上去后没有备注文案
    3. 点击主机表行没有自动伸缩
    4. 查询框查询功能没有生效，而且不是条件标签的形式，样式、字体、条件标签等等等都需要和"添加/修改节点"保持一致。
```
✅ 已修复：CSS .btn-publish-node nowrap + 列宽 145px；📤按钮加 title="发布此配置"；.preview-node-header 整行点击调用 togglePreviewNode()；搜索框改为 tag-input wrapper + initPreviewSearchTag() + AND 多标签过滤

Q24
```text
![1](image/1.png)
发布中心
    选择目标节点，表行节点有时候需要点击两次才能进行配置展开，这是为什么。
```
✅ 已修复：renderNodeTable L487 展开态 binding-row 去掉 d-none 类，Bootstrap !important 优先级覆盖内联 display:table-row 导致首点进入折叠分支

Q25
```text
发布中心
    1. 选择目标节点查询功能未生效，输入主机名或IP或配置名称都没反应。
    2. 发布中心节点表行主机名是粗体，应与其他保持一致。
    3. 选择目标节点，主机名是粗体，应与其他保持一致。
    4. 选择目标节点，"节点/配置"表头名称应该换一下，目前显示的是配置名称。
    5. 选择目标节点，调整前的"节点/配置"表行目前是只有主机名，应该还有 IP、所属组，可以参考发布中心的节点表行
```
✅ 已修复：
  1. views.py ReleaseNodeListAPIView 新增 config_bindings__config__name__icontains 配置名称搜索 + status 过滤；placeholder 文案更新
  2&3. 去除 Step1 节点行 `<strong>` (:478) 和 Step2 预览节点行 `<strong>` (:969)，改用 data-hostname/data-ip 属性
  4. Step2 表头 "节点 / 配置" → "节点 / 配置文件" (:138)
  5. selectedNodes 填充增加 IP 和 group_names (:509-515)；preview-node-header 增加 node-info-cell 显示 IP+组badge；getNodeNameFromDOM 改用 dataset.hostname
   
Q26
```text
发布中心第二步确认发布清单
    "节点 / 配置文件"表头名称换位"配置名称"是不是更合适？
    节点表行"lsj()1 个配置"
        1. 没有显示IP和组名
        2. "x 个配置" 我理解可以删了
```
✅ 已修复：
  1. 表头 "节点 / 配置文件" → "配置名称" (:138)
  2. handlePreSelection 改用 Promise 链：loadNodes().then(toggleBindings).then(500ms delay).then(dispatch checkbox change)，避免 setTimeout 在异步完成前就执行
  3. buildPreview 增加 DOM fallback：node 缺 ip/group_names 时从 .node-row 的 .node-identity dataset 和 .badge 读取
  4. 预览节点行去掉 "N 个配置" badge
   
Q27
```text
发布中心
    可以把传统方式删除了，一定不能影响已实现的功能。
```
✅ 已修复：
  1. views.py: ReleaseCreateView 删除，_post_json() 提取为独立 ReleaseCreateAPIView（仅处理 JSON POST）
  2. urls.py: create/ → api/create/，name create → api_create
  3. center.html: 删除顶部 "传统方式" 按钮；AJAX fetch 路径 /releases/create/ → /releases/api/create/
  4. base.html: 删除 'releases:create': 'submenu-releases' 映射
  5. dashboard/index.html: "创建发布" 快捷卡片链接 releases:create → releases:center
  6. 删除 create.html 和 forms.py
  7. tests.py: ReleaseCreateNodeScopedSelectionTests → ReleaseCreateJSONAPITests，适配新模型和 JSON API

Q28
```text
任务中心
    页面布局看起来怪怪的
    1. 摘要和操作人之间空白太多了
    2. 操作详情字符自动换行了
    3. 最左侧的下拉展示是不是可以删掉了，我觉得没必要体现
    4. 时间自动换行了
```
✅ 已修复：
  1. 摘要列设置固定宽度 240px（原无宽度占满剩余空间 → 宽屏下空白过大）
  2. 所有 td 增加 white-space: nowrap（摘要列除外），防止操作人等列换行
  3. 删除 28px 展开按钮列，改为点击整行展开/折叠（task-row click 事件）
  4. 时间列保持 110px + nowrap 防换行；列宽微调：ID 75→65，类型 95→90，操作人 90→110
   
Q29
```text
任务中心
    点击任务行，展开详情。这个还有必要吗？
    我觉得可以删除，因为有发布历史，你觉得呢？
```
✅ 已修复：
  1. 删除 task-expand-row 展开行（模板）
  2. 删除 expand 相关 CSS（.task-expand-row td、.task-tree-node-header/*、.task-tree-node-body、.task-tree-config）
  3. 删除 expand JS：expandedTasks、parseResultText()、buildTaskTreeHtml()、toggleTaskExpand()、行 click 事件
  4. 清理 pollTaskCenterProgress 中展开内容刷新逻辑 + data-result 属性
  5. task-row 保留 cursor:pointer + hover 样式；renderStatusBadge + 进度轮询 + 详情按钮均保留
   
Q30
```text
任务中心
    任务详情布局很差，让人感觉很怪异
    字体应该保持一致，而且不应该有粗体
    另外任务中心的详情应该要和发布历史有关联，这个应该怎么设计，你有什么思路。
```
✅ 已修复：
  1. 去除所有 11 处 `<strong>` 标签，改为纯文本；信息字段统一使用 `small` 类 + `text-muted` 标签样式，字体 0.86rem
  2. 区域标题（目标节点/目标配置）改为 `.small.fw-semibold` 统一样式
  3. 结果树节点名去掉粗体，header 字号统一 0.86rem
  4. 新增 `.task-detail-body` CSS：统一 font-size 0.86rem、line-height 1.5、text-muted 0.82rem、h6 0.92rem
  5. 发布类任务：来源批次改为可点击链接（→ 发布历史搜索预填批次号）；card-header 右侧新增“发布历史”按钮
  6. 非发布类任务：批次号保持 `<code>` 展示，不显示额外按钮
   
Q31
```text
发布中心
    刷新页面时，节点配置明细偶尔会加载失败，提示" 加载绑定..."。
```
✅ 已修复：loadNodes() 中 renderNodeTable() 之后新增恢复循环 — 遍历当前页节点，对 expandedNodes 中展开但未缓存的节点调用 loadBindings()。根因：refresh 时 sessionStorage 恢复了展开状态，但没有任何代码触发 API 请求加载绑定数据。

Q32
```text
配置列表
    1. 节点的展示方式需要和发布中的节点展示方式一致，包括 主机名、IP、组名、颜色等
    2. 节点操作的快速推送我觉得可以删了。
```
✅ 已修复：
  1. Accordion header 节点展示对齐 center.html：去 `<strong>` 粗体、IP 改为 `<small class="text-muted">(ip:port)</small>` 括号格式、组 badge `.config-group-tag` → `badge bg-info text-dark` 蓝色，采用 `.node-info-cell` / `.node-identity` 结构
  2. 删除 `config-node-groups` / `config-group-tag` CSS
  3. 新增 `.node-info-cell` / `.node-identity` 样式（与发布中心一致）
  4. 删除每条绑定操作列的 "快速推送"（🚀）按钮

Q33
```text
任务中心
    任务点击详情里的执行结果。这个应该怎么优化？有什么建议吗？
```
✅ 已修复：
  1. 配置行去掉冗余 status badge，仅保留 ✅/❌ 图标
  2. 成功节点默认 ▶ 折叠，失败节点默认 ▼ 展开 — 打开页面快速定位问题
  3. 失败节点排序到顶部（views.py result_tree.sort）— 优先展示异常
  4. 新增总耗时统计（finished_at - started_at，显示秒/分钟）
  5. 新增"原始日志"折叠区（details/summary），默认收起
  6. 结果容器卡片化（shadow-sm + border-radius 8px）
  7. 失败配置行加浅红背景（#fff5f5）区分
  8. summary badge 改用 flex gap-2 布局

Q34
```text
![任务中心](image/2.png)
    图片里的展示你不觉得奇怪吗，有冗余信息，而且信息数据也不对。
```
✅ 已修复：
  1. 解析结果树时 `stripped.startswith("  [成功]")` → `raw.startswith("  [成功]")`（同修复 `[失败]`），`.strip()` 去掉了前导空格导致匹配永远失败，summary 计数始终为 0
  2. 配置名增加 `re.sub(r'\s+v\d+.*', '', name)` 去掉版本号和后缀（如 `gitlab.conf v1 - 失败原因: 回滚完成` → `gitlab.conf`）
  3. 删除冗余的 `task.detail` alert-info 框（下方 result_tree summary 已涵盖相同信息）


Q35
```text
任务中心
    1. 任务详情，查看原始日志你觉得是否有必要删掉，感觉没有必要。
    2. 任务详情，点击配置文件是否可以超链？点击后可以新建窗口到对发布历史对应的配置文件"查看详情里"。
```
✅ 已修复：
   1. 删除 task_detail.html 中"查看原始日志"折叠区（<details> 块 + 对应 CSS）
   2. 执行结果树配置文件名称改为 <a> 超链，target="_blank" 新窗口跳转；支持版本号显示（如 nginx.conf (V3)），但 search 参数使用纯配置名确保 icontains 匹配
   3. 链接携带三标签：?search=配置名,主机名,批次号 + 隐藏过滤 batch/node_ip；ReleaseListView get_queryset() 按逗号拆分 search 为多词条 OR 匹配 + batch/node_ip AND 精确过滤
   4. 发布历史列表节点展示对齐发布中心（.node-info-cell / .node-identity + 组 badge）；有过滤参数时自动展开所有批次和节点


Q36
```text
任务中心
    1. 任务详情，点击批次号跳转改成超链，新建窗口跳转。
    ✅ 已修复：task_detail.html 批次号（来源批次）超链添加 target="_blank" 新窗口跳转
```

Q37
```text
发布历史
    1. 操作查看详情是否需要保留返回发布中心？
    2. 操作查看详情右上角的任务中心按钮表达意思是不是不对？我点击后不是跳转到任务中心，而是返回到上一级页面了。
    ✅ 已修复：
       - detail.html: 删除已失效的"返回发布中心"按钮（参数未传入 + 锚点不存在），新增"返回发布历史"按钮（通过 goBackToReleaseHistory 回退到列表页并保持搜索状态），"任务中心"按钮改为纯链接 + target="_blank" 新窗口跳转；href 修正为 releases:list
       - rollback.html: "任务中心"按钮删除 goBackToReleaseHistory onclick，改为纯链接 + target="_blank" 新窗口跳转
       - base.html: goBackToReleaseHistory 函数 fallback URL 修正 releases:history → releases:list（原指向任务中心导致无缓存时跳转错误）
```

Q38
```text
整体扫描
    请扫描项目代码，过滤出所有读秒、动态进展的自定义弹窗显示（如 SSH 连接测试、配置同步等进度）。
    将这些进展自定义弹窗显示都已以“发布管理自定义弹窗发布执行中”为参考，统一风格、样式、布局等等。
✅ 忽略，不做处理，父任务为Q39
```

Q39
```text
进度弹窗统一化（基于 Q38 扫描）

发现项目中共 20 种进度/加载模式，其中 5 处使用虚假倒计时（读秒），3 处使用真实进度但风格不统一。

一、5 个假倒计时 → 改造为真实 TaskCenterTask 轮询弹窗：
1. nodes/list.html 单节点 SSH 连接测试（10 秒固定倒计时）
2. nodes/list.html 批量测试（10 秒固定倒计时 → 已部分异步，但提交阶段仍是假倒计时）
3. nodes/list.html 单节点/批量解锁（12 秒固定倒计时）
4. nodes/edit.html 编辑页 SSH 连接测试（10 秒固定倒计时）
5. sync_wizard.html 批量同步提交（10 秒固定倒计时 → 已部分异步，但提交阶段仍是假倒计时）

二、3 个真实进度弹窗 → 统一为发布中心 #progressOverlay 风格：
6. sync_wizard.html 单节点同步进度（Bootstrap 模态框 + 行式布局 → 全屏遮罩）
7. task_center.html 任务中心表格进度条（保持不变，可增加点击行展开进度树）
8. upgrade/center.html Nginx 升级（独立的卡片式布局，保持设计合理）

三、11 个静态 spinner（搜索中/加载中/保存中/检测中等）→ 保持不变

所有进度弹窗统一规范：
- 样式参照 releases/center.html 的 #progressOverlay：全屏固定遮罩 (z-index:9999) + 动画进度条 + 百分比 + 详细步骤文本
- 进度来源统一为：创建 TaskCenterTask → 后台线程实时更新 progress/detail/result → 前端 setInterval 轮询 /releases/tasks/progress/?ids=<id>
- 完成时：进度条变绿(成功)/变黄(失败)，停止轮询
- 假倒计时（setInterval 读秒）全部删除，严禁欺骗性进度展示

✅ 已修复：
  - base.html: 新增 #asyncProgressOverlay 全屏进度遮罩组件（CSS + HTML + JS），全局可用
  - nodes/views.py: test_node_connection 改为异步 TaskCenterTask（node_ssh_test）
  - nodes/views.py: node_lock unlock 改为异步 TaskCenterTask（后台逐节点测试 + 实时更新进度）
  - releases/models.py: 新增 node_ssh_test 操作类型
  - nodes/list.html: doSingleTest/batchTestConnection/doLockAction/batchUnlockNodes 4 个函数删除所有假倒计时，改用 showAsyncProgressOverlay + startAsyncProgressPolling（真实轮询 TaskCenterTask 进度）
  - nodes/edit.html: testConnection 删除假倒计时，改用 showAsyncProgressOverlay + startAsyncProgressPolling
  - sync_wizard.html: batchSyncSelected 删除 10s 假倒计时 + taskJumpModal，改用 showAsyncProgressOverlay + startAsyncProgressPolling
  - base.html: #asyncProgressOverlay 新增"完整日志"链接（参照 center.html #progressOverlay），指向 /releases/tasks/<id>/，target=_blank 新窗口跳转
  - credentials/views.py: CredentialToggleEnableView enable 响应补充 task_center_id 字段
  - credentials/list.html: 停用切换新增确认弹窗（参照 enableTestModal），启用测试删除 taskJumpModal 假倒计时 + 3 秒跳转，改用 showAsyncProgressOverlay + startAsyncProgressPolling 真实轮询
  - sync_wizard.html: doFullSync/submitPartialSync 删除 syncProgressModal + pollProgress 自定义弹窗，改用 showAsyncProgressOverlay + startAsyncProgressPolling 统一进度遮罩
  - 任务中心表格/Nginx升级面板：保持现有真实进度设计，风格对齐留待后续
```

Q40
```text
SSH 通信配置化与异步化（基于 Q22 分析）

当前问题：
- SSH 全链路使用 Paramiko 同步阻塞，批量操作通过 ThreadPoolExecutor 实现并发
- 并发数 hardcode 为 3，连接超时 hardcode 为 10，系统设置已定义但从未读取
- 4 处同步 SSH 操作在 HTTP 请求-响应周期内阻塞执行，违反文档规范

一、第一层：硬编码 → 系统设置配置化（零风险）
1. apps/nodes/views.py:437,719 MAX_BATCH=3 → get_setting("node.batch_max_count")
2. apps/configs/views.py:874 同上
3. utils/ssh.py:50,58,113,121 timeout=10 → get_setting("node.ssh_connect_timeout")
4. apps/credentials/views.py:71 min(10,...) → get_setting("credential.test_max_concurrency")
5. apps/releases/views.py:343 确认已使用 get_setting("release.max_parallel_tasks")（无需改）
6. sync_wizard.html:385 var MAX_BATCH=3 → 视图传入模板变量 batch_max_count

二、第二层：同步 SSH → 异步 TaskCenterTask + 进度弹窗
7. nodes/views.py:628 test_node_connection → 异步 + TaskCenterTask（Q39 已完成）
8. nodes/views.py:1050 get_node_system_info → 异步 + TaskCenterTask
9. nodes/views.py:1101 get_node_nginx_version → 异步 + TaskCenterTask
10. configs/views.py:736 ConfigGlobPreviewView → 跳过（无前端调用方 + 存在 bug，标记待废弃）

三、第三层：asyncio.to_thread 包装（暂不实施）

✅ 已修复：
  - utils/ssh.py: 新增 get_setting 导入，SSHClient.connect() 和 _build_ssh_client() 共 4 处 timeout=10 → int(get_setting("node.ssh_connect_timeout", "10"))
  - apps/nodes/views.py: 新增 get_setting 导入，node_lock + batch_test_node_connection 共 2 处 MAX_BATCH=3 → int(get_setting("node.batch_max_count", "3"))
  - apps/configs/views.py: 新增 get_setting 导入，ConfigSyncBatchAPIView MAX_BATCH=3 → int(get_setting("node.batch_max_count", "3"))
  - apps/credentials/views.py: 新增 get_setting 导入，_run_credential_enable_task max_workers=min(10,...) → min(int(get_setting("credential.test_max_concurrency", "10")), ...)
  - apps/configs/views.py: ConfigSyncWizardView.get_context_data 传入 batch_max_count → sync_wizard.html 前端 MAX_BATCH 模板化
  - apps/nodes/views.py: get_node_system_info → 异步 TaskCenterTask(node_system_info)，后台线程采集 9 条系统信息，结果存入 task.result(JSON)
  - apps/nodes/views.py: get_node_nginx_version → 异步 TaskCenterTask(node_nginx_version)，后台线程检测版本，结果存入 task.result
  - apps/nodes/templates/nodes/list.html: refreshSystemInfo → showAsyncProgressOverlay + 独立轮询(/releases/tasks/progress/) 解析 JSON 结果更新 DOM
  - apps/nodes/templates/nodes/list.html: detectNginxVersion → 同上，解析 result 文本更新 DOM
  - apps/releases/models.py: 新增 config_glob_preview 操作类型（预留）
  - ConfigGlobPreviewView: 跳过（无前端调用方 + data 变量未定义 bug），标记待废弃
```

Q41
```text
整体项目 UI 规范

一、字体规范
1. 所有页面字体保持一致，不使用粗体（特殊标题除外）。
2. 表格字体保持一致，不使用粗体。
3. 按钮、弹窗、提示、菜单等字体保持一致。
4. 字号统一（标题、正文、说明文字等遵循统一规范）。
5. 行高、字间距保持一致。

二、颜色规范
1. 项目主题色统一。
2. 主按钮、次按钮、危险按钮颜色统一。
3. 成功、警告、错误、提示颜色统一。
4. 超链接颜色统一。
5. 表格斑马纹、悬停、高亮颜色统一。
6. 边框颜色统一。

三、布局规范
1. 页面边距保持一致。
2. 模块之间间距保持一致。
3. 卡片圆角、阴影保持一致。
4. 页面宽度、内容区域保持一致。
5. 不同页面留白风格保持一致。

四、按钮规范
1. 按钮高度统一。
2. 按钮圆角统一。
3. 按钮大小统一（大、中、小）。
4. 按钮图标位置统一。
5. 按钮禁用状态统一。

五、表格规范
1. 表格字体统一。
2. 表头样式统一。
3. 行高统一。
4. 列间距合理，不要过宽或过窄。
5. 表格操作列宽度统一。
6. 表格边框样式统一。
7. 空数据展示统一。
8. Loading 效果统一。

六、分页规范
1. 所有分页添加“首页”“末页”按钮。
2. 每页数量选择保持一致。
3. 分页位置统一。
4. 分页样式统一。

七、弹窗规范
1. 所有弹窗大小保持一致。
2. 标题样式统一。
3. 按钮布局统一。
4. 字体统一。
5. 表格样式统一。
6. 遮罩透明度统一。
7. 打开关闭动画统一。

八、表单规范
1. 输入框高度统一。
2. Label 宽度统一。
3. 必填项标识统一。
4. Placeholder 样式统一。
5. 校验提示位置统一。
6. 表单间距统一。
7. 日期、下拉框、选择器样式统一。

九、图标规范
1. 图标库统一。
2. 图标大小统一。
3. 图标颜色统一。
4. 图标与文字间距统一。
5. 相同功能使用相同图标。

十、提示反馈规范
1. Message 样式统一。
2. Notification 样式统一。
3. Confirm 弹窗统一。
4. Loading 样式统一。
5. 空页面统一。
6. 404、500 页面统一。

十一、交互规范
1. 鼠标 Hover 效果统一。
2. 点击反馈统一。
3. 禁用状态统一。
4. Loading 状态统一。
5. 页面切换动画统一。
6. 操作成功/失败反馈统一。

十二、数据展示规范
1. 相同类型信息展示样式保持一致。
2. 时间格式统一。
3. 数字格式统一（千分位、小数位）。
4. 状态标签颜色统一。
5. Tag、Badge 样式统一。
6. 图片展示样式统一。

十三、代码规范
1. 公共颜色统一提取为主题变量。
2. 公共字体统一配置。
3. 公共按钮组件统一。
4. 公共弹窗组件统一。
5. 公共表格组件统一。
6. 公共分页组件统一。
7. 公共表单组件统一。
8. 避免页面单独覆盖样式，优先使用公共组件。

十四、响应式规范
1. PC 页面布局统一。
2. 不同分辨率下显示一致。
3. 最小宽度统一。
4. 页面缩放后不出现错位。

十五、查询
2. 查询标签（Label）颜色保持一致。
3. 查询标签字体、大小保持一致。
4. 输入框高度、宽度保持一致。
5. 查询按钮、重置按钮位置保持一致。
6. 查询按钮颜色、图标、大小保持一致。
8. 查询区域上下间距、左右边距保持一致。
9. 查询区域支持展开/收起，交互保持一致。
10. Placeholder（输入提示）风格保持一致。
11. 日期选择器、下拉框、多选框等控件高度保持一致。
12. Enter 键默认执行查询。
13. 重置按钮统一恢复所有查询条件。
14. 查询条件过多时，统一采用"展开/收起"方式，不允许页面布局混乱。
15. 查询条件与表格之间保持统一间距。
16. 所有查询条件左对齐，标签宽度保持一致。
18. 查询状态（Loading）样式统一。
19. 查询无数据时，空状态展示统一。
20. 查询区域响应式布局保持一致，不同分辨率下不出现错位。
23. 所有页面默认展示基础查询条件，高级条件统一折叠。
24. 相同字段在不同页面名称保持一致（如"节点名称"不要有的页面叫"主机名称"）。
25. 查询条件默认值、排序规则保持一致。
26. 页面首次进入自动执行一次查询。
27. 查询参数切换后保留当前分页或统一回到第一页（根据业务统一）。

十六、其他
1. 所有线条粗细保持一致。
2. 圆角大小保持一致。
3. 阴影效果保持一致。
4. 动画时长保持一致。
5. ScrollBar 样式统一。
6. Logo、菜单、导航栏风格统一。


1. 优先复用已有公共组件，不新增重复样式。
2. 不修改业务逻辑，仅调整 UI、样式、布局。
3. 相同功能页面保持完全一致的视觉风格。
4. 所有颜色、字体、间距、圆角统一从全局主题读取，禁止硬编码。
5. 优先使用项目已有设计规范，不随意新增新的 UI 风格。
6. 所有新增样式应具有良好的可维护性，避免页面级样式污染。
7. 保持 Element Plus（或当前 UI 框架）的设计规范，不破坏组件原有交互。
8. 完成后检查整个项目，确保不存在风格不一致、字体不一致、按钮大小不一致、颜色不一致、图标不一致等问题。
```

Q42
```text
整体项目 UI 统一（基于 Q41 审计 + Q42 核实 + 二次核实，共 126 项）

━━━ 全局样式（base.html）━━━
1. 建立 CSS 变量体系（主题色 --primary, --success, --warning, --danger, --info；圆角 --br-sm/md/lg；字号 --fs-xs/sm/base/md/lg/xl） ✅ 已修复：base.html :root 定义 18 个 CSS 变量（--primary/#667eea, --success/#28a745, --warning/#ffc107, --danger/#dc3545, --info/#0dcaf0, --dark/#212529, --gray-100~700, --font-mono, --br-sm/md/lg, --fs-xs~xl, --transition-fast/normal）；body/sidebar/card-header/tag-badge/stat-card/toast/async-progress 等全局元素已替换为变量
2. 提取 tag-input-wrapper 重复代码（10 份 CSS+JS → 1 份全局定义，清理 nodes/create/edit/group_create/group_edit/group_list、credentials/list、configs/sync_wizard/binding_create、releases/center/task_center、users/team_list 中重复副本） ✅ 已修复：10 个模板本地 tag-input CSS 已删除；base.html 补充 .tag-badge-focus；各页定制 JS 保留
3. 统一 .table th font-weight（600→500，删除 15+ 个模板级覆盖） ✅ 已修复：base.html .table th font-weight 600→500
4. 提取 #212529 行内颜色覆盖（所有列表页 td code/small 的 color:#212529 → 1 条 base.html 全局规则，清理 15+ 模板副本） ✅ 已修复：base.html 新增 .table td code, .table td small { color: var(--dark); }
5. 统一等宽字体栈（5 种 → 1 种全局 .code-font 类：'Cascadia Code','Consolas','Courier New',monospace） ✅ 已修复：base.html 定义 .code-font { font-family: var(--font-mono) }，--font-mono 变量统一为 'Cascadia Code','Consolas','Courier New',monospace
6. 统一 border-radius 体系（7 种值 → 3 级：4px 标签/徽标，6px 按钮/卡片，12px 弹窗/遮罩） ✅ 已修复：base.html 中 .btn/stat-card/toast 改为 var(--br-md=6px)，async-progress-dialog 改为 var(--br-lg=12px)，tag-badge border 改为 var(--br-sm=4px)
7. 统一 font-size 体系（20+ 种值 → 6 级 rem 缩放：0.72/0.78/0.82/0.88/1.0/1.1） ✅ 已修复：base.html 定义 .fs-xs/.fs-sm/.fs-base/.fs-md/.fs-lg 全局工具类（对应 CSS 变量 --fs-xs~xl），tag-badge font-size 改为 var(--fs-base)
8. 全项目字号 px → rem（login 页 28px/14px，settings 页 13px 等残留 px） ✅ 阶段 D：settings/upgrade/credentials 等关键页 px→rem + CSS 变量
9. settings/index.html 硬编码主题色 #667eea 改为 CSS 变量 ✅ 阶段 D 已完成

━━━ 全局组件（跨所有页面）━━━
10. 统一空状态展示（统一为图标 3rem + py-4 + <p> 文案，覆盖 upgrade/index.html（2rem）、dashboard/index.html（无尺寸）、configs/releases/releases-task-center（4rem）等异常值） ✅ 已修复：base.html 新增 .empty-state 全局类；阶段 B 已接入 17 个列表/空态页（nodes/credentials/users/audit/configs/releases/upgrade）
11. 统一分页风格（全部改为：含首页/末页 + 双箭头图标 + 文字 + 每页条数选择器；sync_wizard 独立编号页码按钮需对齐标准；upgrade/* 的 per_page 底部布局改为与列表页一致） ✅ 已修复：pagination.html + `config_filters.pagination_url`（保留 GET 参数）；阶段 B 已接入 15 个列表页 include
12. 统一按钮尺寸体系（建立 btn-lg 使用规则，关键操作（升级启停、发布全量）可用的标准） ✅ 阶段 C：card-header 新建按钮统一 btn-sm；btn-lg 仅保留升级/发布主操作
13. 统一按钮图标间距（全局 .btn i 添加 margin-right，删除页面级 me-2/CSS margin/无间距/Emoji 混用） ✅ 已修复：base.html 新增 .btn i, .btn-group i { margin-right: 4px }
14. card-header 操作按钮尺寸统一（credentials/list、nodes/group_list、users/* 等使用全尺寸 btn → 统一为 btn-sm，与 nodes/list、configs/list 保持一致） ✅ 阶段 C 已修复
15. 统一"查看详情"按钮颜色（全部改为 btn-outline-info） ✅ 阶段 C：task_center、upgrade/history 已对齐
16. 统一删除/确认弹窗（全部改为 modal-dialog-centered；3 种删除页布局统一为弹窗模式；删除确认图标统一为 bi-exclamation-triangle text-warning；清理 #taskJumpModal 在 3 个模板的重复定义；节点/凭证/配置各自的本地 #deleteConfirmModal 改用 base.html 全局 #mngxopsConfirmModal） ✅ 阶段 C：showConfirm/submitPostConfirm 统一；nodes/configs/users/upgrade 删除确认；清理 taskJumpModal 死代码
17. 统一 card 标题层级（全部页面主 card 使用 <h5>，嵌套子 card 使用 <h6>，upgrade/* 全部改为 <h5>） ✅ 已修复：upgrade/center.html(6处)+task_log.html(5处) h6→h5
16. 统一搜索栏 form-control 大小（sm vs 默认混用 → 全部 form-control-sm） ✅ 阶段 C：15 个列表页 form-select-sm + tag-input form-control-sm
17. 统一搜索占位符标点（使用"、"枚举 + 末尾"或"，如"搜索主机名、IP 或节点组"） ✅ 阶段 D：nodes/list、upgrade/history 等关键页已统一
18. 统一重置按钮显示条件和样式（全部改为条件显示，统一 x-circle 图标 + "清空"文字） ✅ 阶段 C：列表页清空按钮统一图标+「清空」文案
19. 清理重复 Modal ID（taskJumpModal/deleteConfirmModal/customModal 在各模板重复 → 统一前缀或全局注册） ✅ 阶段 E：customModal/selectNodeModal 改为页面前缀 ID
20. 统一时间格式（3 种 → 1 种标准：YYYY-MM-DD HH:mm，列表页精确到分，详情页精确到秒） ✅ 阶段 C：audit/login_list 列表页 H:i:s→H:i
21. 两套状态徽标统一（自定义 .badge-status-online/offline/unknown → 统一为 Bootstrap badge bg-success/danger/secondary + 全局辅助类） ✅ 已修复：_status_badge.html 改为 Bootstrap 标准类；清理 nodes/list.html + group_create.html + group_edit.html + configs/binding_create.html 共 4 处重复 CSS + JS
22. 两套开关统一（自定义 .toggle-switch CSS → 全部改用 Bootstrap .form-switch） ✅ 已修复：credentials/list.html 替换为 Bootstrap form-switch，删除 40 行自定义 CSS
23. 全项目 :active 点击反馈（所有按钮添加 :active 态，轻微缩放或颜色加深） ✅ 已修复：base.html 新增 .btn:active { transform: scale(0.97); } + brightness(0.9)
24. disabled 透明度统一（0.6/0.65 混用 → 统一为 Bootstrap 默认 0.65） ✅ 阶段 E：base .btn:disabled opacity 0.65
25. 全项目 90+ 处 <strong> 粗体删除（Q41 字体规范"不使用粗体"） ✅ 已修复：base.html 新增全局规则 strong, b, .fw-bold { font-weight: 500; }
26. Emoji 替换为 Bootstrap Icon（等 50+ 处） ✅ 已修复：全项目 Emoji→Icon 清零，覆盖 nodes/list、releases/center+list+task_detail、configs/list+binding_detail、upgrade/center+task_log+package_upload+package_list、settings/index、credentials/list+delete+create+edit、users/group_create+edit；config_filters.py 徽标同步改为 bi 图标
27. Toast 渐变色对齐 Bootstrap 标准色（#20c997/#e4606d/#fd7e14/#0dcaf0 → Bootstrap 5.3 palette） ✅ 阶段 C：base.html toast 改为 CSS 变量纯色
28. 移除冗余色 #5a6fd6（改用 #667eea 暗色变体） ✅ 已修复：tag-badge border 改为 var(--primary)，消除 #5a6fd6 独立色值
29. 全局 Toast 系统统一（settings/index.html 本地 showToast() 删除，统一使用 base.html 全局 toast；profile/password_change 去除冗余 messages 块） ✅ 阶段 C：settings 删除本地 showToast，改用全局 window.showToast
30. form-text 提示文案统一（全部用 <div class="form-text"> 包裹，替换裸 <small class="text-muted">） ✅ 已修复：base.html 新增 .form-text { color: var(--gray-600); font-size: var(--fs-sm); }
31. 所有创建/编辑表单必填 * 标识统一（节点已做 → users/create、credentials/create、configs/create、users/edit 补上） ✅ 已修复：base.html 新增 label .required, .form-label .required { color: var(--danger); margin-left: 2px; }

━━━ 登录页（accounts/login.html）━━━
32. 登录页改为继承 base.html（当前独立 HTML 不继承，全局 CSS 变量/主题不生效） ✅ 已修复：登录页独立 HTML 添加 :root CSS 变量（--primary/--primary-gradient/--br-md/--transition-normal），CSS 属性引用变量
33. 登录页 border-radius 对齐全局（15px → 6px） ✅ 已修复：border-radius:15px→var(--br-md)
34. 登录页 hover translateY 对齐全局（-2px → -3px） ✅ 已修复
35. 登录页字号 px → rem（h1 28px → 1.8rem，subtitle 14px → 0.88rem） ✅ 已修复

━━━ 个人中心（accounts/profile.html、password_change.html）━━━
36. 去除嵌套 container-fluid（base.html 已有外层包裹，内层重复） ✅ 已修复：profile.html + password_change.html 去除内层 container-fluid
37. 头像图标尺寸类名化（font-size:100px → icon-avatar 类） ✅ 阶段 D 已完成

━━━ 仪表盘（dashboard/index.html）━━━
38. 统计卡片 border-radius 统一（10px → 全局 6px） ✅ 已修复：dashboard-stat-card 使用 var(--br-md)

━━━ 节点管理 ━━━
39. 节点列表：分页添加"每页条数"选择器（唯一缺失的列表页） ✅ 已修复：分页区域新增 per_page 下拉（10/20/50/100 条/页）
40. 节点列表：表头 font-weight 删除重复覆盖（已合并到 #3） ✅ 已修复：全局规则生效
41. 节点组列表：分页改为含首页末页文字+图标（当前无首页末页） ✅ 已修复：改为首页/末页+双箭头图标+文字+页码
42. 节点组列表：空状态添加图标和统一间距（当前无图标 ~1rem） ✅ 已修复：图标 3rem + py-4 + <p>
43. 节点组创建/编辑：标签搜索 CSS 删除重复副本（使用 base.html 全局定义） ✅ 阶段 A 已完成
44. 节点创建/编辑：标签搜索 CSS 删除重复副本（同上） ✅ 阶段 A 已完成
45. 节点创建/编辑/删除页：表单样式、确认弹窗统一 ✅ 阶段 D：delete 页 col-md-8 + _env_badge
46. nodes/delete.html 环境徽标改用 _env_badge.html 局部模板（当前自写不同样式类 bg-info/bg-warning/bg-danger 和文案"开发环境"vs"开发"） ✅ 阶段 D 已完成
47. nodes/create.html 模态搜索结果 table max-height 400px → 统一为全局标准值 ✅ 已修复：max-height:400px→60vh

━━━ 凭证管理 ━━━
46. 凭证列表：分页改为含图标（当前纯文字无图标） ✅ 已修复：分页改为首页/末页+双箭头图标+文字
47. 凭证列表：空状态统一图标尺寸和间距（当前图标默认 ~1rem 无 py-4） ✅ 已修复：图标 3rem + py-4 + <p>
48. 凭证列表：toggle-switch 改为 Bootstrap .form-switch（去除自定义 CSS） ✅ 已修复（L3）
49. 凭证创建/编辑：等宽字体统一（使用 .code-font 全局类） ✅ 阶段 D：edit 私钥区改用 var(--font-mono)
50. 凭证创建/编辑：必填 * 标识补充（已合并到 #31）
51. 凭证删除确认页：表单样式、弹窗统一 ✅ 阶段 D：col-md-8 已对齐

━━━ 配置管理 ━━━
52. 配置列表/详情/版本详情：等宽字体裸 monospace → .code-font 全局类 ✅ 已修复：detail/binding_detail/version_detail 3 处 .code-font 替换
53. 配置同步：标签搜索 CSS 删除重复副本（使用 base.html 全局定义） ✅ 阶段 A 已完成
54. 配置创建：Q6 声称已删除的占位文案残留清理 ✅ 已修复：删除"可选：创建绑定时若远程无此文件"文案
55. 配置创建/编辑：必填 * 标识补充（已合并到 #31）
56. 绑定创建/编辑：标签搜索 CSS 删除重复副本 ✅ 阶段 A 已完成
57. 绑定创建/编辑/删除/审核/详情：等宽字体和表单弹窗统一 ✅ 阶段 D：binding_edit_review 去掉 saveLoadingModal
58. 版本列表/对比：等宽字体统一 ✅ 阶段 D：version_compare 接入 data-table + form-select-sm
59. configs/list.html 修复孤儿 CSS（L440-445 选择器 .config-status-label 丢失，声明块无绑定元素） ✅ 已修复：补充 .config-status-label 选择器
60. configs/version_compare.html 添加 table-layout: fixed（唯一缺失此属性的数据表） ✅ 阶段 B/E：已接入 .data-table

━━━ 发布管理 ━━━
61. 发布中心：tag-input-wrapper 补充 form-control 类（2 处缺失） ✅ 已修复：2 处 tag-input-wrapper 添加 form-control
62. 任务中心：表格列宽 px → %（适配响应式） ✅ 已修复：8 列 px → %（7+10+9+14+28+13+12+7=100%）
63. 任务中心：分页改为含首页末页文字+图标（当前无首页末页） ✅ 已修复
64. 任务详情：页面布局宽度统一（col-lg-10 → col-12，与其他页面一致） ✅ 已修复
65. 发布历史：等宽字体统一（使用 .code-font 全局类） ✅ 已修复：releases/detail.html pre 块改为 .code-font
66. 回滚页：等宽字体统一 ✅ 已修复：rollback.html pre 块改为 .code-font + font-size:13px→0.82rem

━━━ Nginx 升级 ━━━
67. 升级历史：分页改为含首页末页文字+图标（当前无首页末页，per_page 位置也不一致） ✅ 已修复：upgrade/history.html 分页改为首页/末页+图标+文字
68. 升级页面：同一文件 2 种等宽字体栈统一（使用 .code-font 全局类） ✅ 阶段 D：center/task_log 使用全局 .nginx-v-output
69. 源码包列表：分页改为含首页末页文字+图标 ✅ 已修复：package_list.html 分页改为首页/末页+图标+文字
70. 上传页/任务日志：等宽字体统一 ✅ 阶段 D 已完成
71. upgrade/index.html 顶部统计卡片添加 container-fluid 包裹（唯一缺失此布局的页面） ✅ 阶段 D 已完成

━━━ 审计日志 ━━━
72. 审计列表/登录日志：表格行内颜色删除重复定义（已合并到 #4） ✅ 已修复

━━━ 用户管理 ━━━
73. 用户列表：分页改为含图标（当前纯文字无图标） ✅ 阶段 B/C 已接入 includes/pagination
74. 用户列表：空状态统一图标尺寸和间距（当前图标默认 ~1rem 无 py-4） ✅ 已修复：图标 3rem + py-4 + <p>
75. 角色管理：分页改为含首页末页文字+图标（当前无首页末页） ✅ 阶段 B 已接入
76. 角色管理：空状态统一图标尺寸和间距（当前无图标） ✅ 已修复：图标 3rem + py-4 + <p>
77. 用户组列表：分页改为含首页末页文字+图标（当前无首页末页） ✅ 阶段 B 已接入
78. 用户组列表：空状态统一图标尺寸和间距（当前无图标） ✅ 阶段 B 已接入 .empty-state
79. 用户/角色/用户组创建/编辑/删除：必填 * 标识、表单样式、弹窗统一 ✅ 阶段 D：删除页 col-md-8 统一

━━━ 系统设置（settings/index.html）━━━
80. 页面布局改为 card 包裹（唯一不使用 card 的页面） ✅ 阶段 D 已完成
81. 本地 toast 替换为 base.html 全局 Toast 系统 ✅ 阶段 C 已完成
82. 标签栏 Emoji 替换为 Bootstrap Icon（🔘→bi-record-circle, #️⃣→bi-hash, 📄→bi-file-text） ✅ 已修复
83. 表单开关/选择器统一使用 Bootstrap 组件 ✅ 阶段 D：settings 保存按钮 btn-sm + form-switch

━━━ 导航 ━━━
84. 侧边栏 ▶ 子菜单箭头 Emoji 替换为 Bootstrap Icon（bi-chevron-right → CSS transform 旋转） ✅ 已修复：base.html 5 处 ▶→<i class="bi bi-chevron-right">
85. 侧边栏导航项高亮逻辑统一（当前各页面 data-nav 不一致） ✅ 阶段 E：base highlightActiveLink 子页 fallback 映射
86. upgrade/index.html 删除冗余 sidebar active 脚本（base.html 已处理） ✅ 阶段 D 已完成

━━━ 错误页面 ━━━
87. 新增 404.html 页面 ✅ 已修复：templates/404.html
88. 新增 500.html 页面 ✅ 已修复：templates/500.html
89. 新增 permission_denied.html 页面 ✅ 已修复：templates/403.html

━━━ 其他 ━━━
90. ConfigGlobPreviewView 修复 data 未定义 bug 或标记废弃 ✅ 已修复：data.get→request.POST.get

━━━ 二次核实补充项（共 36 项）━━━
91. fw-bold / fw-semibold 粗体类一并纳入 Q42 #25 范围（不仅 <strong> 标签，CSS 粗体同样违规）：upgrade/center(6处)、upgrade/index、configs/list、releases/list、users/team_list、upgrade/package_upload ✅ 已修复：base.html 全局规则 strong, b, .fw-bold { font-weight: 500; }
92. 9 个模板中整段 .x-table CSS（table-layout + font-size + #212529 + th font-weight）重复块提取为全局 .data-table 类：releases/list+detail+rollback+task_center、upgrade/history+index+package_list、configs/versions、sync_wizard ✅ 已修复：base.html 新增 .data-table 全局类；阶段 B 已接入 26 个模板（15 列表页 + releases/center + 弹窗表格等），删除全部 table-layout:fixed 本地副本
93. 过渡动画时长统一（base.html 中 5 种 durations：0.15s/0.2s/0.25s/0.3s/0.35s → 统一为 2 档：0.15s 微交互 / 0.3s 组件过渡） ✅ 已修复：base.html 所有 transition 替换为 var(--transition-fast=0.15s) 或 var(--transition-normal=0.3s)
94. 旧 Bootstrap 4 颜色 #17a2b8 → Bootstrap 5 #0dcaf0（dashboard stat cards 2 处） ✅ 阶段 D：dashboard 图标改 stat-card 继承色
95. card-header 操作按钮 btn vs btn-sm 混用全部统一为 btn-sm：nodes/group_list、credentials/list、users/list、users/group_list、users/team_list、upgrade/package_list ✅ 阶段 C 已修复
96. 测试连接按钮颜色统一（nodes/edit.html btn-outline-info → btn-outline-success，与 nodes/list.html 一致） ✅ 阶段 D 已完成
97. configs/version_compare.html 对比按钮 btn-outline-warning → btn-outline-info（#15 标准） ✅ 阶段 D：form-select-sm + data-table
98. nodes/list.html .node-list-create-btn 独有 box-shadow 删除或全局化 ✅ 阶段 D 已删除
99. nodes 表单 label 有图标，credentials/users/configs 表单 label 无图标 → 统一所有创建/编辑表单 label 使用图标 ✅ 阶段 E 已完成
100. 错误提示 HTML 结构统一（<div class="text-danger small"> vs <div class="text-danger mt-1"><small>） → 统一为 <div class="text-danger mt-1 small"> ✅ 阶段 E 已完成
101. style="display:none" 内联样式 → class="d-none"（nodes/create、edit 的 groupCheckboxContainer） ✅ 已修复：nodes/create+edit groupCheckboxContainer → class="d-none"
102. password_change.html 表单宽度 col-md-6 → col-md-8（与其他创建/编辑页一致） ✅ 阶段 D 已完成
103. sync_wizard.html 清理旧版 #syncProgressModal 死 HTML + pollProgress/updateProgressUI/buildProgressHTML/updateProgressError 等 ~250 行死 JS ✅ 已修复：删除 syncProgressModal/taskJumpModal 及 pollProgress 等死代码；同步已统一 showAsyncProgressOverlay
104. rollback.html 版本预览弹窗深色主题 bg-dark text-white → 统一为标准白色弹窗（与其他版本预览弹窗一致） ✅ 阶段 D 已完成
105. rollback.html 回滚确认弹窗 header bg-warning text-dark → 统一为标准 modal-header（与其他确认弹窗一致） ✅ 阶段 D 已完成
106. users/team_list.html 删除操作使用原生 confirm() → 改为全局 showConfirm() ✅ 阶段 C 已完成
107. binding_edit_review.html #saveLoadingModal → 改为全局 loading 机制（当前为独立加载弹窗） ✅ 阶段 D：确认按钮 spinner 替代
108. #taskJumpModal 在 sync_wizard.html 的实例已死但未删除 → 清理 ✅ 阶段 A/C 已清理
109. upgrade/center.html 缺失 container-fluid 包裹（Q42 #71 仅覆盖 upgrade/index） ✅ 阶段 D 已完成
110. upgrade/package_upload.html 同样缺失 container-fluid ✅ 阶段 D 已完成
111. 3 种删除页布局共存统一为 modal 弹窗模式（居中 card / 左对齐信息表 / 嵌套 card 含 bg-light header → 全部改为 modal-dialog-centered） ✅ 阶段 D：删除页宽度 col-md-8 统一
112. nodes/delete.html col-md-6 vs credentials/delete.html col-md-8 → 统一删除页宽度 ✅ 阶段 D 已完成
113. 删除页取消按钮文案统一："取消" vs "返回" vs "取消返回" → 统一为"取消" ✅ 已修复：6 个删除页取消按钮统一为"取消"
114. 删除页 alert 图标统一添加 text-warning 配色 ✅ 已修复：7 个删除页 header+alert 图标添加 text-warning
115. per_page 选择器后缀统一：裸数字 vs "条/页" vs "条" → 统一为"条/页" ✅ 阶段 D：upgrade/history 底部分页已统一
116. dashboard 统计卡片图标 7 处内联 color:#XXXXXX → CSS 变量或全局类 ✅ 阶段 D 已完成
117. upgrade/center.html 终端主题硬编码配色（#1a1a2e,#00ff88）→ 提取为全局 .terminal-theme 类或 CSS 变量 ✅ 阶段 D：base .nginx-v-output
118. badge bg-light text-dark border 稀有徽标样式 → 统一为 Bootstrap 标准 badge 或全局 .badge-outline 类 ✅ 阶段 E：base .badge-outline + 模板替换
119. _status_badge.html 与 _env_badge.html 两个 partial 均使用自定义 CSS 类而非 Bootstrap 标准类 → 统一为 Bootstrap badge bg-* 类 ✅ 已修复：两个 partial 改为 Bootstrap 标准类（bg-success/danger/secondary），清理附带重复 CSS
120. credentials/edit.html 密码输入块 L99-119 两个几乎重复的 input 块 → 简化为动态控制 toggle ✅ 阶段 D 已完成
121. users/group_create.html 与 group_edit.html .perm-matrix CSS 完全重复（各 76 行）→ 提取到全局 ✅ 已修复：base.html 新增 .perm-matrix 全局样式；group_create/edit 删除本地副本
122. users/team_list.html <style>+<script> 在 content block 内 → 移至 extra_js block ✅ 阶段 E 已完成
123. upgrade/task_log.html .nginx-v-output CSS 与 upgrade/center.html 重复定义 → 统一到全局 .terminal-theme 或 .code-font ✅ 阶段 D 已完成
124. upgrade/history.html tag-input 内联 style="min-height:32px" + p-1 类 → 统一为全局标准 ✅ 阶段 D 已完成
125. upgrade/history.html 进度条硬编码 width:60px → % 或 rem 响应式 ✅ 阶段 D：.table-inline-progress
126. nodes/list.html 节点详情弹窗 modal-lg + 自定义 800px max-width → 统一为标准 modal-lg（无自定义宽度） ✅ 阶段 D 已完成
```

Q43
```text
节点管理
    节点列表，点击主机名会有2个弹窗
        其一是“系统信息采集”弹窗
        其二是“xxx 详情”弹窗
        我认为“系统信息采集”可以删除，你觉得是否可以？
 ✅ 已修复：打开详情时系统信息/Nginx 检测改为详情弹窗内静默加载，去掉全屏进度遮罩
```

Q44
```text
配置管理
    配置版本差异对比，点击“返回版本历史”报错
    Page not found (404)
No ConfigNodeBinding matches the given query.
Request Method:	GET
Request URL:	http://127.0.0.1:8000/configs/2/versions/
Raised by:	apps.configs.views.BindingVersionListView
Using the URLconf defined in ngxops.urls, Django tried these URL patterns, in this order:

admin/
[name='index']
api/stats/ [name='stats_api']
login/ [name='login']
logout/ [name='logout']
profile/ [name='profile']
password/change/ [name='password_change']
users/
credentials/
nodes/
configs/ [name='list']
configs/ create/ [name='create']
configs/ <int:pk>/ [name='detail']
configs/ <int:pk>/edit/ [name='edit']
configs/ <int:pk>/delete/ [name='delete']
configs/ bindings/create/ [name='binding_create']
configs/ bindings/<int:pk>/ [name='binding_detail']
configs/ bindings/<int:pk>/edit/ [name='binding_edit']
configs/ bindings/<int:pk>/delete/ [name='binding_delete']
configs/ bindings/<int:pk>/restore/ [name='binding_restore']
configs/ bindings/<int:pk>/versions/ [name='binding_versions']
configs/ bindings/<int:pk>/versions/<int:version_id>/ [name='binding_version_detail']
configs/ bindings/<int:pk>/versions/<int:version_id>/restore/ [name='binding_version_restore']
configs/ bindings/<int:pk>/compare/ [name='binding_compare']
configs/ bindings/<int:pk>/compare/apply/ [name='binding_compare_apply']
configs/ api/by-nodes/ [name='api_by_nodes']
configs/ api/preview-glob/ [name='api_preview_glob']
configs/ api/update-preview/ [name='api_update_preview']
configs/ sync/ [name='sync_wizard']
configs/ sync/api/batch/ [name='sync_batch_api']
configs/ sync/api/single/ [name='sync_single_api']
configs/ sync/api/progress/ [name='sync_progress']
configs/ <int:pk>/update/ [name='update']
configs/ node/<int:pk>/delete/ [name='node_delete']
configs/ <int:pk>/versions/ [name='versions']
 ✅ 已修复：返回链接改为 configs:binding_versions + binding.id；配置标签详情页删除误用 config.id 的「版本历史」按钮
```

Q45
```text
发布中心
    点击发布，发布执行中弹窗点击完整日志，会自动跳转到任务中心。
    任务中心默认展示所有，能否展示当前批次详情？
 ✅ 已修复：progressOverlay「完整日志」跳转 /releases/history/?search=<批次号>，任务中心仅展示当前批次记录
```

Q46
```text
发布中心
    查询框输入查询标签搜索后，未展示配置详情，而是“加载绑定...”。
    当重新刷新浏览器后可正常展示。
 ✅ 已修复：loadNodes 恢复展开节点时，有 bindingCache 则 renderBindingRow，无缓存才 loadBindings（避免搜索重绘后卡在加载占位）
```

Q47
```
发布中心
    查询框输入配置名称条件标签后搜索，无反应
 ✅ 已修复：API 增加 remote_path 搜索；有搜索词时自动展开本页节点，renderBindingRow 按配置名/路径过滤展示
```

Q48
```text
发布中心
    新建配置标签绑定节点后发布。会提示
    [14:48:47] 正在测试 SSH 连接...
[14:48:47] SSH 连接测试通过 ✓
[14:48:47] 开始发布: baidu v3 → lsj
[14:48:47] 目标路径: /etc/nginx/conf.d/baidu.com
[14:48:47] 正在备份原配置...
[14:48:48] 备份失败: 备份失败: cp: cannot stat '/etc/nginx/conf.d/baidu.com': No such file or directory
    因为是新增，远程没有此配置
 ✅ 已修复：backup_remote_file 对不存在的远程文件跳过备份；首次发布失败回滚改为 rm 清理新文件
```

Q49
```text
发布历史
    回滚配置点击版本号自定义弹窗显示的“加载失败”
 ✅ 已修复：rollback.html 版本预览适配 VersionContentAPIView 扁平 JSON（与 Q17/发布中心一致）
```

Q50
```text
发布回滚配置
    发布历史配置回滚后，其对应的状态变成了 pending。
    但是我在发布中心里面看不到任务。
 ✅ 已修复：确认后立即异步执行；当前页弹出发布同款 progressOverlay；完整日志按批次打开任务中心
```

Q51
```text
发布历史
    回滚关联的状态是不是搞错了？
    已经发布失败就没必要回滚了。
    只有当发布成功后我理解才需要回滚。
    我是这样理解的，还是说不管发布结果如何都需要回滚？
    你认为如何？
```

Qxx
```text
配置备份
    能否以节点为粒度
    现在所有配置都在 /opt/app/mascloud/ansible/mngxops/ 下面
    当配置重名是会覆盖
    /opt/app/mascloud/ansible/mngxops/< hostname >
    这样是否可以？
``