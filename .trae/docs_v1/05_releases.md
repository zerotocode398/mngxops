# MngxOps 需求文档 - 05 发布管理

> **功能模块**: 发布管理（Releases）  
> **URL**: `/releases/`  
> **核心模型**: `ReleaseTask`, `ReleaseHistory`, `TaskCenterTask`  
> **关联模型**: `ConfigNodeBinding`, `BindingVersion`（来自 04_configs.md）  
> **视图**: `apps/releases/views.py`

---

## 1. 功能概述

发布管理模块负责将平台管理的配置安全地推送到远程节点。与修正后的配置管理模块（`ConfigNodeBinding` 内容独立 + 版本独立）配合：

- **节点为主维度**：发布中心先选节点，再展开每个节点下的配置绑定列表，勾选要发布的配置——符合运维人员"操作某台机器"的思维模式
- **绑定感知**：从 `ConfigNodeBinding` 读取每对 (节点, 配置) 的 content、version、remote_path
- **独立版本发布**：每条 ReleaseTask 发布的是"某条 Binding 的某个版本的内容"
- **回写同步状态**：发布成功后自动更新 Binding 的 `synced_version` 和 `remote_content_hash`

### 1.1 为什么以节点为主维度

| 对比维度 | 旧方案（配置 × 节点矩阵） | 新方案（节点 → 配置绑定） |
|---------|------------------------|------------------------|
| 运维思维 | "nginx.conf 要推哪些节点？"| "web01 上有哪些配置要发布？" |
| 可扩展性 | 32 配置 × 100 节点 = 3200 单元格 | 选 5 个节点 → 每节点展开 2-5 绑定 → 10-25 行 |
| 执行粒度 | 矩阵无法体现串行/并行顺序 | 按节点分组 = 按执行顺序自然排列 |
| 进度追踪 | 单配置列表无法映射节点状态 | 节点 → 配置的树形结构一目了然 |

> **核心原则**: 运维人员操作时想的是"我要操作 web01/web02/web03 这几台机器上的配置"，而非"nginx.conf 这个配置文件要推给哪些节点"。`ConfigNodeBinding` 本身就是 (node, config) 的 N-1 映射，以 node 为分组键是最自然的查询方式。

---

## 2. 与配置管理模块的联动

```
ConfigNodeBinding                          ReleaseTask
┌──────────────────────────────┐          ┌──────────────────────────────┐
│ config: "nginx.conf"         │          │ binding: nginx@web01         │
│ node: web01                  │──发布──→│ config: "nginx.conf"         │
│ content: (web01 独立内容)     │          │ node: web01                  │
│ current_version: 3           │          │ version: 3 ← 发布 BV3        │
│ remote_path: /etc/nginx/...  │          │ remote_path: (继承自 binding) │
│ sync_status: modified        │          │ content: (复用 binding)       │
│ synced_version: 2            │          └──────────────────────────────┘
└──────────────────────────────┘
                        ↑ 发布成功后回写
                    synced_version = 3
                    remote_content_hash = md5(content)
                    sync_status = "synced"
```

**核心联动规则**：
- 发布中心从 `ConfigNodeBinding` 按节点分组查询绑定列表
- ReleaseTask 的 `version` 字段引用 `BindingVersion.version`（而非全局 ConfigVersion）
- 发布成功后回写 `ConfigNodeBinding`：`synced_version ← version`，`remote_content_hash ← md5(content)`，`sync_status ← synced`

---

## 3. 数据模型

### 3.1 ReleaseTask

```python
class ReleaseTask(models.Model):
    """
    发布任务 - 每条记录 = 某条绑定 + 某个版本 发布到远程节点
    多个 ReleaseTask 共享相同 batch_number 表示同一批次
    """

    STATUS_CHOICES = (
        ("pending", "等待发布"),
        ("running", "发布中"),
        ("success", "发布成功"),
        ("failed", "发布失败"),
        ("rollback", "已回滚"),
        ("cancelled", "已取消"),
    )

    id = models.BigAutoField(primary_key=True)
    batch_number = models.CharField(max_length=32, db_index=True, verbose_name="批次号")

    # 核心关联：绑定 + 版本
    binding = models.ForeignKey(
        "configs.ConfigNodeBinding",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="release_tasks",
        verbose_name="关联绑定",
        help_text="发布将沿用绑定的 remote_path 和 content",
    )
    config = models.ForeignKey(
        "configs.Config", on_delete=models.CASCADE, verbose_name="配置标签",
    )
    node = models.ForeignKey(
        "nodes.Node", on_delete=models.CASCADE, verbose_name="目标节点",
    )

    # 发布哪个版本（BindingVersion.version，而非全局 ConfigVersion）
    publish_version = models.IntegerField(
        verbose_name="发布版本号",
        help_text="绑定的版本号，如 V3 表示绑定第 3 版",
    )
    # 远程路径（优先从 binding 继承，若 raw 模式则手动指定）
    remote_path = models.CharField(max_length=500, blank=True, verbose_name="远程路径")

    operator = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="操作人")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="状态")
    result = models.TextField(blank=True, verbose_name="执行结果")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "发布任务"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    @property
    def content_to_publish(self):
        """发布时使用的配置内容 —— 从绑定的 BindingVersion 中读取"""
        if self.binding:
            try:
                bv = self.binding.versions.get(version=self.publish_version)
                return bv.content
            except BindingVersion.DoesNotExist:
                pass
        return self.binding.content if self.binding else ""
```

### 3.2 ReleaseHistory 模型

| 字段 | 类型 | 说明 |
|------|------|------|
| `release_task` | FK → ReleaseTask | 关联任务 |
| `node` | FK → Node | 目标节点 |
| `config` | FK → Config | 配置标签 |
| `version` | IntegerField | 发布的绑定版本号 |
| `operator` | FK → User | 操作人 |
| `action` | CharField | publish / rollback |
| `result` | TextField | 执行结果 |
| `created_at` | DateTimeField | 创建时间 |

### 3.3 TaskCenterTask 模型（扩展 operation_type）

```python
OPERATION_TYPE_CHOICES = (
    ("release_publish", "发布配置"),
    ("release_rollback", "回滚配置"),
    ("credential_enable_test", "凭证启用测试"),
    ("node_batch_test", "节点批量测试"),
    ("node_system_info", "节点系统信息采集"),
    ("node_nginx_version", "Nginx 版本检测"),
    ("config_batch_sync", "配置批量同步"),
    ("config_discover", "配置发现扫描"),
    ("config_drift_check", "配置漂移检测"),
    ("nginx_upgrade", "Nginx 编译升级"),
    ("nginx_rollback", "Nginx 升级回滚"),
    ("other", "其他任务"),
)
```

---

## 4. 页面设计

### 4.1 发布中心（节点为主维度）⭐ 重新设计

**核心思路**: 先选节点 → 展示每个节点下所有配置绑定的状态 → 勾选要发布的绑定 → 确认发布。

```
┌──────────────────────────────────────────────────────────────────┐
│  🚀 发布中心                             [从绑定列表跳转 | 快捷推送]│
│                                                                  │
│  ┌─ 第1步：选择目标节点 ──────────────────────────────────────┐ │
│  │ ┌────────────────────┐ ┌──────────┐ ┌──────────┐          │ │
│  │ │ 🔍 搜索主机名/IP    │ │ 环境 ▼   │ │ 节点组 ▼  │          │ │
│  │ └────────────────────┘ └──────────┘ └──────────┘          │ │
│  │                                                             │ │
│  │ ☑ 全选（仅可选节点） 已选 2/128 个节点   [清除选择]        │ │
│  │                                                             │ │
│  │ ┌─ 可选节点列表（每行一个节点，可展开绑定详情）────────┐  │ │
│  │ │ ☑ │ 节点   │ 环境 │ 状态 │ 绑定数/待推 │ 快捷操作    │  │ │
│  │ │───┼───────┼─────┼─────┼───────────┼───────────│  │ │
│  │ │ ☑ │ web01 │ 生产 │ 🟢在线 │ 3/1 📝     │ [全选配置] │  │ │
│  │ │   │   ▶ nginx.conf  V3 📝待推送  路径: /etc/nginx/nginx.conf  │
│  │ │   │     ssl.conf    V5 ✅已同步  路径: /etc/nginx/conf.d/ssl   │
│  │ │   │   upstream.conf V2 ✅已同步  路径: /etc/nginx/conf.d/up    │
│  │ │───┼───────┼─────┼─────┼───────────┼───────────│  │ │
│  │ │ ☑ │ web02 │ 生产 │ 🟢在线 │ 2/1 📝     │ [全选配置] │  │ │
│  │ │   │   ▶ nginx.conf  V1 📝待推送  路径: /etc/nginx/nginx.conf  │
│  │ │   │     ssl.conf    V5 ✅已同步  路径: /etc/nginx/conf.d/ssl   │
│  │ │───┼───────┼─────┼─────┼───────────┼───────────│  │ │
│  │ │ ☐ │ web03 │ 测试 │ 🟢在线 │ 2/1 ⚠️     │ 🔒 锁定  │  │ │
│  │ │───┼───────┼─────┼─────┼───────────┼───────────│  │ │
│  │ │ ☐ │ web04 │ 开发 │ 🔴离线 │ 1/0     │ 不可选  │  │ │
│  │ └──────────────────────────────────────────────────────┘ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ 第2步：确认发布清单 ──────────────────────────────────────┐ │
│  │ ┌─────────────────────────────────────────────────────────┐ │ │
│  │ │ 节点   │ 配置          │ 版本 │ 远程路径                │ 状态│ │
│  │ │────────┼──────────────┼─────┼───────────────────────┼────│ │
│  │ │ web01  │ nginx.conf    │  V3  │ /etc/nginx/nginx.conf  │ 📝 │ │
│  │ │ web01  │ ssl.conf      │  V5  │ /etc/nginx/conf.d/ssl  │ ✅ │ │
│  │ │ web01  │ upstream.conf │  V2  │ /etc/nginx/conf.d/up   │ ✅ │ │
│  │ │ web02  │ nginx.conf    │  V1  │ /etc/nginx/nginx.conf  │ 📝 │ │
│  │ │ web02  │ ssl.conf      │  V5  │ /etc/nginx/conf.d/ssl  │ ✅ │ │
│  │ └─────────────────────────────────────────────────────────┘ │ │
│  │                                                              │ │
│  │ 共 5 个发布单元，涉及 2 个节点，3 个配置标签                    │ │
│  │                                                              │ │
│  │ 发布选项:                                                     │ │
│  │   ☑ 发布前备份原配置文件                                      │ │
│  │   ☑ 发布后执行 nginx -t 语法检查                              │ │
│  │   ☑ 语法检查通过后执行 nginx -s reload                        │ │
│  │                                                              │ │
│  │ 发布策略: ○ 顺序（逐台） ○ 并行（最多 3 台）                   │ │
│  │         发布顺序: web01 → web02                                │ │
│  │                                                              │ │
│  │ [🚀 开始发布]                                                 │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

**交互规则**：

| 规则 | 说明 |
|------|------|
| 节点选择 | 节点行支持复选框勾选；离线/锁定节点灰色不可选并显示原因 |
| 展开绑定 | 点击节点行前的 `▶` 展开该节点下所有 `ConfigNodeBinding`，每行一个配置绑定 |
| 配置勾选 | 展开后每条绑定行有独立复选框，默认仅勾选 `sync_status=modified`（📝待推送）的绑定 |
| 版本选择 | 每条绑定显示 `current_version`，旁边有下拉可切换选择历史版本 |
| 全选配置 | 节点行的 `[全选配置]` 按钮一键勾选该节点所有绑定 |
| 排序 | 节点按环境（生产→测试→开发）+ 主机名排序；绑定按配置名排序 |
| 记忆 | 切换节点展开/收起状态通过 sessionStorage 保存，刷新页面时恢复 |

### 4.2 从配置列表快速推送

```
配置管理页 → 点击某条 Binding 的 [推送] 按钮
         → 跳转到发布中心，自动筛选该节点并展开绑定
         → 该绑定自动勾选
         → 用户可在此基础上加选更多节点/配置
```

### 4.3 发布执行进度弹窗（节点分组）⭐ 重新设计

```
┌─ 发布执行中 ──────────────────────────────────────────────┐
│                                                            │
│  📤 批次号: release-260710-0005    策略: 顺序发布            │
│                                                            │
│  ┌─ 总体进度 ─────────────────────────────────────────┐   │
│  │ ████████████████████░░░ 80%                         │   │
│  │ 已完成 2/2 节点，成功 4/5 配置，失败 1/5              │   │
│  └────────────────────────────────────────────────────┘   │
│                                                            │
│  ┌─ 按节点分组（可折叠）──────────────────────────────┐   │
│  │                                                     │   │
│  │ ▶ ✅ web01 ─ 全部成功 ────────────────────────     │   │
│  │      ├ ✅ nginx.conf   V3 → 已同步 (2.3s)           │   │
│  │      ├ ✅ ssl.conf     V5 → 已同步 (1.8s)           │   │
│  │      └ ✅ upstream    V2 → 已同步 (1.5s)           │   │
│  │                                                     │   │
│  │ ▼ ❌ web02 ─ 1成功 / 1失败 ─────────────────────     │   │
│  │      ├ 🔄 ssl.conf     V5 → 上传中...               │   │
│  │      └ ❌ nginx.conf   V1 → 校验失败                 │   │
│  │           wc -c: 期望 1280B，实际 0B                  │   │
│  │           [🔁 重试]                                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                            │
│  [⏹ 停止]  [▶ 跳过失败继续] [📋 完整日志]                 │
└────────────────────────────────────────────────────────────┘
```

**分组规则**：
- 实际发布按节点**逐台执行**（或并行最多 N 台），每个节点的所有绑定依次执行
- 一个节点全部成功显示 ✅ 绿色节点标题；部分失败显示 ❌ 红色
- 节点行可折叠，默认失败节点展开、成功节点收起
- 失败绑定支持**单条重试**（不重试已成功的同节点其他绑定）

**发布步骤**：

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 锁定版本 | 记录 `publish_version = binding.current_version` |
| 2 | 备份 | `cp {binding.remote_path} → {backup_dir}/{file}.{timestamp}` |
| 3 | 上传 | SFTP 上传 `binding.content` 到 `binding.remote_path` |
| 4 | 校验 | 远端 `wc -c` 对比 binding.content 大小；MD5 校验 |
| 5 | 检查 | `nginx -t` 语法测试 |
| 6 | Reload | `nginx -s reload` |
| 7 | 回写 | 成功→ binding.synced_version = publish_version, hash = md5(content), status=synced |

### 4.4 任务中心页

展示所有类型的异步任务（不仅限于发布），以**批次**为一级行，展开查看具体执行明细。

```
┌──────────────────────────────────────────────────────────────────┐
│  📋 任务中心                                                       │
│                                                                  │
│  ┌─ 筛选 ── 📛类型 ▼ │ 📊状态 ▼ │ 📅日期范围 ───────────────┐    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  ┌─ 任务列表（每条 = 一个批次/TaskCenterTask）─────────────────┐ │
│  │ ▶│ #42 │ 配置发布 │ ✅全部成功 │ 100% │ 2节点,5配置 │ 3分钟前 │ │
│  │  │    ├ ✅ web01: nginx.conf V3, ssl.conf V5, upstream V2   │ │
│  │  │    └ ✅ web02: ssl.conf V5, nginx.conf V1               │ │
│  │──│ #41 │ 配置发布 │ ❌部分失败 │ 67%  │ 2节点,4配置 │ 10分钟前│ │
│  │  │    ├ ✅ web01: nginx.conf V3, ssl.conf V5               │ │
│  │  │    └ ❌ web03: nginx.conf V2 → 校验失败                  │ │
│  │──│ #40 │ 凭证批量测试│ 🔄执行中  │ 45%  │ 8节点      │ 5分钟前 │ │
│  │──│ #39 │ 节点批量测试│ ✅全部成功 │ 100% │ 5节点      │ 1小时前 │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ 分页 ────────────────────────────────────────────────────┐  │
│  │ 共 128 条  [« 上一页]  [1] [2] ... [13]  [下一页 »]       │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**设计说明**：
- 任务中心是**跨功能模块**的异步任务集中展示入口，数据类型不限于发布（还包括凭证测试、节点探测、配置发现等）
- 每条记录对应一个 `TaskCenterTask`（即一个批次），展开后按节点分组显示明细
- 发布类型的批次号同时显示 `release-YYMMDD-XXXX`
- 非发布类型（如"凭证批量测试"）展开后显示按节点的测试结果

### 4.5 发布历史页（节点视角）⭐ 重新设计

```
┌──────────────────────────────────────────────────────────────────┐
│  📜 发布历史                                                       │
│                                                                  │
│  ┌─ 筛选 ── 🔍 批次/节点/配置 │ 📊状态 ▼ │ 👤操作人 ▼ │ 📅日期 ──┐│
│  └──────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌─ 按批次分组展示（每个批次可折叠）──────────────────────────┐ │
│  │                                                              │ │
│  │ ▼ release-260710-0005 │ 2026-07-10 08:30 │ admin │ ✅全部成功 │ │
│  │    ├ ✅ web01 ─ 3配置全部成功                                │ │
│  │    │   ├ ✅ nginx.conf   V3 → 成功  路径: /etc/nginx/...    │ │
│  │    │   ├ ✅ ssl.conf     V5 → 成功  路径: /etc/nginx/...    │ │
│  │    │   └ ✅ upstream    V2 → 成功  路径: /etc/nginx/...    │ │
│  │    └ ✅ web02 ─ 2配置全部成功                                │ │
│  │        ├ ✅ ssl.conf     V5 → 成功                           │ │
│  │        └ ✅ nginx.conf   V1 → 成功                           │ │
│  │─────────────────────────────────────────────────────────────│ │
│  │ ▶ release-260709-0004 │ 2026-07-09 15:20 │ ops   │ ❌部分失败 │ │
│  │─────────────────────────────────────────────────────────────│ │
│  │ ▶ release-260708-0003 │ 2026-07-08 10:00 │ admin │ ✅全部成功 │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ 分页 ────────────────────────────────────────────────────┐  │
│  │ 共 56 个批次  [« 上一页]  [1] [2] ... [6]  [下一页 »]     │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**设计说明**：
- 一级行 = 批次号（`release-YYMMDD-XXXX`），显示批次概要（时间、操作人、整体状态）
- 展开后**第一层按节点分组**（`web01` / `web02`），节点行显示该节点结果概要
- 展开节点后**第二层显示该节点的配置发布记录**（配置名、版本、状态、路径）
- 失败的任务旁边显示 `[回滚]` 操作按钮
- 筛选器支持按批次号、节点名、配置名搜索

---

## 5. API 接口

| URL | 方法 | 说明 |
|-----|------|------|
| `/releases/center/` | GET | 发布中心页面 |
| `/releases/api/nodes/` | GET | 获取可选节点列表（含绑定状态摘要） |
| `/releases/api/node-bindings/<node_id>/` | GET | 获取指定节点的所有绑定详情（用于发布中心展开行） |
| `/releases/create/` | POST | 批量创建发布任务（传入 [{node_id, binding_id, version}, ...]） |
| `/releases/<id>/rollback/` | POST | 回滚单个发布任务（基于备份文件） |
| `/releases/<id>/cancel/` | POST | 取消待执行/执行中的任务 |
| `/releases/<id>/retry/` | POST | 重试单条失败的发布任务 |
| `/releases/batch-rollback/<batch>/` | POST | 按批次号批量回滚 |
| `/releases/history/` | GET | 发布历史页（按批次分组） |
| `/releases/task-center/` | GET | 任务中心列表（所有异步任务类型） |
| `/releases/task-center/<id>/` | GET | 任务详情（按节点分组展开） |
| `/releases/api/task-progress/<id>/` | GET | 任务进度（Ajax 轮询，返回按节点分组状态） |
| `/releases/api/binding/<id>/versions/` | GET | 获取绑定的版本列表（用于发布时选择历史版本） |

---

## 6. 业务规则

| 编号 | 规则 | 说明 |
|------|------|------|
| R1 | 批次号 | `release-YYMMDD-XXXX`，同批 ReleaseTask 共享 |
| R2 | 发布流程 | 锁定版本→备份→上传→校验→nginx -t→reload→回写绑定 |
| R3 | 安全检查 | nginx -t 失败则中止，不执行 reload |
| R4 | 顺序/并行 | 顺序发布按节点逐台执行，单节点内逐配置执行；并行模式下最多 `max_parallel_tasks` 个节点同时发布 |
| R5 | **回写绑定** | 发布成功后自动更新 binding.synced_version / remote_content_hash / sync_status |
| R6 | raw 模式 | 无绑定的配置（app.conf→无绑定节点）可手动指定 remote_path 发布，成功后自动创建绑定 |
| R7 | 锁定禁止 | 已锁定节点不可选 |
| R8 | 异步执行 | 所有发布走 TaskCenterTask，禁止同步 SSH |
| R9 | 版本锁定 | push 前记录 binding.current_version 为 publish_version，push 期间不允许编辑该绑定 |
| R10 | 默认选择 | 展开节点后自动勾选 `sync_status=modified`（📝待推送）的绑定，`synced` 绑定默认不勾选 |
| R11 | 节点不可选 | 离线节点（🔴）、锁定节点（🔒）不显示复选框，灰显并标注原因 |

---

## 7. 发布后回写绑定逻辑

```python
def on_release_success(release_task: ReleaseTask):
    """发布成功后更新绑定状态"""
    binding = release_task.binding
    if not binding:
        return

    binding.synced_version = release_task.publish_version
    # hash 在 SSH 上传校验阶段已计算过（远端 md5sum），直接写入
    binding.remote_content_hash = release_task.remote_md5  # 发布流程中计算的
    binding.sync_status = "synced"
    binding.last_sync_time = timezone.now()
    binding.save(update_fields=[
        "synced_version", "remote_content_hash",
        "sync_status", "last_sync_time",
    ])
```

---

## 8. 与配置管理的数据流

```
配置管理页（绑定列表，按节点分组）        发布中心（节点视图）
┌──────────────────────────────┐      ┌────────────────────────────────┐
│ web01                        │      │ ☑ web01  生产  🟢  3/1 📝      │
│ ├ nginx.conf V3 📝─[推送]──┐│跳转  │   ├ ☑ nginx.conf V3 📝         │
│ ├ ssl.conf   V5 ✅         ││预填  │   ├ ☐ ssl.conf   V5 ✅         │
│ └ upstream   V2 ✅         ││      │   └ ☐ upstream   V2 ✅         │
│ web02                        ││      │ ☑ web02  生产  🟢  2/1 📝      │
│ ├ nginx.conf V1 📝          ││      │   ├ ☑ nginx.conf V1 📝         │
│ └ ssl.conf   V5 ✅          ││      │   └ ☐ ssl.conf   V5 ✅         │
└──────────────────────────────┘      │                                │
                                      │ [🚀 开始发布] → 5条ReleaseTask │
                                      └────────────────────────────────┘
                                               │
                ┌──────────────────────────────┼──────────────────────┐
                ▼                              ▼                      ▼
          ReleaseTask#1                  ReleaseTask#2          ReleaseTask#5
          batch: release-001             batch: release-001     batch: release-001
          node: web01, binding: nginx    node: web01, ssl       node: web02, ssl
          version: 3                     version: 5             version: 5
                │                              │                      │
            发布成功 ▼                    发布成功 ▼             发布成功 ▼
          ngx@web01:                    ssl@web01:              ssl@web02:
          synced_ver=3                  synced_ver=5            synced_ver=5
          hash=md5(V3)                  hash=md5(V5)            hash=md5(V5)
          status=synced                 status=synced           status=synced
```

---

## 9. 重构建议

| 编号 | 建议 | 说明 |
|------|------|------|
| S1 | ReleaseTask 版本号改为绑定级别 | `publish_version` 对应 `BindingVersion.version`，而非旧的 `ConfigVersion` |
| S2 | 发布中心改为节点视图 | 废弃"配置×节点"矩阵，改用"节点→展开绑定"的树形选择器 |
| S3 | 发布回写自动化 | `on_release_success` 回调自动更新 binding 状态 |
| S4 | raw 模式兜底 | 无绑定的配置发布成功时自动创建 `ConfigNodeBinding` |
| S5 | 发布锁定 | 发布期间锁定 binding（可选），防止并发编辑导致版本号漂移 |
| S6 | 进度弹窗节点分组 | 按节点分组的树形进度展示，支持单条失败重试 |
| S7 | 发布历史重设计 | 改用"批次 → 节点 → 配置"的三级伸缩结构，以节点为中间分组层 |