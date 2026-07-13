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


# 预置配置项
PRESET_SETTINGS = [
    # 仪表盘
    {"key": "dashboard.recent_tasks_count", "group": "仪表盘", "type": "integer", "value": "10",
     "label": "最近发布任务显示条数", "description": "仪表盘首页最近发布任务列表最大行数", "sort_order": 1},
    {"key": "dashboard.recent_failed_bindings_count", "group": "仪表盘", "type": "integer", "value": "10",
     "label": "配置下发失败显示条数", "description": "仪表盘首页同步失败配置告警最大行数", "sort_order": 2},
    # 节点管理
    {"key": "node.batch_max_count", "group": "节点管理", "type": "integer", "value": "3",
     "label": "批量操作最大节点数", "description": "节点批量测试/同步的最大节点数", "sort_order": 10},
    {"key": "node.ssh_connect_timeout", "group": "节点管理", "type": "integer", "value": "10",
     "label": "SSH 连接超时（秒）", "description": "所有 SSH 远程操作的连接超时时间", "sort_order": 11},
    {"key": "node.ssh_default_port", "group": "节点管理", "type": "integer", "value": "22",
     "label": "SSH 默认端口", "description": "新建节点时的默认 SSH 端口", "sort_order": 12},
    {"key": "node.detect_retries", "group": "节点管理", "type": "integer", "value": "1",
     "label": "节点探测重试次数", "description": "状态检测失败时的重试次数", "sort_order": 13},
    # 凭证管理
    {"key": "credential.test_max_concurrency", "group": "凭证管理", "type": "integer", "value": "10",
     "label": "凭证测试最大并发数", "description": "凭证启用批量测试的最大并发数", "sort_order": 20},
    # 配置管理
    {"key": "config.discover_max_depth", "group": "配置管理", "type": "integer", "value": "3",
     "label": "配置发现最大递归深度", "description": "远程 nginx 配置文件扫描的最大 include 递归层次", "sort_order": 30},
    {"key": "config.default_nginx_path", "group": "配置管理", "type": "string", "value": "/etc/nginx/nginx.conf",
     "label": "默认 nginx 主配置路径", "description": "配置发现时的默认 nginx 主配置文件路径", "sort_order": 31},
    {"key": "config.version_retention_days", "group": "配置管理", "type": "integer", "value": "180",
     "label": "配置版本保留天数", "description": "超过此天数的历史版本将被自动清理", "sort_order": 32},
    {"key": "config.sync_max_concurrency", "group": "配置管理", "type": "integer", "value": "3",
     "label": "配置同步最大并发节点数", "description": "批量同步时的最大并发节点数", "sort_order": 33},
    {"key": "config.sync_cache_timeout", "group": "配置管理", "type": "integer", "value": "300",
     "label": "同步进度缓存超时（秒）", "description": "同步进度缓存的有效期", "sort_order": 34},
    # 发布管理
    {"key": "release.single_node_timeout", "group": "发布管理", "type": "integer", "value": "60",
     "label": "单节点发布超时（秒）", "description": "单个节点发布任务的超时时间", "sort_order": 40},
    {"key": "release.max_parallel_tasks", "group": "发布管理", "type": "integer", "value": "3",
     "label": "最大并行任务数", "description": "批量发布/同步时 ThreadPoolExecutor 的最大 worker 数", "sort_order": 41},
    {"key": "release.backup_dir", "group": "发布管理", "type": "string", "value": "/opt/app/mascloud/ansible/mngxops",
     "label": "远程配置备份目录", "description": "配置发布前在远程节点上备份配置文件的目录", "sort_order": 42},
    {"key": "release.history_retention_days", "group": "发布管理", "type": "integer", "value": "90",
     "label": "发布历史保留天数", "description": "超过此天数的发布历史记录将被自动清理", "sort_order": 43},
    # 审计日志
    {"key": "audit.operation_log_retention_days", "group": "审计日志", "type": "integer", "value": "365",
     "label": "操作日志保留天数", "description": "操作日志的保留期限，超过自动清理", "sort_order": 50},
    {"key": "audit.login_log_retention_days", "group": "审计日志", "type": "integer", "value": "180",
     "label": "登录日志保留天数", "description": "登录日志的保留期限", "sort_order": 51},
    {"key": "audit.login_max_fail_count", "group": "审计日志", "type": "integer", "value": "5",
     "label": "登录失败锁定阈值", "description": "连续登录失败次数达到后临时锁定", "sort_order": 52},
    {"key": "audit.login_lock_minutes", "group": "审计日志", "type": "integer", "value": "30",
     "label": "登录锁定时间（分钟）", "description": "达到失败阈值后的临时锁定时间", "sort_order": 53},
    # 系统
    {"key": "system.task_progress_poll_interval", "group": "系统", "type": "integer", "value": "2",
     "label": "任务进度轮询间隔（秒）", "description": "前端轮询任务进度的间隔时间", "sort_order": 60},
    {"key": "system.dashboard_refresh_interval", "group": "系统", "type": "integer", "value": "30",
     "label": "仪表盘自动刷新间隔（秒）", "description": "仪表盘统计卡片自动刷新间隔", "sort_order": 61},
    {"key": "task_center.retention_days", "group": "任务中心", "type": "integer", "value": "30",
     "label": "任务中心记录保留天数", "description": "任务中心记录保留天数，超过自动清理", "sort_order": 70},
    # Nginx 编译升级
    {"key": "upgrade.default_work_dir", "group": "Nginx升级", "type": "string", "value": "/tmp/nginx-upgrade",
     "label": "默认编译工作目录", "description": "远程节点上执行编译操作的临时目录", "sort_order": 80},
    {"key": "upgrade.make_jobs_default", "group": "Nginx升级", "type": "integer", "value": "4",
     "label": "默认并行编译数 (-j)", "description": "make 编译时的默认并行任务数", "sort_order": 81},
    {"key": "upgrade.package_max_size_mb", "group": "Nginx升级", "type": "integer", "value": "500",
     "label": "源码包上传大小限制 (MB)", "description": "上传 Nginx 源码包的最大文件大小限制", "sort_order": 82},
    {"key": "upgrade.oldbin_keep_seconds", "group": "Nginx升级", "type": "integer", "value": "60",
     "label": "旧 master 进程保留时间（秒）", "description": "平滑升级后旧 master 进程的最大保留时间", "sort_order": 83},
]