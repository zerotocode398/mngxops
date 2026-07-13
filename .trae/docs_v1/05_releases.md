# MngxOps 需求文档 - 05 发布管理

> **功能模块**: 发布管理（Releases）  
> **URL**: `/releases/`  
> **核心模型**: `ReleaseTask`, `ReleaseHistory`, `TaskCenterTask`  
> **关联模型**: `ConfigNodeBinding`, `BindingVersion`（来自 04_configs.md）  
> **视图**: `apps/releases/views.py`

---

## 1. 功能概述

发布管理模块负责将平台管理的配置安全地推送到远程节点。与修正后的配置管理模块（`ConfigNodeBinding` 内容独立 + 版本独立）配合：

- **矩阵式发布中心**：多配置 × 多节点的交叉选择，每个单元格展示**该节点上该配置的独立版本号和同步状态**
- **绑定感知**：从 `ConfigNodeBinding` 读取每对 (配置, 节点) 的 content、version、remote_path
- **独立版本发布**：每条 ReleaseTask 发布的是"某条 Binding 的某个版本的内容"
- **回写同步状态**：发布成功后自动更新 Binding 的 `synced_version` 和 `remote_content_hash`

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
- 发布中心从 `ConfigNodeBinding` 矩阵读取每个单元格的独立 `content`、`current_version`、`remote_path`
- ReleaseTask 的 `version` 字段引用 `BindingVersion.version`（而非全局 ConfigVersion）
- 发布成功后回写 `ConfigNodeBinding`：`synced_version ← version`，`remote_content_hash ← md5(content)`，`sync_status ← synced`

---

## 3. 数据模型

### 3.1 ReleaseTask（重构后）

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

### 3.2 ReleaseHistory 模型（不变）

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

### 4.1 发布中心（矩阵式选择器）⭐ 关键

```
┌──────────────────────────────────────────────────────────────┐
│  🚀 发布中心                     [从绑定列表跳转 | 快捷推送]  │
│                                                              │
│  ┌─ 第1步：选择配置标签（多选）─ 按 Config 列出所有绑定 ──┐ │
│  │ ☑ nginx.conf ─ 3个绑定                                 │  │
│  │ ☐ ssl.conf   ─ 2个绑定                                 │  │
│  │ ☐ upstream.conf ─ 0个绑定（未绑定任何节点）              │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 第2步：发布矩阵（配置 × 节点）── 每个单元格独立版本 ──┐ │
│  │ ┌──────────────────────────────────────────────────┐    │ │
│  │ │ 配置\节点 │ ☑web01  │ ☑web02  │ ☐web03 │ 行统计 │    │ │
│  │ │──────────┼─────────┼─────────┼────────┼───────│    │ │
│  │ │nginx.conf │V3 📝待推│V1 ✅同步│V2 ⚠️冲突│ 2/3选 │    │ │
│  │ │ ssl.conf │V5 ✅同步│V5 ✅同步│  ─     │ 2/2选 │    │ │
│  │ │ 列统计   │  2/2   │  2/2   │  1/2   │ 总计  │    │ │
│  │ └──────────────────────────────────────────────────┘    │ │
│  │                                                          │ │
│  │ 已选 4 个发布单元:                                        │ │
│  │   nginx.conf@web01 V3, nginx.conf@web03 V2,              │ │
│  │   ssl.conf@web01 V5,     ssl.conf@web02 V5               │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─ 第3步：确认发布摘要 ────────────────────────────────┐  │
│  │ # │ 配置      │节点  │版本│远程路径            │状态│  │
│  │ 1 │ nginx.conf│web01 │ V3 │/etc/nginx/nginx..  │📝 │  │
│  │ 2 │ nginx.conf│web03 │ V2 │/etc/nginx/nginx..  │⚠️ │  │
│  │ 3 │ ssl.conf  │web01 │ V5 │/etc/nginx/conf.d/..│✅ │  │
│  │ 4 │ ssl.conf  │web02 │ V5 │/etc/nginx/conf.d/..│✅ │  │
│  │───────────────────────────────────────────────────│  │
│  │ 共 4 个发布单元，涉及 2 个配置标签，3 个节点         │  │
│  │                                                       │  │
│  │ 发布选项:                                              │  │
│  │   ☑ 发布前备份原配置文件                               │  │
│  │   ☑ 发布后执行 nginx -t 语法检查                       │  │
│  │   ☑ 语法检查通过后执行 nginx -s reload                 │  │
│  │                                                       │  │
│  │ 发布策略: ○ 顺序（逐台） ○ 并行（最多 3 台）           │  │
│  │                                                       │  │
│  │ [🚀 开始发布]                                          │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

**矩阵交互说明**（与修正后的配置模型对齐）：
- **每个单元格是独立的**：`V3` / `V1` / `V2` 分别代表 nginx.conf 在 web01/web02/web03 上的绑定版本号
- `📝待推送` = 绑定 `modified`（current_version > synced_version）
- `✅已同步` = 绑定 `synced`
- `⚠️冲突` = 绑定 `conflict`
- `─` = 该节点没有此配置的绑定
- 勾选后，**每个 ReleaseTask 发布的是其对应 Binding 的指定版本内容**

### 4.2 从配置列表快速推送

```
配置列表 → 点击某条 Binding 的 [推送] 按钮
         → 跳转到发布中心，自动勾选 (Config=nginx.conf, Node=web01)
         → 该单元格自动选中，版本号为绑定的 current_version
         → 用户可在此基础上加选更多配置/节点
```

### 4.3 发布执行进度弹窗

```
┌─ 发布执行中 ──────────────────────────────────────────┐
│                                                         │
│  📤 批次号: release-260710-0005    策略: 顺序发布        │
│                                                         │
│  ┌─ 总体进度 ──────────────────────────────────────┐   │
│  │ ████████████████░░░░ 75%                         │   │
│  │ 已完成 3/4，成功 2，失败 1                         │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─ 发布单元详情 ──────────────────────────────────┐   │
│  │ ✅ [1] nginx.conf@web01 V3 → synced               │   │
│  │    绑定的 synced_version 已更新为 3                 │   │
│  │ ─────────────────────────────────────────────── │   │
│  │ ❌ [2] nginx.conf@web03 V2 → 校验失败              │   │
│  │    wc -c 结果: 期望1280B，实际0B                     │   │
│  │ ─────────────────────────────────────────────── │   │
│  │ ✅ [3] ssl.conf@web01 V5 → synced                 │   │
│  │ ─────────────────────────────────────────────── │   │
│  │ 🔄 [4] ssl.conf@web02 V5 → 上传中...              │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  [⏹ 停止]  [▶ 跳过失败继续] [📋 完整日志]              │
└─────────────────────────────────────────────────────────┘
```

**发布步骤**（不变，但与 Binding 版本关联）：

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

```
┌──────────────────────────────────────────────────────────────┐
│  📋 任务中心                                                   │
│  ┌─ 筛选 ── 📛类型 ▼ │ 📊状态 ▼ │ 📅日期范围 ──────────────┐  │
│  │ ID│类型     │状态│进度│目标               │创建    │操作  │  │
│  │ 42│配置发布 │✅  │100%│4项(3台,2标签)      │3分钟前 │详情  │  │
│  │ 41│配置发布 │❌  │67% │3项(2台,2标签)      │10分钟前│详情  │  │
│  └──────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 4.5 发布历史页

```
┌──────────────────────────────────────────────────────────────┐
│  📜 发布历史                                                   │
│  ┌─ 筛选 ── 🔍批次/配置/节点 │ 📊状态 ▼ │ 👤操作人 ▼ ──────┐ │
│  │ 批次号      │配置     │节点  │版本│状态│操作人│时间      │  │
│  │ release-..05│nginx.cnf│web01│ V3 │✅  │admin │08:30     │  │
│  │ release-..05│nginx.cnf│web03│ V2 │❌  │admin │08:30     │  │
│  │             │         │     │    │[回滚]│     │          │  │
│  │ release-..05│ssl.conf │web01│ V5 │✅  │admin │08:31     │  │
│  │ release-..04│app.conf │web02│ V1 │✅  │ops   │昨天      │  │
│  └──────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. API 接口

| URL | 方法 | 说明 |
|-----|------|------|
| `/releases/center/` | GET | 发布中心页面（矩阵选择器） |
| `/releases/create/` | POST | 批量创建发布任务（传入 binding_id → version 列表） |
| `/releases/<id>/rollback/` | POST | 回滚单个发布任务（基于备份文件） |
| `/releases/<id>/cancel/` | POST | 取消待执行/执行中的任务 |
| `/releases/batch-rollback/<batch>/` | POST | 按批次号批量回滚 |
| `/releases/history/` | GET | 发布历史页 |
| `/releases/task-center/` | GET | 任务中心列表 |
| `/releases/task-center/<id>/` | GET | 任务详情 |
| `/releases/api/task-progress/<id>/` | GET | 任务进度（Ajax 轮询） |
| `/releases/api/bindings-matrix/` | POST | 传入 config_ids + node_ids → 返回矩阵数据 |
| `/releases/api/binding/<id>/versions/` | GET | 获取绑定的版本列表 |

---

## 6. 业务规则

| 编号 | 规则 | 说明 |
|------|------|------|
| R1 | 批次号 | `release-YYMMDD-XXXX`，同批 ReleaseTask 共享 |
| R2 | 发布流程 | 锁定版本→备份→上传→校验→nginx -t→reload→回写绑定 |
| R3 | 安全检查 | nginx -t 失败则中止，不执行 reload |
| R4 | 顺序/并行 | 顺序发布逐台执行，单台失败可跳过；并行 ≤ max_parallel_tasks |
| R5 | **回写绑定**（核心新增） | 发布成功后自动更新 binding.synced_version / remote_content_hash / sync_status |
| R6 | raw 模式 | 无绑定的配置（app.conf→无绑定节点）可手动指定 remote_path 发布，成功后自动创建绑定 |
| R7 | 锁定禁止 | 已锁定节点不可选 |
| R8 | 异步执行 | 所有发布走 TaskCenterTask，禁止同步 SSH |
| R9 | 版本锁定 | push 前记录 binding.current_version 为 publish_version，push 期间不允许编辑该绑定 |

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
配置管理页（绑定列表）                          发布中心（矩阵选择器）
┌────────────────────────────┐      ┌──────────────────────────────┐
│ nginx.conf                 │      │         nginx.conf  ssl.conf │
│ ├ web01 V3 📝─[推送]───┐  │      │ web01  ☑ V3 📝     ☑ V5 ✅  │
│ ├ web02 V1 ✅          │  │跳转  │ web02  ☐ V1 ✅     ☑ V5 ✅  │
│ └ web03 V2 ⚠️          │  │预填  │ web03  ☑ V2 ⚠️     ☐ ─     │
│ ssl.conf                │  │      │                              │
│ ├ web01 V5 ✅           │  │      │ [🚀 开始发布] → 3条ReleaseTask│
│ └ web02 V5 ✅           │  │      │                              │
└────────────────────────────┘      └──────────────────────────────┘
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    ▼                        ▼                        ▼
              ReleaseTask#1             ReleaseTask#2             ReleaseTask#3
              binding: ngx@web01        binding: ngx@web03        binding: ssl@web01
              version: 3                version: 2                version: 5
                    │                        │                        │
              发布成功 ▼                发布失败 ▼               发布成功 ▼
              ngx@web01:               ngx@web03:               ssl@web01:
              synced_ver=3             synced_ver 不变          synced_ver=5
              hash=md5(V3)             (仍为旧值)               hash=md5(V5)
              status=synced            status=failed            status=synced
              last_sync=now                                     last_sync=now
```

---

## 9. 重构建议

| 编号 | 建议 | 说明 |
|------|------|------|
| S1 | ReleaseTask 版本号改为绑定级别 | `publish_version` 对应 `BindingVersion.version`，而非旧的 `ConfigVersion` |
| S2 | 矩阵单元格渲染独立 | 每个单元格展示该节点上该配置绑定的 `current_version` 和 `sync_status` |
| S3 | 发布回写自动化 | `on_release_success` 回调自动更新 binding 状态 |
| S4 | raw 模式兜底 | 无绑定的配置发布成功时自动创建 `ConfigNodeBinding` |
| S5 | 发布锁定 | 发布期间锁定 binding（可选），防止并发编辑导致版本号漂移 |
| S6 | 矩阵预览 API | `/releases/api/bindings-matrix/` 返回结构化矩阵数据供前端渲染 |