# MngxOps 需求文档 - 04 配置管理

> **功能模块**: 配置管理（Configs）  
> **URL**: `/configs/`  
> **核心模型**: `Config`, `ConfigNodeBinding`, `BindingVersion`  
> **关联模块**: 05 发布管理（Releases）

---

## 1. 功能概述

配置管理负责 nginx 配置文件的全生命周期管理。核心设计理念经过修正：

```
旧思路（错误）:  Config → 多个 Node 共享同一份内容
               nginx.conf          → web01, web02, web03 都是同一份 nginx.conf
               ❌ web01 的 nginx.conf 与 web02 的 nginx.conf 显然内容不同！

新思路（正确）:  Config 作为"配置标签/类型"，内容隶属于每个 Binding
               nginx.conf          ← 只是一个标签
               ├── web01 的 nginx.conf     ← 有自己独立的内容和版本
               ├── web02 的 nginx.conf     ← 有自己独立的内容和版本
               └── web03 的 nginx.conf     ← 有自己独立的内容和版本
```

**Config 的职责**：
- 定义"这是什么配置"（名称、远程默认路径模板）
- 内容在创建绑定时从**远程节点读取**或**从模板复制**，后续各节点独立编辑

**ConfigNodeBinding 的职责**：
- 承载"配置在节点上的实际内容、版本历史、路径、同步状态"
- 每条绑定有独立的版本链

---

## 2. 核心问题分析

### 2.1 现有设计的双重缺陷

| 缺陷 | 说明 | 后果 |
|------|------|------|
| sync_status 在 Config 级别 | 一个配置关联 N 个节点，只能有一个状态 | 2 个成功 + 1 个失败 = 信息丢失 |
| content 在 Config 级别 | 所有绑定节点共享同一份内容 | **web01 的 nginx.conf = web02 的 nginx.conf** —— 这不现实 |

### 2.2 四种核心场景

| 场景 | 本地 | 远程 | 典型操作 |
|------|------|------|---------|
| A | ✅ 有 | ❌ 无 | 本地新建配置模板 → 绑定节点（内容为空或从模板生成）→ 推送到远程 |
| B | ❌ 无 | ✅ 有 | 配置发现 → 创建 Config(标签) + Binding + 从远程拉取内容 |
| C | ✅ 有(V1) | ✅ 有(V2) | 漂移检测 → 对比差异 → 决定推送/拉取 |
| D | ✅ 多配置 | ✅ 多节点 | 发布中心矩阵选择（每个单元格有独立内容和版本）→ 批量发布 |

### 2.3 同一配置在不同节点上的差异示例

```
nginx.conf 的内容差异（真实场景）:
┌──────────────────────┬──────────────────────┬──────────────────────┐
│ web01 (主节点)       │ web02 (从节点)       │ web03 (静态资源)     │
├──────────────────────┼──────────────────────┼──────────────────────┤
│ worker_processes 8;  │ worker_processes 4;  │ worker_processes 2;  │
│ server_name app.com; │ server_name app.com; │ server_name cdn.com; │
│ upstream {           │ upstream {           │                      │
│   server 10.0.1.1;  │   server 10.0.1.2;  │  (无 upstream)       │
│ }                    │ }                    │                      │
│ ssl_cert /cert/a;   │ ssl_cert /cert/b;   │ ssl_cert /cert/c;   │
└──────────────────────┴──────────────────────┴──────────────────────┘
三份内容都叫 nginx.conf，但内容完全不同！不可能共享同一份 Config.content。
```

---

## 3. 新数据模型

### 3.1 Config（配置标签）

```python
class Config(models.Model):
    """
    配置标签 - 定义"这是什么类型的配置"，不保存实际内容
    实际内容和版本历史存放在 ConfigNodeBinding 中
    """

    SOURCE_CHOICES = (
        ("manual", "手动创建"),
        ("discovered", "远程发现导入"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    name = models.CharField(max_length=255, verbose_name="配置名称")
    # 默认远程路径模板（创建绑定时可覆盖）
    default_remote_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="默认远程路径",
        help_text="如 /etc/nginx/conf.d/app.conf，创建绑定时自动填入，可修改",
    )
    # 初始内容模板（可选，创建绑定时作为初始内容）
    template_content = models.TextField(
        blank=True,
        verbose_name="内容模板",
        help_text="创建绑定时若远程无此文件，可基于此模板生成初始内容",
    )
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default="manual", verbose_name="来源",
    )
    description = models.TextField(blank=True, verbose_name="描述")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="创建人")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "配置"
        verbose_name_plural = verbose_name
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name
```

### 3.2 ConfigNodeBinding（配置-节点绑定）⭐ 核心模型

```python
class ConfigNodeBinding(models.Model):
    """
    配置与节点的绑定关系
    每条绑定独立存储内容、版本、路径、同步状态
    不同节点的同一 Config 可以有完全不同的内容
    """

    BINDING_SOURCE_CHOICES = (
        ("manual", "手动绑定"),
        ("discovered", "远程发现"),
    )

    SYNC_STATUS_CHOICES = (
        ("not_synced", "未同步"),     # 绑定刚创建，从未推送过
        ("synced", "已同步"),         # 本地 current_version == 远程实际内容
        ("modified", "本地已修改"),    # 绑定的 current_version > synced_version
        ("conflict", "冲突"),         # 本地改了 + 远程也改了（漂移检测发现）
        ("orphaned", "远程已删除"),    # 远程文件已不存在
        ("syncing", "同步中"),
        ("failed", "同步失败"),
    )

    id = models.BigAutoField(primary_key=True)
    config = models.ForeignKey(
        Config, on_delete=models.CASCADE, related_name="bindings", verbose_name="配置标签",
    )
    node = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name="config_bindings", verbose_name="节点",
    )

    # 该节点上的远程路径
    remote_path = models.CharField(
        max_length=500,
        verbose_name="远程文件路径",
        help_text="此配置在该节点上的绝对路径",
    )

    # ===== 内容 & 版本（每绑定独立）=====
    content = models.TextField(verbose_name="当前内容")
    current_version = models.IntegerField(default=1, verbose_name="当前版本号")

    # 同步状态
    sync_status = models.CharField(
        max_length=20, choices=SYNC_STATUS_CHOICES, default="not_synced",
        verbose_name="同步状态",
    )
    synced_version = models.IntegerField(
        null=True, blank=True,
        verbose_name="已同步版本",
        help_text="最后成功推送的版本号 → 等于 current_version 时状态为 synced",
    )
    last_sync_time = models.DateTimeField(null=True, blank=True, verbose_name="最后同步时间")
    last_sync_error = models.TextField(blank=True, verbose_name="最后同步错误")

    # 漂移检测（异步任务）
    remote_content_hash = models.CharField(
        max_length=64, blank=True,
        verbose_name="远程内容 Hash(MD5)",
        help_text="最后同步时记录的远程文件 MD5，用于检测漂移",
    )
    drift_detected_at = models.DateTimeField(null=True, blank=True, verbose_name="漂移检测时间")

    source = models.CharField(max_length=20, choices=BINDING_SOURCE_CHOICES, default="manual")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="创建人")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "配置节点绑定"
        verbose_name_plural = verbose_name
        unique_together = ("config", "node")
        ordering = ["config__name", "node__hostname"]

    def __str__(self):
        return f"{self.config.name} @ {self.node.hostname} (v{self.current_version})"

    @property
    def is_synced(self):
        return self.sync_status == "synced" and self.synced_version == self.current_version
```

### 3.3 BindingVersion（绑定版本历史）

```python
class BindingVersion(models.Model):
    """每条绑定的独立版本历史"""
    id = models.BigAutoField(primary_key=True)
    binding = models.ForeignKey(
        ConfigNodeBinding, on_delete=models.CASCADE, related_name="versions",
        verbose_name="绑定",
    )
    version = models.IntegerField(verbose_name="版本号")
    content = models.TextField(verbose_name="版本内容")
    remark = models.TextField(blank=True, verbose_name="备注")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="修改人")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "绑定版本"
        verbose_name_plural = verbose_name
        ordering = ["-version"]
        unique_together = ("binding", "version")
```

### 3.4 数据模型对比

| 维度 | 旧设计（错误） | 新设计（正确） |
|------|-------------|-------------|
| 内容存储 | `Config.content`（所有节点共享） | `ConfigNodeBinding.content`（每节点独立） |
| 版本管理 | `ConfigVersion`（Config 级别） | `BindingVersion`（Binding 级别） |
| 同步状态 | `Config.sync_status`（一个状态） | `ConfigNodeBinding.sync_status`（每节点独立） |
| 远程路径 | `Config.file_path` | `ConfigNodeBinding.remote_path`（每节点可不同） |
| Config 角色 | 既有内容又有状态 | **纯标签**：名称 + 默认路径模板 + 初始内容模板 |

### 3.5 状态流转图（不变，但绑定级别）

```
                     ┌─────────────┐
     手动创建绑定 →  │  not_synced │  ← 刚绑定，从未推送
                     └──────┬──────┘
                            │ 推送到远程
                     ┌──────▼──────┐
            ┌───────│   synced    │───────┐
            │       └──────┬──────┘       │
            │ 编辑绑定内容  │               │ 远程文件被修改
            │ version+1    │               │ (漂移检测发现)
       ┌────▼─────┐  ┌─────▼─────┐  ┌──────▼──────┐
       │ modified │  │  syncing  │  │   conflict   │
       └────┬─────┘  └─────┬─────┘  └──────┬───────┘
            │              │                │
            └── 推送 ─────→│← 同步结果      ├ 推送到远程 → syncing
                           │                └ 拉取到本地 → 覆盖 content
                           │                             → synced
        synced ── 远程文件被删除 ──→ orphaned
```

---

## 4. 四种场景的详细设计

### 4.1 场景 A：本地新建，远程没有

```
操作流程:
1. 用户点击 [ + 添加配置 ]
2. 填写配置名称（标签）如 "nginx.conf"
3. 填写默认远程路径模板：/etc/nginx/nginx.conf
4. (可选) 填写初始内容模板
5. 保存 → 创建 Config(标签)
6. 在配置列表展开 Config，点击 [ + 绑定节点 ]
7. 选择目标节点 web01，远程路径自动填充模板（可修改）
8. 内容：可选择"从模板复制"或"留空手动编辑"
9. 保存 → 创建 ConfigNodeBinding(sync_status=not_synced)
10. 用户编辑绑定内容 → version → V1
11. 用户可在发布中心将 (Config, Node, V1) 推送到远程
```

### 4.2 场景 B：远程发现，本地没有

```
操作流程:
1. 用户进入 [配置发现] 页面
2. 选择目标节点 web01
3. 后台 SSH 扫描 include 指令，发现远程文件列表：
   ├ /etc/nginx/nginx.conf (2560 bytes)
   ├ /etc/nginx/conf.d/ssl.conf (890 bytes)
   └ /etc/nginx/conf.d/upstream.conf (420 bytes)
4. 对于每个发现的文件：
   a. 检查是否已有同名 Config
   b. 已有 → 提示"是否新增此节点的绑定？"→ 拉取远程内容创建新绑定
   c. 没有 → 创建 Config(标签) + 拉取远程内容 + ConfigNodeBinding(sync_status=synced, source=discovered)
5. 导入后，每个绑定的 content = 远程实际内容，current_version = 1，synced_version = 1
```

### 4.3 场景 C：漂移检测

```
现有绑定: nginx.conf → web01 (content=V3, synced_version=2, remote_hash=md5(V2))

触发检测:
1. SSH 读取 web01 的 /etc/nginx/nginx.conf
2. 计算远程内容 MD5
3. 对比 remote_content_hash：
   - 相同 → sync_status 不变
   - 不同 → sync_status = "conflict", drift_detected_at = now

用户操作:
a. [查看差异] → 左右对比 binding.content(V3) vs 远程实际内容
b. [拉取远程] → 远程内容覆盖 binding.content → 新 BindingVersion(V4)
c. [推送本地] → binding.content(V3) 覆盖远程 → 走发布流程 → synced
```

### 4.4 场景 D：批量发布（每个单元格独立内容）

```
发布中心矩阵 ── 每个单元格有独立的版本:

            nginx.conf          ssl.conf
web01    V3(📝已修改)        V5(✅已同步)
web02    V1(✅已同步)        V5(✅已同步)
web03    V2(⚠️冲突)          ─(未绑定)

勾选: nginx.conf→web01(V3), nginx.conf→web03(V2), ssl.conf→web01(V5)
创建 3 条 ReleaseTask，各自独立执行
```

---

## 5. 配置列表页（重构后）

```
┌──────────────────────────────────────────────────────────────┐
│  📝 配置管理                       [+ 添加配置] [🔍 配置发现] │
│                                                              │
│  ┌─ 搜索筛选 ────────────────────────────────────────────┐  │
│  │ 🔍 配置名/节点 ┃ 同步状态 ▼ ┃ 来源 ▼ ┃ 环境 ▼ ┃ 每页 ▼ │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 数据表格（按 Config 展开绑定）────────────────────────┐ │
│  │▶│ nginx.conf ─ 默认路径: /etc/nginx/nginx.conf ─ 3绑定 │ │
│  │ │  ├ web01 │ V3 │📝待推送│ 编辑 对比 推送 历史         │ │
│  │ │  ├ web02 │ V1 │✅已同步│ 编辑 对比 推送 历史         │ │
│  │ │  └ web03 │ V2 │⚠️冲突  │ 编辑 对比 推送 历史         │ │
│  │─│ ssl.conf ─ 默认路径: /etc/nginx/conf.d/ssl ── 2绑定 │ │
│  │ │  ├ web01 │ V5 │✅已同步│ 编辑 对比 推送 历史         │ │
│  │ │  └ web02 │ V5 │✅已同步│ 编辑 对比 推送 历史         │ │
│  │ │ upstream.conf ─ 模板导入 ─ 2绑定                      │ │
│  │ │  ├ web01 │ V2 │✅已同步│ 编辑 对比 推送 历史         │ │
│  │ │  └ web02 │ ─ │📭孤立  │ (远程已删除) 解除绑定        │ │
│  └──────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 分页 ────────────────────────────────────────────────┐  │
│  │ 共 3 个配置标签，7 条绑定                               │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

---

## 6. 绑定状态徽标

| sync_status | 图标 | 颜色 | 含义 |
|-------------|------|------|------|
| `not_synced` | 🆕 | 灰 | 绑定刚创建，从未推送 |
| `synced` | ✅ | 绿 | content 与远程一致 |
| `modified` | 📝 | 蓝 | 本地版本 > 已同步版本，待推送 |
| `conflict` | ⚠️ | 橙 | 本地改了 + 远程也改了 |
| `orphaned` | 📭 | 红灰 | 远程文件已删除 |
| `syncing` | 🔄 | 蓝(动画) | 同步进行中 |
| `failed` | ❌ | 红 | 上次同步失败 |

---

## 7. API 接口（重构后）

| URL | 方法 | 说明 |
|-----|------|------|
| `/configs/` | GET | 配置标签列表（展开绑定树） |
| `/configs/create/` | GET/POST | 创建配置标签 |
| `/configs/<id>/edit/` | GET/POST | 编辑配置标签（名称/默认路径/模板） |
| `/configs/<id>/delete/` | POST | 删除标签 → 级联所有绑定 |
| `/configs/bindings/create/` | GET/POST | 创建绑定 (Config × Node × 远程路径) |
| `/configs/bindings/<id>/` | GET | 绑定详情 + 内容编辑器 |
| `/configs/bindings/<id>/edit/` | POST | 编辑绑定内容 → version+1 |
| `/configs/bindings/<id>/delete/` | POST | 解除绑定 |
| `/configs/bindings/<id>/versions/` | GET | 绑定的版本历史 |
| `/configs/bindings/<id>/diff/` | GET | 绑定版本间/与远程对比 |
| `/configs/bindings/<id>/drift-check/` | POST | 漂移检测（异步任务） |
| `/configs/bindings/<id>/pull/` | POST | 拉取远程覆盖本地（异步任务） |
| `/configs/discover/` | GET/POST | 配置发现（扫描远程节点） |
| `/configs/discover/import/` | POST | 导入发现的配置 |
| `/configs/api/bindings/?config_id=&node_id=&sync_status=` | GET | 绑定列表 API |

---

## 8. 业务规则

| 编号 | 规则 | 说明 |
|------|------|------|
| R1 | 绑定唯一 | 同一 Config 标签 + 同一 Node 只能有一条绑定 |
| R2 | 内容独立 | 每条绑定的 content 完全独立，不同节点同 Config 可有不同内容 |
| R3 | 版本独立 | 每条绑定有独立的版本号序列 (V1, V2, V3...) |
| R4 | 编辑即升级 | 编辑绑定内容保存后 current_version+1，状态 → modified |
| R5 | 同步判定 | synced_version == current_version 时为 synced |
| R6 | 远程路径 | 创建绑定时继承 Config.default_remote_path，可覆盖 |
| R7 | 删除级联 | 删除 Config 标签时级联删除所有绑定 |
| R8 | 异步原则 | 所有 SSH 远程操作（同步/发现/漂移检测）均走 TaskCenterTask |

---

## 9. 与旧模型的迁移路径

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 新建 `BindingVersion` 模型 | 替代 `ConfigVersion` |
| 2 | 为每条 `Config.nodes` M2M 关系创建 `ConfigNodeBinding` | 保留 remote_path 和 content |
| 3 | 迁移版本数据：`ConfigVersion` → `BindingVersion` | 按 (config, node) 匹配 |
| 4 | 废弃 `Config.content` / `Config.current_version` / `Config.sync_status` | 数据已下沉 |
| 5 | 删除 `Config.nodes` 和 `ConfigVersion` 表 | 完成迁移 |

---

## 10. 重构建议

| 编号 | 建议 | 说明 |
|------|------|------|
| S1 | Config → 纯标签 | content/version/sync_status 全部下沉到 Binding |
| S2 | Binding 内容独立 | 每个 Binding 独立编辑，独立版本号 |
| S3 | 发布中心绑定感知 | 从 Binding 读取 (content, version, remote_path) |
| S4 | 批量复制功能 | 将 web01 的 nginx.conf 内容一键复制到 web02 的绑定（快速初始化） |
| S5 | 差异对比 | 支持"同 Config 标签不同节点的内容对比" |