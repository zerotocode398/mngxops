# MngxOps 需求文档 - 10 Nginx 编译升级

> **功能模块**: Nginx 编译升级（Nginx Upgrade）  
> **URL**: `/upgrade/`  
> **模型**: `apps/upgrade/models.py` → `NginxSourcePackage`, `NginxUpgradeTask`（新模块）  
> **状态**: 规划中，暂未实现  
> **说明**: 平台上传源码包 → SFTP 分发到目标节点 → 基于 `nginx -V` 现有编译参数增量调整 → 远程编译 → 平滑升级

---

## 1. 功能概述

Nginx 编译升级模块的核心设计理念是 **"延续现有编译参数 + 按需增量调整"**，而非从零选择模块。运维人员通过 Web 界面完成：

- **源码包管理**：平台本地上传 nginx 源码包（`.tar.gz`），存储到 Django Media，升级时自动 SFTP 分发到目标节点
- **编译参数继承**：通过 SSH 执行 `nginx -V` 获取当前运行版本的完整编译参数，作为基础配置
- **增量调整**：在现有参数基础上增加/移除模块，而非重新勾选所有模块
- **远程编译**：在目标节点执行 configure → make → 替换二进制
- **平滑升级**：USR2+WINCH+QUIT 三信号实现零停机切换
- **升级回滚**：保留旧版本二进制，支持快速回滚

---

## 2. 核心设计差异

与常见方案对比，MngxOps 的方案有两个关键区别：

| 对比项 | 常见方案 | MngxOps 方案 |
|--------|---------|-------------|
| 编译参数 | 从空白开始勾选模块 | **继承** `nginx -V` 的现有参数，在此基础上增减 |
| 源码包 | wget 从官网下载 | **平台本地上传**，统一管理版本，SFTP 分发到目标节点 |
| 目标路径 | 固定 `/usr/local/nginx` | 从 `nginx -V` 的 `--prefix` 自动识别，**可手动覆盖** |

---

## 3. 数据模型

### 3.1 NginxSourcePackage（源码包）

```python
def nginx_package_upload_path(instance, filename):
    """上传路径：media/nginx_packages/nginx-1.26.1.tar.gz"""
    return f"nginx_packages/{filename}"


class NginxSourcePackage(models.Model):
    """Nginx 源码包 - 平台上传，统一管理"""

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    name = models.CharField(max_length=100, verbose_name="包名称")
    version = models.CharField(max_length=20, verbose_name="Nginx 版本号")
    package_file = models.FileField(
        upload_to=nginx_package_upload_path,
        verbose_name="源码包文件",
        help_text="支持 .tar.gz / .tgz 格式",
    )
    file_size = models.BigIntegerField(default=0, verbose_name="文件大小（字节）")
    file_md5 = models.CharField(max_length=64, blank=True, verbose_name="文件 MD5")

    # 元信息
    description = models.TextField(blank=True, verbose_name="描述")
    is_official = models.BooleanField(default=False, verbose_name="是否官方包")
    custom_modules_included = models.TextField(
        blank=True,
        verbose_name="预置第三方模块",
        help_text="JSON 列表，如 [{\"name\":\"headers-more\",\"version\":\"v0.37\"}]",
    )

    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="上传人")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="上传时间")

    class Meta:
        verbose_name = "Nginx 源码包"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]
        unique_together = [["version", "uploaded_by"]]

    def __str__(self):
        return f"nginx-{self.version} ({self.name})"
```

### 3.2 NginxUpgradeTask（升级任务）

```python
class NginxUpgradeTask(models.Model):
    """Nginx 升级/安装任务"""

    STATUS_CHOICES = (
        ("pending", "等待执行"),
        ("fetching_config", "获取当前编译参数"),
        ("uploading_package", "上传源码包到节点"),
        ("downloading_modules", "下载第三方模块"),
        ("configuring", "执行 configure"),
        ("compiling", "执行 make"),
        ("backing_up", "备份旧版本"),
        ("replacing_binary", "替换二进制文件"),
        ("upgrading", "平滑升级中"),
        ("verifying", "验证中"),
        ("success", "升级成功"),
        ("failed", "升级失败"),
        ("rollback", "已回滚"),
        ("cancelled", "已取消"),
    )

    UPGRADE_MODE_CHOICES = (
        ("install", "全新安装（无旧版本）"),
        ("upgrade", "平滑升级（同路径）"),
        ("switch_path", "切换路径升级（不同 --prefix）"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    node = models.ForeignKey(Node, on_delete=models.CASCADE, verbose_name="目标节点")
    source_package = models.ForeignKey(
        NginxSourcePackage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="源码包",
    )

    upgrade_mode = models.CharField(
        max_length=20,
        choices=UPGRADE_MODE_CHOICES,
        default="upgrade",
        verbose_name="升级模式",
    )

    # 远程路径配置
    remote_work_dir = models.CharField(
        max_length=500,
        default="/tmp/nginx-upgrade",
        verbose_name="节点上的编译工作目录",
        help_text="源码包将上传到此目录，编译也在此进行",
    )

    # 编译参数 - 从 nginx -V 获取并调整
    current_version = models.CharField(max_length=50, blank=True, verbose_name="当前版本")
    current_configure_opts = models.TextField(
        blank=True,
        verbose_name="当前编译参数（nginx -V）",
        help_text="从目标节点自动获取的原始编译参数",
    )
    current_configure_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="当前 --prefix 路径",
    )
    current_binary_path = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="当前 nginx 二进制路径",
    )

    # 调整后的编译参数
    target_version = models.CharField(max_length=20, verbose_name="目标版本")
    target_configure_opts = models.TextField(
        blank=True,
        verbose_name="调整后的编译参数",
        help_text="基于 current_configure_opts 调整后的最终参数",
    )
    target_prefix = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="目标 --prefix",
    )

    # 模块增减（增量记录）
    added_modules = models.TextField(
        default="[]",
        verbose_name="新增的编译参数",
        help_text='JSON列表，如 ["--with-http_v3_module"]',
    )
    removed_modules = models.TextField(
        default="[]",
        verbose_name="移除的编译参数",
        help_text='JSON列表，如 ["--with-mail"]',
    )
    added_third_party = models.TextField(
        default="[]",
        verbose_name="新增第三方模块",
        help_text='JSON列表，如 [{"name":"echo-nginx","git_url":"...","branch":"v0.63"}]',
    )

    # 编译选项
    make_jobs = models.IntegerField(default=4, verbose_name="并行编译数 (-j)")
    old_binary_backup = models.CharField(max_length=500, blank=True, verbose_name="旧二进制备份路径")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="状态")
    progress = models.IntegerField(default=0, verbose_name="进度百分比")
    current_step = models.CharField(max_length=255, blank=True, verbose_name="当前步骤")
    log_output = models.TextField(blank=True, verbose_name="完整输出日志")
    error_message = models.TextField(blank=True, verbose_name="错误信息")

    operator = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="upgrade_tasks", verbose_name="操作人",
    )
    task_center = models.ForeignKey(
        "releases.TaskCenterTask", on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name="任务中心关联",
    )
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "Nginx 升级任务"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]
```

---

## 4. 页面设计

### 4.1 源码包管理页

```
┌──────────────────────────────────────────────────────────────┐
│  📦 Nginx 源码包管理                           [ + 上传源码包 ]│
│                                                              │
│  ┌─ 数据表格 ────────────────────────────────────────────┐  │
│  │ 包名称     │版本  │文件大小│MD5前8位│上传时间│操作     │  │
│  │───────────┼─────┼───────┼───────┼───────┼────────│  │
│  │官方标准包  │1.26.1│1.2 MB │a3f2b...│07-10  │删除 下载│  │
│  │官方标准包  │1.24.0│1.1 MB │b7c8d...│07-08  │删除 下载│  │
│  │定制版(SSL)│1.26.1│1.3 MB │e1d4f...│07-05  │删除 下载│  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 分页 ────────────────────────────────────────────────┐  │
│  │ 共 3 条  [1]                                           │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 源码包上传页

```
┌──────────────────────────────────────────────────────────────┐
│  📤 上传 Nginx 源码包                         [← 返回列表]    │
│                                                              │
│  ┌─ 上传 ────────────────────────────────────────────────┐  │
│  │                                                        │  │
│  │  ┌──────────────────────────────────────────────┐     │  │
│  │  │                                              │     │  │
│  │  │       📁 点击或拖拽文件到此处上传              │     │  │
│  │  │          支持 .tar.gz / .tgz 格式              │     │  │
│  │  │          最大 500MB                            │     │  │
│  │  │                                              │     │  │
│  │  └──────────────────────────────────────────────┘     │  │
│  │                                                        │  │
│  │  已选择: nginx-1.26.1.tar.gz (1.2 MB)                   │  │
│  │                                                        │     │
│  │  包名称 *  ┌──────────────────────────────────┐       │  │
│  │            │ 官方标准包 1.26.1                │       │  │
│  │            └──────────────────────────────────┘       │  │
│  │                                                        │  │
│  │  版本号 *  ┌──────────────────────────────────┐       │  │
│  │            │ 1.26.1  (自动从文件名提取)        │       │  │
│  │            └──────────────────────────────────┘       │  │
│  │                                                        │  │
│  │  描述      ┌──────────────────────────────────┐       │  │
│  │            │ 从 nginx.org 下载的官方稳定版     │       │  │
│  │            └──────────────────────────────────┘       │  │
│  │                                                        │  │
│  │  ☑ 标记为官方包（非定制版本）                            │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                              │
│  [📤 上传并校验]  [❌ 取消]                                  │
└──────────────────────────────────────────────────────────────┘
```

**上传流程**:
1. 文件拖拽或点击选择 `.tar.gz` 文件
2. 自动提取文件名中的版本号（如 `nginx-1.26.1.tar.gz` → `1.26.1`）
3. 上传后计算文件大小和 MD5，存储到 Django Media 目录
4. MD5 用于后续 SFTP 分发的完整性校验

### 4.3 升级任务创建页（核心）

```
┌──────────────────────────────────────────────────────────────┐
│  🚀 Nginx 升级中心                                            │
│                                                              │
│  ┌─ 第1步：选择目标节点 ────────────────────────────────┐   │
│  │ ○ web01 (10.0.1.1) 🟢在线 生产  nginx/1.24.0          │   │
│  │ ○ web02 (10.0.1.2) 🟢在线 生产  nginx/1.24.0          │   │
│  │ (单选)  [🔍 搜索节点...]  [环境 ▼]                     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─ 第2步：选择源码包 ──────────────────────────────────┐   │
│  │ ○ 官方标准包 - nginx/1.26.1 (上传于 07-10)            │   │
│  │ ○ 官方标准包 - nginx/1.24.0 (上传于 07-08)            │   │
│  │ ○ 定制版(SSL) - nginx/1.26.1 (上传于 07-05)          │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─ 第3步：获取当前编译参数并调整 ⬅ 核心差异 ════════════┐   │
│  │                                                        │   │
│  │  ┌─ 目标节点当前编译参数（nginx -V 自动获取）──────┐   │   │
│  │  │ ✅ 获取成功 (web01 -- 2026-07-10 11:00)        │   │   │
│  │  │                                                │   │   │
│  │  │ nginx version: nginx/1.24.0                    │   │   │
│  │  │ configure arguments:                           │   │   │
│  │  │   --prefix=/usr/local/nginx                    │   │   │
│  │  │   --with-http_ssl_module                       │   │   │
│  │  │   --with-http_v2_module                        │   │   │
│  │  │   --with-http_stub_status_module               │   │   │
│  │  │   --with-stream                                │   │   │
│  │  │   --with-stream_ssl_module                     │   │   │
│  │  │   --add-module=/path/to/headers-more            │   │   │
│  │  └────────────────────────────────────────────────┘   │   │
│  │                                                        │   │
│  │  ┌─ 调整编译参数 ──────────────────────────────────┐   │   │
│  │  │ 当前参数将作为基础配置，您可以在其基础上增减模块:   │   │   │
│  │  │                                                   │   │   │
│  │  │ ➕ 新增内置模块:                                    │   │   │
│  │  │   ☐ --with-http_v3_module                         │   │   │
│  │  │   ☐ --with-http_realip_module                     │   │   │
│  │  │   ☐ --with-http_gunzip_module                     │   │   │
│  │  │   ...更多模块...                                    │   │   │
│  │  │                                                   │   │   │
│  │  │ ➖ 移除现有参数:   (勾选将从编译参数中移除)          │   │   │
│  │  │   ☐ --with-mail                                   │   │   │
│  │  │   ☐ --with-stream                                 │   │   │
│  │  │                                                   │   │   │
│  │  │ ➕ 新增第三方模块:                                  │   │   │
│  │  │   [+ 添加模块]                                    │   │   │
│  │  │   模块名: [echo-nginx    ]                         │   │   │
│  │  │   Git仓库: [https://github.com/.../echo-nginx]     │   │   │
│  │  │   分支/标签: [v0.63       ]                        │   │   │
│  │  │                                                   │   │   │
│  │  │ 目标 --prefix:                                     │   │   │
│  │  │   ┌──────────────────────────────────────────┐    │   │   │
│  │  │   │ /usr/local/nginx  (从 nginx -V 自动获取)  │    │   │   │
│  │  │   └──────────────────────────────────────────┘    │   │   │
│  │  └───────────────────────────────────────────────────┘   │   │
│  │                                                           │   │
│  │  ┌─ 调整后的最终编译命令预览 ────────────────────────┐    │   │
│  │  │ ./configure \                                     │    │   │
│  │  │   --prefix=/usr/local/nginx \                     │    │   │
│  │  │   --with-http_ssl_module \                        │    │   │
│  │  │   --with-http_v2_module \                         │    │   │
│  │  │   --with-http_v3_module \     ← 新增               │    │   │
│  │  │   --with-http_stub_status_module \                │    │   │
│  │  │   --with-stream_ssl_module \                      │    │   │
│  │  │   --add-module=/path/to/headers-more \            │    │   │
│  │  │   --add-module=/tmp/nginx-modules/echo-nginx ←新增│    │   │
│  │  └───────────────────────────────────────────────────┘    │   │
│  │                                                            │   │
│  │  [🔄 重新获取 nginx -V]                                    │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌─ 第4步：目标路径配置 ─────────────────────────────────┐   │
│  │                                                        │  │
│  │  编译工作目录  ┌──────────────────────────────────┐    │  │
│  │  (远程)        │ /tmp/nginx-upgrade               │    │  │
│  │                └──────────────────────────────────┘    │  │
│  │                SFTP 上传源码包到此目录，编译也在此进行   │  │
│  │                                                        │  │
│  │  并行编译数    ┌─────┐                                  │  │
│  │  (-j)         │  4  │                                  │  │
│  │               └─────┘                                  │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 第5步：确认升级 ────────────────────────────────────┐   │
│  │ ┌─ 升级摘要 ─────────────────────────────────────┐    │   │
│  │ │ 目标节点:  web01 (10.0.1.1)                     │    │   │
│  │ │ 当前版本:  nginx/1.24.0                         │    │   │
│  │ │ 目标版本:  nginx/1.26.1                         │    │   │
│  │ │ 源码包:    官方标准包 (nginx-1.26.1.tar.gz)      │    │   │
│  │ │ 安装路径:  /usr/local/nginx (继承自 nginx -V)    │    │   │
│  │ │ 新增模块:  --with-http_v3_module, echo-nginx     │    │   │
│  │ │ 移除参数:  无                                    │    │   │
│  │ │ 远程工作目录: /tmp/nginx-upgrade                  │    │   │
│  │ └───────────────────────────────────────────────┘    │   │
│  │                                                       │   │
│  │ 升级前检查:                                            │   │
│  │   ☑ 备份当前二进制 (→ nginx.old.{timestamp})           │   │
│  │   ☑ 编译后执行 nginx -t 语法检查                        │   │
│  │   ☑ 升级后验证新版本号                                  │   │
│  │                                                       │   │
│  │ [🚀 开始升级]                                          │   │
│  └─────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### 4.4 升级执行进度页

步骤流程改为：

```
┌─ Nginx 升级执行中 ──────────────────────────────────┐
│  📦 节点: web01 (10.0.1.1)                           │
│  🎯 目标: nginx/1.26.1                               │
│                                                      │
│  ┌─ 执行进度 ████████████████░░ 73% ──────────────┐  │
│                                                      │
│  ✅ 1. 连接节点 & 环境检查 (gcc/make/pcre)  (1.5s)    │
│  ✅ 2. 获取当前 nginx -V 编译参数           (0.3s)    │
│  ✅ 3. SFTP 上传源码包到 /tmp/nginx-upgrade  (3.2s)   │
│  ✅ 4. 远程解压源码包                       (1.1s)    │
│  ✅ 5. 下载第三方模块 (git clone)            (5.8s)    │
│  ✅ 6. 备份旧二进制 → nginx.old.timestamp     (0.2s)    │
│  ✅ 7. 执行 ./configure {final_params}       (2.8s)    │
│  🔄 8. 执行 make -j4                         (32.5s)   │
│  ⏳ 9. 替换二进制文件                                 │
│  ⏳ 10. 执行 nginx -t 语法检查                         │
│  ⏳ 11. 平滑升级 (USR2+WINCH+QUIT)                    │
│  ⏳ 12. 验证版本 & 运行状态                            │
│                                                      │
│  [⏹ 取消升级]  [📋 查看完整日志]                      │
└──────────────────────────────────────────────────────┘
```

---

## 5. 完整升级流程详解

```
1. 平台侧准备
   ├── 运维人员上传 nginx-{version}.tar.gz 到平台 Media 目录
   ├── 平台记录文件大小 + MD5
   └── 创建升级任务：选择节点 + 源码包

2. 连接节点 & 获取现有配置
   ├── SSH 连接目标节点
   ├── 执行 nginx -V 2>&1 获取当前编译参数
   ├── 解析输出，提取：
   │   ├── nginx version: nginx/1.24.0
   │   ├── configure arguments: --prefix=... --with-... --add-module=...
   │   ├── --prefix 路径
   │   └── 二进制路径（通常为 {prefix}/sbin/nginx）
   └── 页面展示解析结果，运维人员在此基础上增减模块

3. SFTP 上传源码包到目标节点
   ├── 从 Django Media 读取源码包文件
   ├── SFTP 上传到目标节点的 {remote_work_dir}/nginx-{version}.tar.gz
   ├── 上传后校验 MD5（与平台记录对比）
   └── tar -xzf nginx-{version}.tar.gz -C {remote_work_dir}/

4. 下载第三方模块（如有新增）
   ├── git clone {repo_url} -b {branch} {remote_work_dir}/nginx-modules/{name}
   └── 失败则中止任务（允许重试）

5. 备份当前版本
   ├── cp {prefix}/sbin/nginx → {prefix}/sbin/nginx.old.{timestamp}
   └── (可选) cp -r {prefix}/conf → {backup_dir}/conf.{timestamp}

6. 编译
   ├── cd {remote_work_dir}/nginx-{version}
   ├── ./configure {final_params}
   │   (继承 current_configure_opts + added_modules - removed_modules)
   ├── make -j{n}
   └── (注意：只 make，不 make install，避免覆盖现有配置)

7. 替换二进制 & 验证
   ├── cp {remote_work_dir}/nginx-{version}/objs/nginx → {prefix}/sbin/nginx
   ├── {prefix}/sbin/nginx -t   (语法检查，失败则回滚)
   └── {prefix}/sbin/nginx -v   (版本验证)

8. 平滑升级（零停机）
   ├── kill -USR2 `cat {prefix}/logs/nginx.pid`
   │   → 启动新 master (新 pid → nginx.pid, 旧 pid → nginx.pid.oldbin)
   ├── kill -WINCH `cat {prefix}/logs/nginx.pid.oldbin`
   │   → 优雅关闭旧 worker (旧 master 保留，可回滚)
   └── kill -QUIT `cat {prefix}/logs/nginx.pid.oldbin`
       → 彻底退出旧 master

9. 最终验证
   ├── {prefix}/sbin/nginx -v
   ├── curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1
   └── ps aux | grep nginx

10. 回滚（如需要）
    ├── 若旧 master 仍在:
    │   ├── kill -HUP `cat {prefix}/logs/nginx.pid.oldbin`  (唤醒旧 worker)
    │   └── kill -QUIT `cat {prefix}/logs/nginx.pid`        (关闭新 master)
    └── 若旧 master 已退出:
        └── cp {prefix}/sbin/nginx.old.{timestamp} → {prefix}/sbin/nginx
            && {prefix}/sbin/nginx -s reload
```

---

## 6. API接口

| URL | 方法 | 说明 |
|-----|------|------|
| `/upgrade/packages/` | GET | 源码包列表页 |
| `/upgrade/packages/upload/` | POST | 上传源码包 |
| `/upgrade/packages/<id>/delete/` | POST | 删除源码包 |
| `/upgrade/packages/<id>/download/` | GET | 下载源码包 |
| `/upgrade/center/` | GET | 升级中心页面 |
| `/upgrade/api/nginx-v/<node_id>/` | POST | 获取目标节点 nginx -V 输出 (Ajax) |
| `/upgrade/api/parse-config/` | POST | 解析 nginx -V 输出为结构化参数 (Ajax) |
| `/upgrade/create/` | POST | 创建升级任务 (Ajax) |
| `/upgrade/<id>/progress/` | GET | 获取升级进度 (Ajax 轮询) |
| `/upgrade/<id>/cancel/` | POST | 取消升级任务 |
| `/upgrade/<id>/rollback/` | POST | 回滚升级 |
| `/upgrade/<id>/log/` | GET | 获取完整编译日志 |
| `/upgrade/history/` | GET | 升级历史列表 |

---

## 7. 业务规则

| 编号 | 规则 | 说明 |
|------|------|------|
| R1 | 参数继承 | 编译参数**必须**从 `nginx -V` 获取作为基础，不允许从空白开始配置 |
| R2 | 路径继承 | `--prefix` 默认使用 `nginx -V` 中的值，可手动修改（修改后升级模式变为 switch_path） |
| R3 | 包校验 | 源码包上传时计算 MD5，SFTP 分发后远端校验 MD5，不一致则中止 |
| R4 | 编译隔离 | 编译在 `{remote_work_dir}` 独立目录进行，不污染现有 nginx 目录 |
| R5 | 只 make 不 install | 永远不执行 `make install`，手动 `cp objs/nginx` 替换二进制，保护现有配置文件 |
| R6 | 语法检查 | 替换二进制后必须执行 `nginx -t`，失败立即回滚 |
| R7 | 平滑切换 | 使用 USR2+WINCH+QUIT 三信号零停机升级 |
| R8 | 回滚窗口 | USR2 后旧 master 保留，可随时回滚（手动或超时自动） |
| R9 | 锁定保护 | 已锁定节点不允许升级 |
| R10 | 权限控制 | `upgrade.create` 可创建升级任务，`upgrade.update` 可回滚 |
| R11 | 连接要求 | 目标节点必须在线且已配置有效 SSH 凭证 |
| R12 | 环境依赖 | 编译前检查 gcc/make/pcre-devel/zlib-devel/openssl-devel，缺失则提示安装 |

---

## 8. 权限扩展

在 `apps/users/perm_defs.py` 中新增：

```python
# 资源新增
("upgrade", "Nginx升级"),

# 权限定义
"upgrade": {
    "read": "升级历史查看",
    "create": "创建升级任务",
    "update": "回滚升级",
    "delete": "删除升级记录",
}
```

---

## 9. 样式规范

### 9.1 编译参数展示区（终端风格）

```css
.nginx-v-output {
  background: #1a1a2e;
  color: #00ff88;
  font-family: 'Cascadia Code', 'Courier New', monospace;
  font-size: 13px;
  line-height: 1.6;
  padding: 14px 18px;
  border-radius: 8px;
  max-height: 300px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.nginx-v-output .param-highlight {
  color: #ffd166;
  background: rgba(255, 209, 102, 0.1);
  padding: 1px 4px;
  border-radius: 3px;
}
```

### 9.2 参数对比预览

```css
.config-diff-preview {
  border: 1px solid #e9ecef;
  border-radius: 8px;
  overflow: hidden;
}
.config-diff-preview .param-kept    { background: #f8f9fa; color: #495057; }
.config-diff-preview .param-added   { background: #e6ffed; color: #155724; }
.config-diff-preview .param-removed { background: #ffeef0; color: #721c24; text-decoration: line-through; }
```

### 9.3 文件上传区域

```css
.upload-dropzone {
  border: 2px dashed #667eea;
  border-radius: 12px;
  padding: 40px;
  text-align: center;
  background: #f0f3ff;
  transition: all 0.3s;
  cursor: pointer;
}
.upload-dropzone:hover, .upload-dropzone.drag-over {
  background: #e8ecff;
  border-color: #4a5fd7;
}
```

### 9.4 升级步骤状态

```css
.upgrade-step {
  display: flex;
  align-items: center;
  padding: 8px 12px;
  border-radius: 6px;
  margin-bottom: 4px;
  font-family: 'Courier New', monospace;
  font-size: 13px;
}
.upgrade-step.success { background: #f0fff4; color: #155724; }
.upgrade-step.running { background: #fff8e1; color: #856404; animation: pulse 1.5s infinite; }
.upgrade-step.failed  { background: #fff5f5; color: #721c24; }
.upgrade-step.pending { background: #f8f9fa; color: #6c757d; }
```

---

## 10. 与系统设置的联动

以下配置项应加入系统设置 (`09_system_settings.md`)：

| key | type | 默认值 | 说明 |
|-----|------|--------|------|
| `upgrade.default_work_dir` | string | `/tmp/nginx-upgrade` | 默认编译工作目录 |
| `upgrade.make_jobs_default` | integer | 4 | 默认并行编译数 (-j) |
| `upgrade.package_max_size_mb` | integer | 500 | 源码包上传大小限制 (MB) |
| `upgrade.oldbin_keep_seconds` | integer | 60 | 旧 master 进程保留时间（秒） |

---

## 11. 重构建议

| 编号 | 建议 | 说明 |
|------|------|------|
| S1 | `nginx -V` 解析器 | 封装为独立服务函数，将字符串解析为 `{"--prefix": "...", "--with-*": [...], "--add-module": [...]}` |
| S2 | 同版本重编译 | 同版本号只改模块参数时，skip 下载解压，直接用已存在的源码目录 |
| S3 | 编译缓存 | 同版本+同参数在多节点间复用编译产物（需架构一致） |
| S4 | dry-run 模式 | 只模拟流程不实际执行，输出预期操作列表供审核 |
| S5 | 批量升级 | 同源码包+同参数可一键批量应用到多个节点 |