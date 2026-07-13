# MngxOps 需求文档 - 08 UI 样式与交互规范

> **文档类型**: UI/UX 设计规范  
> **适用范围**: MngxOps 全平台所有页面  
> **基于框架**: Bootstrap 5.3 + Bootstrap Icons 1.11

---

## 1. 概述

本文档定义 MngxOps 平台通用的 UI 样式规范和交互模式，确保所有页面的视觉一致性和用户体验统一性。所有新页面的开发、旧页面的重构均需遵循此规范。

---

## 2. 设计系统

### 2.1 色彩体系

| 用途 | 色值 | 说明 |
|------|------|------|
| 主题色（Primary） | `#667eea` → `#764ba2` | 侧边栏渐变、主按钮、链接 |
| 成功色（Success） | `#28a745` / `#d4edda` | 在线状态、成功操作、成功提示 |
| 危险色（Danger） | `#dc3545` / `#f8d7da` | 离线状态、失败操作、删除按钮 |
| 警告色（Warning） | `#ffc107` / `#fff3cd` | 警告提示、待处理状态 |
| 信息色（Info） | `#17a2b8` / `#d1ecf1` | 一般提示、进行中状态 |
| 中性色（Muted） | `#6c757d` / `#e2e3e5` | 禁用/未知状态、辅助文字 |
| 背景色 | `#f8f9fa` | 页面主背景 |
| 卡片背景 | `#ffffff` | 卡片/面板/弹窗背景 |
| 表格表头 | `#f8f9fa` | 表格头部背景 |
| 边框色 | `#dee2e6` / `#f0f0f0` | 卡片边框、表格边框 |
| 文字色 | `#212529` | 正文颜色 |
| 辅助文字 | `#6c757d` | 说明文字、提示文字 |

### 2.2 间距系统

| 级别 | 值 | 用途 |
|------|-----|------|
| xs | 4px | 图标与文字间距、紧凑元素间距 |
| sm | 8px | 表单元素间距、徽标内间距 |
| md | 16px | 卡片内边距、表格单元格内边距 |
| lg | 24px | 页面内容区边距、区块间距 |
| xl | 32px | 大区块间距 |
| 2xl | 48px | 页面标题与内容间距 |

### 2.3 圆角系统

| 级别 | 值 | 用途 |
|------|-----|------|
| sm | 4px | 徽标、小标签 |
| md | 6px | 按钮、输入框、表格 |
| lg | 8px | 卡片、面板 |
| xl | 10px | 大型统计卡片 |
| pill | 50px | 状态徽标、标签 |

### 2.4 阴影系统

| 级别 | 值 | 用途 |
|------|-----|------|
| 基础阴影 | `0 2px 10px rgba(0,0,0,0.05)` | 卡片 |
| 悬停阴影 | `0 6px 20px rgba(0,0,0,0.1)` | 卡片悬停 |
| 顶部阴影 | `0 -2px 10px rgba(0,0,0,0.05)` | 底部固定栏 |
| 弹窗阴影 | `0 10px 40px rgba(0,0,0,0.15)` | 弹窗/下拉 |

### 2.5 字体系统

| 级别 | 大小 | 行高 | 用途 |
|------|------|------|------|
| h1 | 1.75rem (28px) | 1.3 | 页面标题 |
| h2 | 1.5rem (24px) | 1.3 | 区块标题 |
| h3 | 1.25rem (20px) | 1.4 | 卡片标题 |
| h4 | 1.1rem (17.6px) | 1.4 | 子区块标题 |
| body | 0.95rem (15.2px) | 1.5 | 正文 |
| small | 0.8rem (12.8px) | 1.4 | 辅助说明 |
| code | 0.85rem (13.6px) | 1.5 | 代码/配置内容 |

**字体栈**: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans SC", sans-serif`

---

## 3. 通用组件规范

### 3.1 侧边栏（Sidebar）

```
┌──────────────────────┐
│                      │
│  🛡 MngxOps          │  ← 品牌区
│  多节点Nginx管理平台  │
│                      │
│  ─────────────────── │
│                      │
│  🏠 首页             │  ← 导航区
│                      │
│  ── 功能模块 ──      │  ← 分组标题
│  🖥 节点管理         │
│  🔑 凭证管理         │
│  📁 节点组管理       │
│  📝 配置管理         │
│  🚀 发布中心         │
│  📋 任务中心         │
│                      │
│  ── 系统管理 ──      │
│  👥 用户管理         │
│  👤 角色管理         │
│  📦 用户组管理       │
│  📜 操作日志         │
│  🔐 登录日志         │
│                      │
└──────────────────────┘
```

**规范**:
- 宽度：`col-md-2`（约 16.67% 视口宽度）
- 背景：渐变 `linear-gradient(180deg, #667eea 0%, #764ba2 100%)`
- 品牌区：上下内边距 `1.5rem`，底部边框分隔
- 导航项：内边距 `12px 20px`，外边距 `4px 10px`，圆角 `8px`
- 导航项默认：`color: rgba(255,255,255,0.8)`，透明背景
- 导航项悬停/激活：`color: white`，`background: rgba(255,255,255,0.2)`
- 分组标题：`font-size: 0.8rem`，`opacity: 0.5`，大写或小字
- 图标：左侧 `margin-right: 8px`
- 禁用项：`opacity: 0.4`，`cursor: not-allowed`
- 过渡动画：`transition: all 0.3s`

### 3.2 顶部导航栏（TopBar）

```
┌──────────────────────────────────────────────────────┐
│  ☰ 节点管理                    👤 admin ▼           │
└──────────────────────────────────────────────────────┘
```

**规范**:
- 背景：白色 `#ffffff`
- 阴影：`0 2px 10px rgba(0,0,0,0.1)`
- 高度：约 56px
- 左侧：面包屑/页面标题（`font-size: 1.25rem`，`font-weight: 600`）
- 右侧：用户下拉菜单
  - 图标：`bi-person-circle`
  - 下拉项：个人中心、分割线、退出登录（红色文字）
- 下拉菜单定位：`dropdown-menu-end`

### 3.3 卡片（Card）

```css
.card {
  border: none;
  box-shadow: 0 2px 10px rgba(0,0,0,0.05);
  border-radius: 8px;
  margin-bottom: 20px;
}
.card-header {
  background: white;
  border-bottom: 2px solid #f0f0f0;
  font-weight: 600;
  padding: 16px 20px;
}
.card-body {
  padding: 20px;
}
```

### 3.4 按钮（Button）

| 类型 | 样式 | 用途 |
|------|------|------|
| 主按钮 | `.btn-primary` (主题色渐变) | 主要操作（保存、提交、确认） |
| 次按钮 | `.btn-outline-secondary` | 次要操作（取消、返回） |
| 成功按钮 | `.btn-success` | 正向操作（启用、通过） |
| 危险按钮 | `.btn-danger` / `.btn-outline-danger` | 破坏性操作（删除、禁用） |
| 信息按钮 | `.btn-info` / `.btn-outline-info` | 信息类操作（详情、查看） |
| 小按钮 | `.btn-sm` | 表格内操作、工具栏按钮 |

**通用规范**:
- 圆角：`6px`
- 内边距：`8px 16px`（默认）/ `4px 10px`（sm）
- 字体权重：`500`
- 添加 `transition` 过渡效果
- 危险按钮（删除）使用 `.btn-outline-danger` 避免过于醒目

### 3.5 表格（Table）

```css
.table {
  margin-bottom: 0;
  vertical-align: middle;
}
.table th {
  background-color: #f8f9fa;
  font-weight: 600;
  font-size: 0.85rem;
  color: #495057;
  border-bottom: 2px solid #dee2e6;
  white-space: nowrap;
}
.table td {
  padding: 12px 14px;
  font-size: 0.9rem;
  border-bottom: 1px solid #f0f0f0;
}
.table tr:hover {
  background-color: #f8f9ff;
}
```

**规范**:
- 表格行悬停：`background-color: #f8f9ff`
- 垂直对齐：`vertical-align: middle`
- 表头：灰色背景，白色，加粗
- 操作列：右对齐，使用小按钮组
- 空数据：显示居中提示 "暂无数据"
- 加载状态：显示骨架屏或加载指示器

### 3.6 表单（Form）

```css
.form-label {
  font-weight: 500;
  font-size: 0.9rem;
  color: #495057;
  margin-bottom: 6px;
}
.form-label.required::after {
  content: " *";
  color: #dc3545;
}
.form-control {
  border-radius: 6px;
  border: 1px solid #dee2e6;
  padding: 8px 12px;
  font-size: 0.95rem;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.form-control:focus {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102,126,234,0.15);
}
.form-text {
  font-size: 0.8rem;
  color: #6c757d;
  margin-top: 4px;
}
.is-invalid {
  border-color: #dc3545;
}
.invalid-feedback {
  font-size: 0.8rem;
  color: #dc3545;
}
```

**规范**:
- 标签：必填字段加红色 `*` 标记
- 输入框：统一高度 `38px`，聚焦时蓝色边框 `#667eea`
- 帮助文本：在输入框下方，灰色小字
- 错误信息：红色小字，紧跟对应字段
- 表单分组：使用 fieldset 分组，加分隔线
- 按钮区域：表单底部左对齐（保存按钮在左）
- 所有表单页使用 600px 最大宽度居中布局

### 3.7 分页器（Pagination）

```
共 128 条  [« 上一页]  [1] [2] [3] ... [13]  [下一页 »]
```

**规范**:
- 显示"共 N 条"总数信息
- 上一页/下一页按钮
- 页码按钮，当前页高亮
- 超过 7 页时显示省略号 `...`
- 响应式：小屏幕缩小按钮间距
- 每页条数选择器：`10/20/50/100`

### 3.8 搜索筛选区

```
┌─ 搜索筛选区 ────────────────────────────────────────┐
│ ┌──────────────┐ ┌────────┐ ┌────────┐ ┌────────┐ │
│ │ 🔍 搜索...    │ │ 筛选1 ▼│ │ 筛选2 ▼│ │ 每页 ▼ │ │
│ └──────────────┘ └────────┘ └────────┘ └────────┘ │
└──────────────────────────────────────────────────────┘
```

**规范**:
- 所有列表页顶部统一的搜索筛选栏
- 采用 `d-flex gap-2 align-items-center flex-wrap` 布局
- 搜索输入框带放大镜图标前缀
- 筛选下拉框宽度自适应内容
- "每页条数"选择器在最右侧
- 搜索支持回车触发和输入防抖（300ms）

### 3.9 弹窗（Modal）— 自定义实现

```css
.mngxops-modal-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1050;
  animation: fadeIn 0.2s;
}
.mngxops-modal {
  background: white;
  border-radius: 10px;
  box-shadow: 0 10px 40px rgba(0,0,0,0.15);
  max-width: 600px;
  width: 90vw;
  max-height: 85vh;
  overflow-y: auto;
  animation: slideUp 0.3s;
}
.mngxops-modal-header {
  padding: 16px 20px;
  border-bottom: 1px solid #f0f0f0;
  font-weight: 600;
  font-size: 1.1rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mngxops-modal-body {
  padding: 20px;
}
.mngxops-modal-footer {
  padding: 12px 20px;
  border-top: 1px solid #f0f0f0;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
```

**规范**:
- 替代浏览器原生 `confirm()` / `alert()`，统一风格
- 弹窗自适应宽度：max-width 600px（桌面）/ 90vw（移动端）
- 包含标题栏（关闭按钮）、内容区、操作按钮区
- 关闭方式：点击 × 按钮、点击遮罩层、按 ESC 键
- 入场动画：遮罩淡入 + 弹窗上滑
- 确认弹窗：标题 + 描述文字 + [取消] + [确认] 按钮
- 信息弹窗：标题 + 内容 + [关闭] 按钮

### 3.10 Toast 消息提示

```css
.mngxops-toast-container {
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 1060;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mngxops-toast {
  background: white;
  border-radius: 8px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.12);
  padding: 12px 20px;
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 280px;
  max-width: 400px;
  animation: slideInRight 0.3s, fadeOut 0.3s 3s forwards;
  border-left: 4px solid;
}
.mngxops-toast.success { border-color: #28a745; }
.mngxops-toast.error   { border-color: #dc3545; }
.mngxops-toast.warning { border-color: #ffc107; }
.mngxops-toast.info    { border-color: #17a2b8; }
```

**规范**:
- 固定定位：右上角
- 自动消失：3 秒后淡出
- 类型：success（绿色）/ error（红色）/ warning（黄色）/ info（蓝色）
- 带左侧彩色边框和对应图标
- 支持多条同时显示（垂直堆叠）
- 动画：从右侧滑入 → 停留 → 淡出

### 3.11 徽标（Badge）

```css
.badge-status-online  { background: #d4edda; color: #155724; }
.badge-status-offline { background: #f8d7da; color: #721c24; }
.badge-status-unknown { background: #e2e3e5; color: #383d41; }
.badge-env-prod       { background: #f8d7da; color: #dc3545; }
.badge-env-test       { background: #d1ecf1; color: #0c5460; }
.badge-env-dev        { background: #e2e3e5; color: #6c757d; }
```

### 3.12 下拉菜单（Dropdown）

```css
.mngxops-dropdown-menu {
  min-width: 140px;
  border: 1px solid #e9ecef;
  border-radius: 8px;
  box-shadow: 0 6px 20px rgba(0,0,0,0.1);
  padding: 6px 0;
}
.mngxops-dropdown-item {
  padding: 8px 16px;
  font-size: 0.9rem;
  display: flex;
  align-items: center;
  gap: 8px;
  transition: background 0.15s;
}
.mngxops-dropdown-item:hover {
  background: #f0f3ff;
}
.mngxops-dropdown-item.danger {
  color: #dc3545;
}
.mngxops-dropdown-item.danger:hover {
  background: #fff5f5;
}
```

---

## 4. 动画规范

| 动画 | 时长 | 缓动 | 用途 |
|------|------|------|------|
| 卡片悬停上浮 | 0.2s | ease | 统计卡片 hover |
| 弹窗遮罩淡入 | 0.2s | linear | 弹窗打开 |
| 弹窗上滑 | 0.3s | ease-out | 弹窗打开 |
| Toast 滑入 | 0.3s | ease-out | Toast 出现 |
| Toast 淡出 | 0.3s | ease-in | Toast 消失 |
| 侧边栏过渡 | 0.3s | ease | 侧边栏 hover |
| 进度条 | 0.5s | ease | 进度条变化 |
| 旋转加载 | 1.5s 循环 | linear | 加载状态 |

**CSS 关键帧**:
```css
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes fadeOut { from { opacity: 1; } to { opacity: 0; } }
@keyframes slideUp { from { transform: translateY(20px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
@keyframes slideInRight { from { transform: translateX(100%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
```

---

## 5. 响应式规范

| 断点 | 宽度 | 调整策略 |
|------|------|---------|
| 超大屏 (xxl) | ≥1400px | 全布局，4列统计卡片 |
| 大屏 (xl) | ≥1200px | 全布局，4列统计卡片 |
| 中屏 (lg) | ≥992px | 全布局，3列统计卡片 |
| 小屏 (md) | ≥768px | 侧边栏折叠为汉堡菜单，2列统计卡片 |
| 超小屏 (sm) | ≥576px | 表格横向滚动，1列统计卡片 |
| 手机 (xs) | <576px | 表格横向滚动，操作按钮换行，弹窗全屏 |

**核心响应式规则**:
- 侧边栏：<768px 时隐藏，通过顶部汉堡按钮展开/收起
- 表格：<768px 时添加 `table-responsive` 容器实现横向滚动
- 搜索筛选区：小屏幕时输入框和下拉框换行
- 弹窗：<576px 时 width: 100vw，height: 100vh，圆角 0
- 操作按钮：小屏幕时使用图标按钮替代文字按钮

---

## 6. 交互模式规范

### 6.1 加载状态

| 场景 | 表现 |
|------|------|
| 页面加载 | 骨架屏（灰色占位块 + 脉冲动画） |
| 按钮加载 | 按钮禁用 + 旋转图标 + "处理中..."文字 |
| 表格加载 | 遮罩层 + 居中旋转加载图标 |
| 异步查询 | 输入框右侧旋转图标 + 防抖 300ms |
| 任务进度 | 进度条 + 百分比 + 当前状态文字 |

### 6.2 空状态

```
┌────────────────────────────────────┐
│                                    │
│            📭                      │
│        暂无数据                     │
│    点击下方按钮开始添加             │
│                                    │
│       [ + 添加第一个 ]             │
│                                    │
└────────────────────────────────────┘
```

**规范**:
- 居中显示
- 灰色图标 + 说明文字 + 引导操作按钮
- 无权限时显示"暂无数据或无权限查看"

### 6.3 确认操作

所有不可逆操作必须弹出自定义确认弹窗（非 `confirm()`）：
- 删除节点/凭证/配置/用户
- 批量操作（批量锁定/批量删除）
- 发布配置到生产环境
- 回滚配置

确认弹窗内容：
- ⚠️ 警告图标
- 明确的操作描述
- 受影响的数据列表（如"将删除以下 3 个节点：web01, web02, web03"）
- [取消] + [确认删除/确认操作] 按钮（危险按钮红色）

### 6.4 错误处理

| 场景 | 处理方式 |
|------|---------|
| 网络错误 | Toast 提示"网络连接失败，请检查网络" |
| 权限不足 | Toast 提示"无权限执行该操作" |
| SSH 连接失败 | 弹窗显示具体错误信息 + 操作建议 |
| 表单验证失败 | 字段下方红色提示文字 |
| 500 服务器错误 | 页面显示友好的错误页 + 返回首页按钮 |

### 6.5 操作反馈

| 操作 | 反馈方式 |
|------|---------|
| 创建/编辑成功 | Toast 绿色提示 + 跳转到列表页 |
| 删除成功 | Toast 绿色提示 + 刷新列表 |
| 异步任务创建 | Toast 蓝色提示 + 跳转链接 |
| 远程操作失败 | Toast 红色提示 + 无法关闭（需手动关闭） |

---

## 7. JavaScript 工具函数规范

### 7.1 CSRF Token

```javascript
// 所有 Ajax 请求自动携带 CSRF Token
const csrftoken = document.querySelector('[name=csrfmiddlewaretoken]').value;

function csrfSafeMethod(method) {
  return (/^(GET|HEAD|OPTIONS|TRACE)$/.test(method));
}

$.ajaxSetup({
  beforeSend: function(xhr, settings) {
    if (!csrfSafeMethod(settings.type) && !this.crossDomain) {
      xhr.setRequestHeader("X-CSRFToken", csrftoken);
    }
  }
});
```

### 7.2 自定义弹窗 API

```javascript
// 确认弹窗
MngxOpsModal.confirm({
  title: '确认删除',
  message: '确定要删除节点 web01 吗？此操作不可撤销。',
  confirmText: '确认删除',
  confirmClass: 'btn-danger',
  onConfirm: function() { /* 执行删除 */ }
});

// 信息弹窗
MngxOpsModal.alert({
  title: '操作成功',
  message: '节点 web01 已成功创建',
  onClose: function() { /* 关闭回调 */ }
});

// 自定义内容弹窗
MngxOpsModal.open({
  title: '节点详情',
  content: '<div>自定义HTML</div>',
  size: 'lg',  // sm / md / lg / xl
  onClose: function() { /* 关闭回调 */ }
});
```

### 7.3 Toast API

```javascript
MngxOpsToast.success('操作成功');
MngxOpsToast.error('操作失败：连接超时');
MngxOpsToast.warning('部分节点未响应');
MngxOpsToast.info('任务已创建，正在后台执行');
```

### 7.4 相对时间格式化

```javascript
function relativeTime(dateString) {
  const now = new Date();
  const date = new Date(dateString);
  const diff = Math.floor((now - date) / 1000); // 秒

  if (diff < 60) return '刚刚';
  if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
  if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
  if (diff < 2592000) return Math.floor(diff / 86400) + '天前';
  return date.toLocaleDateString('zh-CN');
}
```

---

## 8. 图标使用规范

### 8.1 全局图标映射

| 模块 | 图标 | Bootstrap Icon |
|------|------|---------------|
| 首页/仪表盘 | 🏠 | `bi-house-door` |
| 节点管理 | 🖥 | `bi-server` |
| 凭证管理 | 🔑 | `bi-key` |
| 节点组 | 📁 | `bi-collection` |
| 配置管理 | 📝 | `bi-file-earmark-code` |
| 发布中心 | 🚀 | `bi-rocket-takeoff` |
| 任务中心 | 📋 | `bi-list-task` |
| 用户管理 | 👥 | `bi-people` |
| 角色管理 | 👤 | `bi-people-fill` |
| 用户组 | 📦 | `bi-collection` |
| 操作日志 | 📜 | `bi-journal-text` |
| 登录日志 | 🔐 | `bi-box-arrow-in-right` |
| 个人中心 | 👤 | `bi-person` |
| 退出登录 | 🚪 | `bi-box-arrow-right` |

---

## 9. 命名约定

### 9.1 CSS 类名

采用 `mngxops-` 前缀避免与 Bootstrap 冲突：

- 布局：`mngxops-sidebar`, `mngxops-topbar`, `mngxops-content`
- 组件：`mngxops-modal`, `mngxops-toast`, `mngxops-stat-card`
- 状态：`mngxops-online`, `mngxops-offline`, `mngxops-loading`

### 9.2 JS 函数

采用驼峰命名：
- 工具函数：`relativeTime()`, `formatBytes()`, `parseSearchTerms()`
- API 调用：`fetchNodes()`, `testConnection()`, `batchSync()`
- UI 操作：`showModal()`, `hideModal()`, `showToast()`

---

## 10. 重构优先级建议

| 优先级 | 重构项 | 说明 |
|--------|--------|------|
| P0 | toast.js | 统一 Toast 组件，替换 Bootstrap alert |
| P0 | modal.js | 自定义弹窗组件，替换 confirm()/alert() |
| P1 | 统一表格样式 | 所有列表页面使用一致的表格组件 |
| P1 | 统一搜索筛选区 | 提取为可复用模板片段 |
| P1 | 更新时间显示 | 使用相对时间（relativeTime）函数 |
| P2 | 动画效果 | 统一添加卡片悬停、Toast、弹窗动画 |
| P2 | 空状态组件 | 统一空状态展示 |
| P3 | 响应式适配 | 移动端适配优化 |
| P3 | 骨架屏加载 | 列表页面加载骨架屏 |