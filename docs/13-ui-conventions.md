# 13 · UI 与交互约定（已落地）

本文档沉淀 Q41/Q42/Q66/Q69/Q73 等已落地的实现约束，作为前端改动的需求基线。不引入新设计语言。

## 1. 全局壳

- 布局：[`templates/base.html`](../templates/base.html) app-shell；侧栏可折叠 220px/64px，状态 `localStorage`（Q73）。
- 折叠后子菜单浮层展示。
- 导航项按 `has_perm_code` 显隐。
- 全局全屏进度：`#asyncProgressOverlay`（Q39）；完成可跳转任务详情「完整日志」。

## 2. 视觉与字体

- CSS 变量驱动主题色；禁止页面硬编码主题色值替代变量（Q41）。
- 字号 rem；表头字重 600；`.node-identity` 固定 base/400（Q69）。
- 表行避免滥用 `strong`。
- 等宽：`.code-font`。
- 圆角/过渡两档；状态/环境用统一 badge 组件。

## 3. 表格与列表

- 业务列表统一 `.data-table`；首末列留白（Q66）。
- 节点展示：`.node-info-cell` / `.node-identity` + 组 badge，对齐发布中心与配置列表（Q15/Q32）。
- 分页：共用 `templates/includes/pagination.html`（首页/末页 + 每页条数）。
- 空态：统一 empty-state 组件。

## 4. 按钮与表单

- 列表「新增」等主操作：`btn-sm`（Q78）。
- 表单分区：`.form-section` 色条卡片（Q68）。
- 必填标记 `*`；`form-switch` 统一。
- 关联多选：弹窗 + chips（用户组/角色/节点等，Q68/Q83）。

## 5. 弹窗与确认

- 确认/警告：`showConfirm` / `showAlert` 收敛，支持排队（升级中心 Q65）。
- 弹窗内表格：picker 表 + `bindModalTableRowToggle` 行点击勾选（Q66）。
- 弹窗 ID 使用模块前缀，避免冲突（Q42 二次核实）。

## 6. 标签搜索

- 全局 tag-input；多标签 AND 过滤（发布确认清单 Q23 等）。
- 节点选择弹窗支持主机名/IP/节点组名搜索。

## 7. 状态展示

- 配置同步状态 badge：`config_filters` / 列表过滤标签（含 UI 中存在但后端未写入的 `syncing`/`conflict`，见 Q84/Q85）。
- 环境 badge：`_env_badge.html`；节点状态：`_status_badge.html`。

## 8. 反馈

- Toast 轻提示；危险操作走确认框。
- 图标优先 Bootstrap Icons，避免 Emoji（Q42）。

## 9. 权限与操作列

- 无权限不展示入口；操作列 icon-only 防换行（用户列表 Q68）。
- 已删除节点在发布历史禁用回滚勾选（Q76）。

## 10. 页面级特殊约定（摘录）

| 页面 | 约定 |
|------|------|
| 发布中心 | 两步选择；节点行点击展开绑定；路径 Modal 预览；状态过滤栏 |
| 发布历史 | 按批次分页；三级勾选联动；批量回滚明细 modal-lg |
| 任务中心 | 摘要定宽；详情结果树失败置顶；批次超链新窗口 |
| 配置列表 | 返回保持节点展开；未绑定标签区 |
| 设置页 | 左导航右 side-by-side；未保存离开确认；`?group=` + localStorage |
| 升级中心 | 四步向导；sessionStorage 按 settingsBaseline 智能恢复（Q81） |
| 节点详情 | 静默采集系统信息，不用全屏遮罩（Q43） |
