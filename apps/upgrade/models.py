"""Nginx 编译升级模块 - 数据模型"""
import hashlib
from django.db import models
from django.contrib.auth import get_user_model
from apps.nodes.models import Node

User = get_user_model()


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
        help_text='JSON 列表，如 [{"name":"headers-more","version":"v0.37"}]',
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

    def save(self, *args, **kwargs):
        """保存时自动计算文件大小和 MD5"""
        super().save(*args, **kwargs)
        if self.package_file and (not self.file_size or not self.file_md5):
            try:
                self.package_file.seek(0)
                content = self.package_file.read()
                self.file_size = len(content)
                self.file_md5 = hashlib.md5(content).hexdigest()
                self.package_file.seek(0)
                super().save(update_fields=["file_size", "file_md5"])
            except Exception:
                pass


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

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="状态"
    )
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

    def __str__(self):
        return f"[{self.get_status_display()}] {self.node.hostname} → nginx-{self.target_version}"