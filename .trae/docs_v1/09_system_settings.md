# MngxOps 需求文档 - 09 系统设置

> **功能模块**: 系统设置（System Settings）  
> **URL**: `/settings/`  
> **模型**: `apps/settings/models.py` → `SystemSetting`（新模块）  
> **说明**: 此模块为新增规划模块，将现有代码中的硬编码参数抽离为可配置项

---

## 1. 功能概述

系统设置模块负责管理平台内部运行参数，将现有代码中散布的硬编码常量统一为可通过页面配置的管理项。包括：

- 仪表盘展示参数（各列表最近显示条数）
- 节点探测参数（超时时间、并发数、重试次数）
- 配置同步参数（并发数、缓存超时）
- 配置发现参数（最大递归深度、默认 nginx 路径）
- 备份与保留策略（备份目录、版本保留天数、日志保留天数）
- SSH 连接参数（默认端口、连接超时）
- 发布策略参数（单节点超时、最大并行任务数）

---

## 2. 现有硬编码参数盘点

通过扫描现有代码，梳理出以下分散在代码中的硬编码参数：

| 编号 | 参数 | 当前值 | 位置 | 影响功能 |
|------|------|--------|------|---------|
| P0 | 仪表盘最近记录条数 | `[:10]` | `apps/dashboard/views.py` | 仪表盘各列表的显示数量 |
| P1 | 批量操作最大节点数 | `3` | `apps/nodes/views.py` (多处), `apps/configs/views.py` | 节点批量测试/锁定/解锁/同步 |
| P2 | SSH 连接超时 | `10` 秒 | `utils/ssh.py` (全部 20+ 处) | 所有 SSH 远程操作 |
| P3 | 配置发现最大递归深度 | `3` 层 | `utils/ssh.py:discover_nginx_configs()` | 远程 nginx 配置文件扫描 |
| P4 | 配置版本保留天数 | `180` 天 | `apps/configs/models.py:prune_old_versions()` | 历史版本自动清理 |
| P5 | 凭证测试最大并发数 | `min(10, n)` | `apps/credentials/views.py` | 凭证启用批量测试 |
| P6 | 远程备份目录 | `/opt/app/mascloud/ansible/mngxops` | `utils/ssh.py:backup_remote_file()` | 配置发布前备份 |
| P7 | 默认 nginx 配置路径 | `/etc/nginx/nginx.conf` | `utils/ssh.py:discover_nginx_configs()` | 配置发现默认路径 |
| P8 | 批量同步缓存超时 | `300` 秒 | `apps/configs/views.py` (多处) | 批量同步进度缓存 |
| P9 | 单节点发布超时 | `60` 秒（隐式） | `apps/releases/views.py` | 发布任务超时控制 |
| P10 | 最大并行任务数 | `3`（隐式） | 多处 ThreadPoolExecutor | 并发异步任务控制 |
| P11 | 任务中心进度轮询间隔 | 未显式定义（前端轮询频率） | - | 进度更新频率 |

**仪表盘硬编码来源**（`apps/dashboard/views.py`）:

```python
recent_nodes = Node.objects.select_related("credential").order_by("-updated_at")[:10]
recent_tasks = ReleaseTask.objects.select_related(...).order_by("-created_at")[:10]
failed_configs = Config.objects.filter(sync_status="failed").prefetch_related("nodes").order_by("-updated_at")[:10]
```

所有 `[:10]` 均为硬编码，应改为从系统设置读取。

---

## 3. 数据模型

### 3.1 SystemSetting 模型

采用 **key-value** 模型存储所有配置项，灵活扩展。

```python
class SystemSetting(models.Model):
    """系统设置 - 键值对模型"""

    TYPE_CHOICES = (
        ("string", "字符串"),
        ("integer", "整数"),
        ("boolean", "布尔"),
        ("json", "JSON"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    key = models.CharField(max_length=100, unique=True, verbose_name="配置键")
    value = models.TextField(verbose_name="配置值")
    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default="string",
        verbose_name="值类型",
    )
    group = models.CharField(
        max_length=50,
        verbose_name="配置分组",
    )
    label = models.CharField(max_length=100, verbose_name="显示名称")
    description = models.TextField(blank=True, verbose_name="说明")
    placeholder = models.CharField(max_length=255, blank=True, verbose_name="占位提示")
    options = models.TextField(blank=True, verbose_name="可选值JSON")  # 用于下拉选择型配置
    is_required = models.BooleanField(default=True, verbose_name="必填")
    sort_order = models.IntegerField(default=0, verbose_name="排序")
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, verbose_name="最后修改人",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
```

### 3.2 预置配置项

| key | group | type | 默认值 | 说明 |
|-----|-------|------|--------|------|
| `dashboard.recent_nodes_count` | 仪表盘 | integer | 10 | 仪表盘"最近操作节点"显示条数 |
| `dashboard.recent_tasks_count` | 仪表盘 | integer | 10 | 仪表盘"最近发布任务"显示条数 |
| `dashboard.recent_failed_configs_count` | 仪表盘 | integer | 10 | 仪表盘"同步失败配置"显示条数 |
| `node.batch_max_count` | 节点管理 | integer | 3 | 批量操作最大节点数 |
| `node.ssh_connect_timeout` | 节点管理 | integer | 10 | SSH 连接超时（秒） |
| `node.ssh_default_port` | 节点管理 | integer | 22 | SSH 默认端口 |
| `node.detect_retries` | 节点管理 | integer | 1 | 节点探测重试次数 |
| `credential.test_max_concurrency` | 凭证管理 | integer | 10 | 凭证测试最大并发数 |
| `config.discover_max_depth` | 配置管理 | integer | 3 | 配置发现最大递归深度 |
| `config.default_nginx_path` | 配置管理 | string | `/etc/nginx/nginx.conf` | 默认 nginx 主配置路径 |
| `config.version_retention_days` | 配置管理 | integer | 180 | 配置版本保留天数 |
| `config.sync_max_concurrency` | 配置管理 | integer | 3 | 配置同步最大并发节点数 |
| `config.sync_cache_timeout` | 配置管理 | integer | 300 | 同步进度缓存超时（秒） |
| `release.single_node_timeout` | 发布管理 | integer | 60 | 单节点发布超时（秒） |
| `release.max_parallel_tasks` | 发布管理 | integer | 3 | 最大并行任务数 |
| `release.backup_dir` | 发布管理 | string | `/opt/app/mascloud/ansible/mngxops` | 远程配置备份目录 |
| `audit.operation_log_retention_days` | 审计日志 | integer | 365 | 操作日志保留天数 |
| `audit.login_log_retention_days` | 审计日志 | integer | 180 | 登录日志保留天数 |
| `audit.login_max_fail_count` | 审计日志 | integer | 5 | 登录失败锁定阈值 |
| `audit.login_lock_minutes` | 审计日志 | integer | 30 | 登录锁定时间（分钟） |
| `system.task_progress_poll_interval` | 系统 | integer | 2 | 任务进度轮询间隔（秒） |

---

## 4. 页面设计

### 4.1 系统设置列表页（分组展示）

```
┌──────────────────────────────────────────────────────────────┐
│  ⚙️ 系统设置                                                  │
│                                                              │
│  ┌─ 设置分组 Tab ────────────────────────────────────────┐  │
│  │ [仪表盘] [节点] [凭证] [配置] [发布] [审计] [系统]       │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 仪表盘 ──────────────────────────────────────────────┐  │
│  │                                                        │  │
│  │  最近操作节点显示条数  ┌─────┐                          │  │
│  │                     │ 10  │ 条                         │  │
│  │                     └─────┘                            │  │
│  │                     仪表盘首页"最近操作节点"列表最大行数  │  │
│  │                                                        │  │
│  │  最近发布任务显示条数  ┌─────┐                          │  │
│  │                     │ 10  │ 条                         │  │
│  │                     └─────┘                            │  │
│  │                     仪表盘首页"最近发布任务"列表最大行数  │  │
│  │                                                        │  │
│  │  同步失败配置显示条数  ┌─────┐                          │  │
│  │                     │ 10  │ 条                         │  │
│  │                     └─────┘                            │  │
│  │                     仪表盘首页"同步失败配置"告警最大行数  │  │
│  │                                                        │  │
│  │  [💾 保存仪表盘设置]                                    │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 节点管理 ────────────────────────────────────────────┐  │
│  │  批量操作最大节点数  ┌─────┐                            │  │
│  │                     │  3  │ 台                         │  │
│  │                     └─────┘                            │  │
│  │  SSH 连接超时        ┌─────┐                            │  │
│  │                     │ 10  │ 秒                         │  │
│  │                     └─────┘                            │  │
│  │  SSH 默认端口        ┌─────┐                            │  │
│  │                     │ 22  │                            │  │
│  │                     └─────┘                            │  │
│  │  [💾 保存节点管理设置]                                   │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  (其他分组类似，Tab 切换展示)                                 │
└──────────────────────────────────────────────────────────────┘
```

#### 4.1.1 Tab 分组设计

| Tab | 包含配置项 |
|-----|-----------|
| 📊 仪表盘 | 最近节点条数、最近任务条数、失败配置条数 |
| 🔌 节点管理 | 批量最大节点数、SSH 超时、SSH 默认端口、探测重试次数 |
| 🔑 凭证管理 | 测试最大并发数 |
| 📝 配置管理 | 发现递归深度、默认 nginx 路径、版本保留天数、同步并发数、缓存超时 |
| 🚀 发布管理 | 单节点超时、最大并行任务数、备份目录 |
| 📜 审计日志 | 操作日志保留天数、登录日志保留天数、登录失败阈值、锁定时间 |
| 🖥 系统 | 任务进度轮询间隔 |

### 4.2 表单交互

**输入控件根据 type 自动适配**:

| type | 控件 | 示例 |
|------|------|------|
| `integer` | `<input type="number" min="..." max="...">` | 批量最大节点数 |
| `string` | `<input type="text">` | 备份目录、nginx 默认路径 |
| `boolean` | Toggle Switch | 是否启用某功能 |
| `json` | 根据 options 渲染下拉选择或代码编辑器 | 复杂配置 |

**交互行为**:
- 每个 Tab 分组独立保存（点击"保存 XX 设置"按钮）
- 保存成功后 Toast 提示 + 配置值实时生效（读缓存刷新）
- 修改前后值不同时，"保存"按钮才可点击
- 输入验证：整数范围检查、路径格式检查
- 首次加载时显示当前生效值
- 每项配置下方显示灰色说明文字

### 4.3 配置变更审计弹窗

保存关键配置时弹出确认：

```
┌─ 确认修改系统设置 ─────────────────────────────────┐
│                                                     │
│  ⚠️ 您正在修改以下系统设置：                          │
│                                                     │
│  ┌─ 变更详情 ───────────────────────────────────┐  │
│  │ 批量操作最大节点数:  3 → 5                     │  │
│  │ SSH 连接超时:       10秒 → 15秒               │  │
│  └──────────────────────────────────────────────┘  │
│                                                     │
│  修改后将立即生效，影响后续所有相关操作。              │
│                                                     │
│  [取消]                        [确认修改]            │
└─────────────────────────────────────────────────────┘
```

---

## 5. API接口

| URL | 方法 | 说明 |
|-----|------|------|
| `/settings/` | GET | 系统设置页面 |
| `/settings/save/` | POST | 保存指定分组的配置 (Ajax) |
| `/settings/api/group/<group>/` | GET | 获取指定分组的所有配置项 |
| `/settings/api/all/` | GET | 获取所有配置项 (JSON) |
| `/settings/api/<key>/` | GET | 获取单个配置值 |

---

## 6. 业务规则

| 编号 | 规则 | 说明 |
|------|------|------|
| R1 | 配置缓存 | 首次读取配置时从数据库加载并缓存（Django cache），保存后刷新缓存 |
| R2 | 范围限制 | 整数类型配置项需定义 min/max，如批量节点数 1-10，超时 5-120 秒，显示条数 1-50 |
| R3 | 立即生效 | 修改保存后，通过刷新缓存使新配置在下次操作时立即生效（无需重启） |
| R4 | 变更审计 | 所有配置变更记录 AuditLog（模块="系统设置"，动作="修改配置"） |
| R5 | 权限控制 | 仅拥有 `settings.update` 权限的用户可修改系统设置 |
| R6 | 默认值 | 若某配置项在数据库中不存在，代码使用硬编码默认值作为兜底 |
| R7 | 不可删除 | 系统配置项不可删除，只能修改值 |

---

## 7. 代码改造方案

### 7.1 新增 setting_service.py

```python
# utils/setting_service.py
from django.core.cache import cache

def get_setting(key, default=None):
    """从缓存读取系统设置，缓存未命中则查数据库"""
    cache_key = f"system_setting:{key}"
    value = cache.get(cache_key)
    if value is not None:
        return value

    try:
        obj = SystemSetting.objects.get(key=key)
        value = _cast_value(obj.value, obj.type)
    except SystemSetting.DoesNotExist:
        value = default

    cache.set(cache_key, value, timeout=3600)  # 缓存1小时
    return value

def refresh_setting_cache(key=None):
    """保存配置后刷新缓存"""
    if key:
        cache.delete(f"system_setting:{key}")
    else:
        ...
```

### 7.2 现有代码改造示例

```python
# Before (硬编码) — 节点批量:
MAX_BATCH = 3
if len(node_ids) > MAX_BATCH:
    ...

# After:
from utils.setting_service import get_setting
max_batch = get_setting("node.batch_max_count", default=3)

# Before (硬编码) — 仪表盘:
recent_nodes = Node.objects.select_related(...).order_by("-updated_at")[:10]

# After:
recent_nodes = Node.objects.select_related(...).order_by("-updated_at")[
    :get_setting("dashboard.recent_nodes_count", default=10)
]

# Before (硬编码) — 备份目录:
backup_remote_file(..., backup_dir="/opt/app/mascloud/ansible/mngxops")

# After:
backup_remote_file(..., backup_dir=get_setting("release.backup_dir", default="/opt/app/mascloud/ansible/mngxops"))
```

---

## 8. 权限扩展

### 8.1 新增权限资源

在 `apps/users/perm_defs.py` 中新增：

```python
# 资源新增
("settings", "系统设置"),

# 权限定义
"settings": {
    "read": "系统设置查看",
    "update": "系统设置修改",
}
```

### 8.2 导航权限

- `settings.read` → 侧边栏显示"系统设置"菜单项
- `settings.update` → 页面中"保存"按钮可用，否则仅可查看

---

## 9. 样式规范

### 9.1 设置分组卡片

```css
.setting-group-card {
  border: 1px solid #e9ecef;
  border-radius: 10px;
  margin-bottom: 24px;
}
.setting-group-header {
  background: #f8f9fa;
  padding: 14px 20px;
  border-bottom: 2px solid #e9ecef;
  font-weight: 600;
  font-size: 1rem;
}
.setting-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid #f0f0f0;
}
.setting-item:last-child {
  border-bottom: none;
}
.setting-item-label {
  flex: 0 0 220px;
  font-weight: 500;
  font-size: 0.9rem;
}
.setting-item-control {
  flex: 1;
  max-width: 300px;
}
.setting-item-hint {
  font-size: 0.8rem;
  color: #6c757d;
  margin-top: 4px;
}
```

---

## 10. 改造优先级

| 优先级 | 配置项 | 影响范围 | 改造难度 |
|--------|--------|---------|---------|
| P0 | `dashboard.recent_*_count` | 仪表盘显示 | 低（替换切片数字） |
| P0 | `node.ssh_connect_timeout` | 全局 SSH 操作 | 低 |
| P0 | `node.batch_max_count` | 批量操作限制 | 低 |
| P1 | `release.backup_dir` | 配置发布备份 | 低 |
| P1 | `config.version_retention_days` | 版本清理策略 | 低 |
| P1 | `config.default_nginx_path` | 配置发现 | 低 |
| P2 | `config.discover_max_depth` | 配置发现 | 低 |
| P2 | `credential.test_max_concurrency` | 凭证测试 | 低 |
| P2 | `audit.operation_log_retention_days` | 日志清理 | 中（需新增清理任务） |
| P3 | `release.single_node_timeout` | 发布超时 | 中（需改造超时逻辑） |
| P3 | `system.task_progress_poll_interval` | 任务进度 | 中（前端轮询间隔） |