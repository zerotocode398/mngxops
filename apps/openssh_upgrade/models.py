"""OpenSSH 升级/回滚模块 - 数据模型"""
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from apps.nodes.models import Node


def generate_openssh_batch_number(prefix="OSI"):
    """生成 OpenSSH 升级批次号，格式 OSI-YYMMDD-NNNN / OSR-YYMMDD-NNNN（当日自增）"""
    today = timezone.now().strftime("%y%m%d")
    batch_prefix = f"{prefix}-{today}-"
    with transaction.atomic():
        last = (
            OpenSSHUpgradeTask.objects.select_for_update()
            .filter(batch_number__startswith=batch_prefix)
            .order_by("-batch_number")
            .first()
        )
        if last and last.batch_number:
            seq = int(last.batch_number[-4:]) + 1
        else:
            seq = 1
        return f"{batch_prefix}{seq:04d}"


def generate_openssh_upgrade_batch_number():
    """升级批次号 OSI-YYMMDD-NNNN"""
    return generate_openssh_batch_number("OSI")


def generate_openssh_rollback_batch_number():
    """回滚批次号 OSR-YYMMDD-NNNN"""
    return generate_openssh_batch_number("OSR")


def openssh_package_upload_path(instance, filename):
    """上传路径：media/openssh_packages/openssh-9.8p1.tar.gz"""
    return f"openssh_packages/{filename}"


class OpenSSHSourcePackage(models.Model):
    """OpenSSH 源码包 - 平台上传，统一管理"""

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    name = models.CharField(max_length=100, verbose_name="包名称")
    version = models.CharField(max_length=50, verbose_name="OpenSSH 版本号")
    package_file = models.FileField(
        upload_to=openssh_package_upload_path,
        verbose_name="源码包文件",
        help_text="支持 .tar.gz / .tgz 格式",
    )
    file_size = models.BigIntegerField(default=0, verbose_name="文件大小（字节）")
    file_md5 = models.CharField(max_length=64, blank=True, verbose_name="文件 MD5")

    description = models.TextField(blank=True, verbose_name="描述")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="上传人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="上传时间")

    class Meta:
        verbose_name = "OpenSSH 源码包"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]
        unique_together = [["version", "uploaded_by"]]

    def __str__(self):
        return f"openssh-{self.version} ({self.name})"

    def save(self, *args, **kwargs):
        """保存时自动计算文件大小和 MD5"""
        super().save(*args, **kwargs)
        if self.package_file and (not self.file_size or not self.file_md5):
            try:
                import hashlib

                self.package_file.seek(0)
                content = self.package_file.read()
                self.file_size = len(content)
                self.file_md5 = hashlib.md5(content).hexdigest()
                self.package_file.seek(0)
                super().save(update_fields=["file_size", "file_md5"])
            except Exception:
                pass


class OpenSSHUpgradeTask(models.Model):
    """单节点 OpenSSH 升级/回滚任务"""

    ACTION_CHOICES = (
        ("upgrade", "OpenSSH 升级"),
        ("rollback", "OpenSSH 回滚"),
    )

    STATUS_CHOICES = (
        ("pending", "等待执行"),
        ("probing", "预检探测"),
        ("building", "上传编译"),
        ("verifying", "预验证新二进制"),
        ("backing_up", "备份旧版本"),
        ("switching", "切换并重启 sshd"),
        ("confirming", "连接实证"),
        ("success", "升级/回滚成功"),
        ("failed", "失败"),
        ("rolled_back", "已自动回滚"),
        ("cancelled", "已取消"),
    )

    id = models.BigAutoField(primary_key=True, verbose_name="ID")
    action = models.CharField(
        max_length=20, choices=ACTION_CHOICES, default="upgrade", verbose_name="操作",
    )
    batch_number = models.CharField(
        max_length=32, blank=True, db_index=True, verbose_name="批次号",
        help_text="升级 OSI-YYMMDD-NNNN；回滚 OSR-YYMMDD-NNNN",
    )
    node = models.ForeignKey(Node, on_delete=models.CASCADE, verbose_name="目标节点")
    source_package = models.ForeignKey(
        OpenSSHSourcePackage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="源码包",
    )

    current_version = models.CharField(max_length=50, blank=True, verbose_name="当前版本")
    target_version = models.CharField(max_length=50, blank=True, verbose_name="目标版本")
    configure_opts = models.TextField(blank=True, verbose_name="configure 参数")
    target_prefix = models.CharField(
        max_length=500, blank=True, verbose_name="目标安装前缀 (--prefix)",
    )
    make_jobs = models.IntegerField(default=4, verbose_name="并行编译数 (-j)")
    remote_work_dir = models.CharField(
        max_length=500, default="/tmp/openssh-upgrade", verbose_name="节点编译工作目录",
    )
    test_port = models.IntegerField(default=2222, verbose_name="预验证备用端口")
    reconnect_grace_seconds = models.IntegerField(
        default=60, verbose_name="看门狗回滚宽限（秒）",
    )
    auto_rollback = models.BooleanField(default=True, verbose_name="失败自动回滚")

    # 探测/执行期运行时字段
    binaries = models.JSONField(default=dict, blank=True, verbose_name="二进制路径映射")
    is_root = models.BooleanField(default=False, verbose_name="是否 root")
    use_sudo = models.BooleanField(default=False, verbose_name="使用免密 sudo")
    manage_mode = models.CharField(
        max_length=20, default="binary", verbose_name="托管方式",
        help_text="systemctl | binary",
    )
    manage_unit = models.CharField(max_length=50, blank=True, verbose_name="托管 unit")
    sshd_config_path = models.CharField(
        max_length=500, default="/etc/ssh/sshd_config", verbose_name="sshd 配置路径",
    )
    sshd_port = models.IntegerField(default=22, verbose_name="sshd 监听端口")
    sshd_binary = models.CharField(max_length=500, blank=True, verbose_name="sshd 二进制路径")
    home_dir = models.CharField(max_length=255, blank=True, verbose_name="远程主目录")

    # 回滚材料（备份/看门狗/标记）
    backup_dir = models.CharField(max_length=500, blank=True, verbose_name="备份根目录")
    backup_manifest_json = models.TextField(default="{}", verbose_name="备份清单 JSON")
    rollback_script_path = models.CharField(
        max_length=500, blank=True, verbose_name="远端回滚脚本路径",
    )
    ok_marker = models.CharField(max_length=500, blank=True, verbose_name="成功标记路径")
    rolled_back_marker = models.CharField(
        max_length=500, blank=True, verbose_name="回滚标记路径",
    )

    # 升级成功后回写
    upgraded_openssh_version = models.CharField(
        max_length=50, blank=True, verbose_name="升级后版本（已实证）",
    )

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="状态",
    )
    progress = models.IntegerField(default=0, verbose_name="进度百分比")
    current_step = models.CharField(max_length=255, blank=True, verbose_name="当前步骤")
    log_output = models.TextField(blank=True, verbose_name="完整输出日志")
    error_message = models.TextField(blank=True, verbose_name="错误信息")

    task_center = models.ForeignKey(
        "releases.TaskCenterTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="openssh_upgrade_tasks",
        verbose_name="关联任务中心",
    )
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="操作人",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="完成时间")

    class Meta:
        verbose_name = "OpenSSH 升级任务"
        verbose_name_plural = verbose_name
        ordering = ["-created_at"]

    def __str__(self):
        act = self.get_action_display()
        return f"{act} {self.node_id} ({self.status})"