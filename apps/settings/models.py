"""系统设置模块 - 数据模型"""
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


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
        max_length=20, choices=TYPE_CHOICES, default="string", verbose_name="值类型",
    )
    group = models.CharField(max_length=50, verbose_name="配置分组")
    label = models.CharField(max_length=100, verbose_name="显示名称")
    description = models.TextField(blank=True, verbose_name="说明")
    placeholder = models.CharField(max_length=255, blank=True, verbose_name="占位提示")
    options = models.TextField(blank=True, verbose_name="可选值JSON")
    is_required = models.BooleanField(default=True, verbose_name="必填")
    sort_order = models.IntegerField(default=0, verbose_name="排序")
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, verbose_name="最后修改人",
    )
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "系统设置"
        verbose_name_plural = verbose_name
        ordering = ["group", "sort_order"]

    def __str__(self):
        return f"{self.key} = {self.value}"


# 预置配置项（仅保留已接线、保存后立即生效的参数）
# 整数项可选 min_value / max_value，供保存与设置页范围校验（不落库）
PRESET_SETTINGS = [
    # 仪表盘
    {"key": "dashboard.recent_tasks_count", "group": "仪表盘", "type": "integer", "value": "20",
     "label": "最近任务显示条数",
     "description": "仪表盘与 Nginx 升级/安装/启停首页「最近任务」列表最大行数（刷新对应页面后生效）",
     "sort_order": 1, "min_value": 1, "max_value": 100},
    # 节点管理
    {"key": "node.batch_max_count", "group": "节点管理", "type": "integer", "value": "3",
     "label": "批量操作最大节点数",
     "description": "节点批量测试/解锁、Nginx 启停等操作的最大节点数（后端立即生效；页面勾选上限需刷新对应页面）",
     "sort_order": 10, "min_value": 1, "max_value": 50},
    {"key": "node.ssh_connect_timeout", "group": "节点管理", "type": "integer", "value": "10",
     "label": "SSH 连接超时（秒）",
     "description": "所有 SSH 远程操作的连接超时时间（下次连接立即生效）",
     "sort_order": 11, "min_value": 1, "max_value": 120},
    {"key": "node.ssh_default_port", "group": "节点管理", "type": "integer", "value": "22",
     "label": "SSH 默认端口",
     "description": "仅影响新建节点/批量导入时的默认 SSH 端口，已有节点不改动",
     "sort_order": 12, "min_value": 1, "max_value": 65535},
    {"key": "node.detect_retries", "group": "节点管理", "type": "integer", "value": "1",
     "label": "节点探测重试次数",
     "description": "SSH 连接失败时的额外重试次数（不含首次；下次连接立即生效）",
     "sort_order": 13, "min_value": 0, "max_value": 10},
    # 凭证管理
    {"key": "credential.test_max_concurrency", "group": "凭证管理", "type": "integer", "value": "10",
     "label": "凭证测试最大并发数",
     "description": "凭证启用批量测试的最大并发数（下次启用测试立即生效）",
     "sort_order": 20, "min_value": 1, "max_value": 100},
    # 配置管理
    {"key": "config.discover_max_depth", "group": "配置管理", "type": "integer", "value": "3",
     "label": "配置发现最大递归深度",
     "description": "远程 nginx 配置文件扫描的最大 include 递归层次（下次发现/同步立即生效）",
     "sort_order": 30, "min_value": 1, "max_value": 20},
    {"key": "config.default_nginx_path", "group": "配置管理", "type": "string", "value": "/etc/nginx/nginx.conf",
     "label": "默认 nginx 主配置路径",
     "description": "仅用于新建/空同步设置的默认主配置路径；节点已有 main_conf_path 时不改动",
     "sort_order": 31},
    {"key": "config.default_nginx_bin", "group": "配置管理", "type": "string", "value": "/usr/sbin/nginx",
     "label": "默认 Nginx 可执行文件路径",
     "description": "仅影响新建/批量导入节点时 Nginx 路径未填的默认值（与主配置路径区分）",
     "sort_order": 32},
    {"key": "config.sync_max_concurrency", "group": "配置管理", "type": "integer", "value": "3",
     "label": "配置同步最大并发节点数",
     "description": "批量同步时的最大并发节点数（后端立即生效；向导勾选上限需刷新同步页）",
     "sort_order": 33, "min_value": 1, "max_value": 50},
    # 发布管理
    {"key": "release.max_parallel_tasks", "group": "发布管理", "type": "integer", "value": "3",
     "label": "最大并行任务数",
     "description": "批量发布/回滚时 ThreadPoolExecutor 的最大 worker 数（下次发布立即生效）",
     "sort_order": 41, "min_value": 1, "max_value": 50},
    {"key": "release.backup_dir", "group": "发布管理", "type": "string", "value": "/opt/app/mascloud/ansible/mngxops",
     "label": "远程配置备份目录",
     "description": "配置发布前在远程节点上备份的根目录，实际路径为 {backup_dir}/{节点hostname}/（下次发布立即生效）",
     "sort_order": 42},
    # 系统
    {"key": "system.task_progress_poll_interval", "group": "系统", "type": "integer", "value": "2",
     "label": "任务进度轮询间隔（秒）",
     "description": "前端轮询任务进度的间隔时间（刷新页面后生效）",
     "sort_order": 60, "min_value": 1, "max_value": 60},
    {"key": "system.dashboard_refresh_interval", "group": "系统", "type": "integer", "value": "30",
     "label": "仪表盘自动刷新间隔（秒）",
     "description": "仪表盘统计卡片自动刷新间隔（刷新页面后生效）",
     "sort_order": 61, "min_value": 5, "max_value": 3600},
    {"key": "system.retention_task_center_days", "group": "系统", "type": "integer", "value": "90",
     "label": "任务中心保留天数",
     "description": "超过天数的任务中心记录将在次日自动清理时删除；0 表示不清理",
     "sort_order": 62, "min_value": 0, "max_value": 3650},
    {"key": "system.retention_release_history_days", "group": "系统", "type": "integer", "value": "90",
     "label": "发布历史保留天数",
     "description": "超过天数的发布任务记录将在次日自动清理时删除；0 表示不清理",
     "sort_order": 63, "min_value": 0, "max_value": 3650},
    {"key": "system.retention_audit_log_days", "group": "系统", "type": "integer", "value": "90",
     "label": "操作日志保留天数",
     "description": "超过天数的操作日志将在次日自动清理时删除；0 表示不清理",
     "sort_order": 64, "min_value": 0, "max_value": 3650},
    {"key": "system.retention_login_log_days", "group": "系统", "type": "integer", "value": "90",
     "label": "登录日志保留天数",
     "description": "超过天数的登录日志将在次日自动清理时删除；0 表示不清理",
     "sort_order": 65, "min_value": 0, "max_value": 3650},
    {"key": "system.retention_upgrade_task_days", "group": "系统", "type": "integer", "value": "90",
     "label": "Nginx 升级任务保留天数",
     "description": "超过天数的 Nginx 升级任务记录将在次日自动清理时删除；0 表示不清理",
     "sort_order": 66, "min_value": 0, "max_value": 3650},
    # Nginx 编译升级
    {"key": "upgrade.default_work_dir", "group": "Nginx升级", "type": "string", "value": "/tmp/nginx-upgrade",
     "label": "默认编译工作目录",
     "description": "升级中心高级选项默认远程编译目录",
     "sort_order": 80},
    {"key": "upgrade.make_jobs_default", "group": "Nginx升级", "type": "integer", "value": "4",
     "label": "默认并行编译数 (-j)",
     "description": "升级中心高级选项默认 make -j",
     "sort_order": 81, "min_value": 1, "max_value": 32},
    {"key": "upgrade.package_max_size_mb", "group": "Nginx升级", "type": "integer", "value": "20",
     "label": "源码包/第三方模块包上传大小限制 (MB)",
     "description": "源码包与第三方模块包上传共用此上限；上传校验立即按新限制生效",
     "sort_order": 82, "min_value": 1, "max_value": 2048},
    # Nginx 全新安装
    {"key": "install.default_user", "group": "安装管理", "type": "string", "value": "root",
     "label": "用户 (--user)",
     "description": "仅作 Nginx 安装向导右栏缺省；向导内可改；保存后刷新安装向导生效",
     "sort_order": 90},
    {"key": "install.default_group", "group": "安装管理", "type": "string", "value": "root",
     "label": "用户组 (--group)",
     "description": "仅作 Nginx 安装向导右栏缺省；向导内可改；保存后刷新安装向导生效",
     "sort_order": 91},
    {"key": "install.default_prefix", "group": "安装管理", "type": "string", "value": "/opt/app",
     "label": "默认安装路径 (--prefix)",
     "description": "仅作 Nginx 安装向导右栏缺省；向导内可改；保存后刷新安装向导生效",
     "sort_order": 92},
    {"key": "install.default_listen_port", "group": "安装管理", "type": "integer", "value": "80",
     "label": "默认监听端口 (listen)",
     "description": "安装完成后写入主配置 listen 的缺省端口；向导内可改；非 root SSH 使用 80 时在编译参数下一步告警；保存后刷新安装向导生效",
     "sort_order": 93, "min_value": 1, "max_value": 65535},
]


def preset_key_set():
    """返回当前 PRESET 中的全部 key 集合"""
    return {item["key"] for item in PRESET_SETTINGS}


def preset_by_key():
    """返回 key → PRESET 项映射"""
    return {item["key"]: item for item in PRESET_SETTINGS}
