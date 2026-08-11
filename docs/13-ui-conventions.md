# 13 · UI 与交互约定（已落地）

本文档沉淀 Q41/Q42/Q66/Q69/Q73 等已落地的实现约束，作为前端改动的需求基线。**不引入新设计语言**；以 [`templates/base.html`](../templates/base.html) 为准。

技术栈：Bootstrap 5.3 + Bootstrap Icons 1.11（CDN）。全局样式内联于 `base.html`；登录页独立模板。

---

## 1. 设计令牌（`:root`）

定义于 `base.html`。新样式优先 `var(--*)`；禁止页面硬编码替代主题色（Q41）。

### 1.1 颜色

| 变量 | 值 | 用途 |
|------|-----|------|
| `--primary` | `#667eea` | 主色、强调、tag 徽标；同时映射 `--bs-primary` |
| `--primary-rgb` | `102, 126, 234` | 与 Bootstrap `--bs-primary-rgb` 对齐 |
| `--primary-soft` | `rgba(102, 126, 234, 0.08)` | 上传区/浅强调底 |
| `--primary-gradient` | `linear-gradient(180deg, #667eea 0%, #764ba2 100%)` | 侧栏背景（竖直） |
| `--success` | `#28a745` | 成功 / 在线 |
| `--warning` | `#ffc107` | 警告 / 进行中 |
| `--danger` | `#dc3545` | 危险 / 失败 / 必填 `*` |
| `--info` | `#0dcaf0` | 信息 |
| `--dark` | `#212529` | 正文强调色 |
| `--gray-100` | `#f8f9fa` | 页面底、表头、分区 header |
| `--gray-200` | `#e9ecef` | 卡片底边、细分割 |
| `--gray-300` | `#dee2e6` | 边框、outline badge |
| `--gray-400` | `#ced4da` | 空态图标 |
| `--gray-500` | `#adb5bd` | placeholder |
| `--gray-600` | `#6c757d` | 次要文案、默认统计卡 |
| `--gray-700` | `#495057` | 更深灰（预留） |

### 1.2 字号 / 圆角 / 过渡 / 布局

| 变量 | 值 | 用途 |
|------|-----|------|
| `--fs-xs` | `0.72rem` | 节点组 badge 等 |
| `--fs-sm` | `0.78rem` | 表格内 badge、进度步骤 |
| `--fs-base` | `0.82rem` | 业务表格与正文默认 |
| `--fs-md` | `0.88rem` | 进度树节点头等 |
| `--fs-lg` | `1rem` | 略大正文 |
| `--fs-xl` | `1.1rem` | 侧栏品牌标题量级 |
| `--br-sm` | `4px` | 小徽标、tag |
| `--br-md` | `6px` | 按钮、导航项、卡片圆角默认 |
| `--br-lg` | `12px` | 进度弹窗、上传区 |
| `--transition-fast` | `0.15s ease` | 点击反馈 |
| `--transition-normal` | `0.3s ease` | 侧栏折叠、hover |
| `--sidebar-width` | `220px` | 展开侧栏 |
| `--sidebar-width-collapsed` | `64px` | 折叠侧栏 |
| `--font-mono` | Cascadia Code, Consolas, Courier New, monospace | 等宽 |

工具类：`.fs-xs` / `.fs-sm` / `.fs-base` / `.fs-md` / `.fs-lg`；`.code-font`。

---

## 2. 背景与表面

当前**无全站背景图/纹理**；氛围靠浅灰底 + 侧栏渐变 + 卡片阴影。

| 表面 | 规则 | 说明 |
|------|------|------|
| 业务页 `body` | `background-color: var(--gray-100)` | 纯色浅灰 |
| 侧栏 `.sidebar` | `background: var(--primary-gradient)`；白字 | 无背景图 |
| 顶栏 `.navbar` | 白底 + `box-shadow: 0 2px 10px rgba(0,0,0,.1)` | |
| `.card` | `border: none`；`box-shadow: 0 2px 10px rgba(0,0,0,.05)` | |
| `.card-header` | 白底；底边 `2px solid var(--gray-200)` | |
| 表头 `th` | `background-color: var(--gray-100)`；字重 `600` | |
| 登录页 `body` | 斜向渐变 `135deg, #667eea → #764ba2` | 见 [`login.html`](../apps/accounts/templates/accounts/login.html) |
| 登录卡 `.login-card` | 白底；`box-shadow: 0 10px 40px rgba(0,0,0,.1)` | 独立于 app-shell |
| 进度遮罩 | `rgba(0,0,0,.45)` + 白面板 `.async-progress-dialog` | `#asyncProgressOverlay` |
| 终端/编译输出 | `.terminal-theme` / `.nginx-v-output` 深色底 | 见 §8 例外 |

阴影惯例：顶栏/卡片约 `0 2px 10px`；弹窗/Toast 更深（`0 8px–16px`）。

---

## 3. 全局壳与布局

- 布局：`base.html` 中 `.app-shell`（flex）；侧栏 `.sidebar` + 主区 `.main-area`。
- 侧栏可折叠 `220px` / `64px`；`body.sidebar-collapsed` 切换；状态存 `localStorage`（Q73）。
- 折叠后子菜单浮层展示；浮层背景见 §8（`#5a67d8`）。折叠态 `.sidebar { overflow: visible }`，避免浮层被裁切。
- 子菜单触发器用 `data-submenu-toggle`（勿用 `data-bs-toggle`，避免与 Bootstrap 冲突）。
- 导航项按 `has_perm_code` 显隐；无权限不展示入口。
- 全局全屏进度：`#asyncProgressOverlay`（Q39）；完成可跳转任务详情「完整日志」。

---

## 4. 字体与字重

| 规则 | 说明 |
|------|------|
| 字号 | 业务默认 `--fs-base`；用 rem 变量，避免页面随意 `px` 字号 |
| 表头 | `font-weight: 600`；`white-space: nowrap` |
| `.node-identity` | `--fs-base` / `400`；nowrap（Q69） |
| 全局粗体 | `strong` / `b` / `.fw-bold` 降为 `500`；`.stat-number` 等可自行覆盖为 `700` |
| 表行 | 避免滥用 `strong` |
| 等宽 | `.code-font`；代码预览 `.code-block-preview` 用 `--fs-base` |
| 图标 | 优先 Bootstrap Icons；避免 Emoji（Q42） |

---

## 5. 公共组件

| 组件 | 类名 / 入口 | 视觉与行为要点 |
|------|-------------|----------------|
| 数据表 | `.data-table` | `table-layout: fixed`；单元格 `--fs-base`；首末列左右留白 `1rem`（Q66） |
| 节点展示 | `.node-info-cell` / `.node-identity` | 主机名+组 badge；发布中心/配置列表对齐（Q15/Q32） |
| 空态 | `.empty-state` | 居中；图标 `3rem` + `--gray-400`；文案 `--gray-600` |
| 分页 | `templates/includes/pagination.html` | 首页/末页 + 每页条数 |
| 主操作按钮 | `btn-sm` | 列表「新增」等（Q78）；圆角 `--br-md` |
| 按钮反馈 | `.btn:active` | `scale(0.97)` / `brightness(0.9)` |
| 表单分区 | `.form-section` | 左边 `3px` 色条 + header `--gray-100`（Q68） |
| 表单分区变体 | `--basic` / `--status` / `--roles` / `--teams` / `--perms` | 色条分别为 primary / warning / info / info / success |
| 必填 | `label .required` | 色 `--danger` |
| 开关 | Bootstrap `form-switch` | 统一 |
| 关联多选 | 弹窗 + chips | 用户组/角色/节点等（Q68/Q83） |
| 权限矩阵 | `.perm-matrix` | 全局；单元格可点切换勾选（Q117） |
| 确认/警告 | `showConfirm` / `showAlert` | 收敛；支持排队（升级中心 Q65）；尺寸见 §5.1 |
| 弹窗表 | `.modal-picker-table` + `bindModalTableRowToggle` | 行点击勾选；滚动区 `max-height: 60vh`；picker 宜 `modal-lg` + `modal-dialog-scrollable` |
| 弹窗 ID | 模块前缀 | 避免冲突（Q42） |
| 弹窗页脚 | `.modal-footer .btn` | 统一小号（等同 `btn-sm`，Q149） |
| 标签搜索 | `.tag-input-wrapper` | 白底；选中 tag 用 `--primary`；多标签 AND（Q23） |
| Toast | `.toast-container-global` | 右上；success/danger/warning/info 对应语义色 |
| 进度遮罩 | `#asyncProgressOverlay` | 同步/发布/回滚/节点测试等共用；可传 `batchNumber` / `showSkipFailed` / `onClose`（Q149） |
| 状态徽标 | `.badge-status-*` | 见 §6 |
| 描边徽标 | `.badge-outline` | 灰底 + `--gray-300` 边 |
| 上传区 | `.upload-dropzone` | 虚线 `--primary`；浅紫底 `var(--primary-soft)` / `rgba(102,126,234,.08)` |
| 统计卡 | `.dashboard-stat-card` + `.stat-card-*` | 白底；左边 `4px` 语义色；可点/只读分叉 |

进度树节点头底色：成功 `#d4edda`、失败 `#f8d7da`、进行中 `#fff3cd`。

### 5.1 `showConfirm` 尺寸矩阵

| 尺寸 | 适用 |
|------|------|
| `sm` | 纯文案短确认（默认，且 `asHtml=false` 时） |
| `md` | HTML / 多行说明（`asHtml=true` 且未显式传 size 时自动） |
| `lg` | 含 `<table>` 的明细确认（`asHtml=true` 且正文含 table 时自动，或显式传入） |
| `xl` | 双栏代码 / 大型预览类（显式传入；内容预览类 Bootstrap Modal 亦用 `xl`） |

业务页勿再使用浏览器原生 `alert()` / `confirm()`；失败提示统一 `showAlert`。

---

## 6. 状态与环境色

### 6.1 全局徽标（`base.html`）

| 类 | 背景 | 字色 |
|----|------|------|
| `.badge-status-success` | `var(--success)` | `#fff` |
| `.badge-status-danger` | `var(--danger)` | `#fff` |
| `.badge-status-warning` | `var(--warning)` | `var(--dark)` |
| `.badge-status-info` | `var(--info)` | `#fff` |
| `.badge-status-secondary` | `var(--gray-600)` | `#fff` |

### 6.2 节点环境 / 在线状态（子模板）

- 环境：[`_env_badge.html`](../apps/nodes/templates/nodes/_env_badge.html) — `bg-danger` 生产 / `bg-info` 测试 / `bg-secondary` 开发。
- 状态：[`_status_badge.html`](../apps/nodes/templates/nodes/_status_badge.html) — `bg-success` 在线 / `bg-danger` 离线 / `bg-secondary` 未知。

### 6.3 仪表盘统计卡左边框

| 类 | 边框色 |
|----|--------|
| `.stat-card-primary` | `--primary` |
| `.stat-card-online` | `--success` |
| `.stat-card-offline` | `--danger` |
| `.stat-card-warning` | `--warning` |
| `.stat-card-info` | `--info` |
| `.stat-card-default` | `--gray-600` |

### 6.4 配置同步状态过滤标签

配置列表 `.config-status-active` 默认灰底 `#6c757d`（「全部」可见，Q99）；`pending` 等状态色对齐 `--primary` / 语义色（Q149）。`conflict` / `syncing` **过滤入口已下线**（Q84/Q85）；行内兜底 badge 仍可显示脏数据。

---

## 7. 页面级特殊约定（摘录）

| 页面 | 约定 |
|------|------|
| 发布中心 | 两步选择；节点行点击展开绑定；路径 Modal 预览 `modal-xl`；状态过滤栏；进度用全局 `#asyncProgressOverlay` |
| 发布历史 | 按批次分页；三级勾选联动；批量回滚明细 `modal-lg` |
| 任务中心 | 摘要窄列省略（全文看详情）；无横向滚动；筛选 onchange；操作 icon-only；详情结果树失败置顶；批次超链新窗口 |
| 配置列表 | 返回保持节点展开；未绑定标签区；状态过滤标签见 §6.4 |
| 设置页 | 左导航右 side-by-side；未保存离开确认；`?group=` + localStorage |
| 升级中心 | 四步向导；再进入从第 1 步空白开始；sessionStorage 仅续看进行中批次（Q113） |
| 节点详情 | 静默采集系统信息，不用全屏遮罩（Q43） |
| 登录页 | 全屏渐变底 + 居中白卡；不走 app-shell |

操作列：icon-only 防换行（用户列表等，Q68）；已删除节点在发布历史禁用回滚勾选（Q76）。

---

## 8. 已知例外（硬编码）

以下为已落地特例，**不当作待办**；改全局主题时勿静默「统一掉」而未回归。

| 位置 | 内容 |
|------|------|
| 侧栏折叠浮层 | 子菜单背景 `#5a67d8`（非 CSS 变量） |
| 登录页 | 局部 `:root` 子集；渐变方向 `135deg`（侧栏为 `180deg`） |
| 发布中心页内 | `.env-badge-*` / `.sync-badge-*` / `.status-badge-*` 本地十六进制 |
| 配置列表页内 | `.config-status-active.*` 部分状态色（如 marked `#e83e8c`）；pending 已改 `--primary` |
| 终端主题 | `.nginx-v-output` / `.terminal-theme`：`#1a1a2e` 底、`#00ff88` 字；高亮/批注色为固定十六进制 |
| 进度树 / 步骤条 | 部分背景直接用 `#f8f9fa`、`#d4edda` 等 Bootstrap 浅色，未全部走变量 |
| tag 聚焦 | `.tag-badge-focus` 用 `#fff3cd` / `#ffe69c` |

主色：`--primary` / `--bs-primary` 均为 `#667eea`（Q149），CTA 与侧栏品牌色一致。

---

## 9. 改动原则（给 Agent）

1. **复用**：优先 `base.html` 变量与上表公共组件；列表用 `.data-table`，确认用 `showConfirm`/`showAlert`。
2. **不平行造轮**：不新增第二套主题色、不引入全站背景图、不抽与 `base.html` 冲突的全局 CSS 文件（除非整体迁移并同步本文档）。
3. **特例登记**：页面必须硬编码时，同步更新本节「已知例外」，并保持与语义色含义一致。
