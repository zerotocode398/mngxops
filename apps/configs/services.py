"""配置管理服务层 - 适配 ConfigNodeBinding 模型"""

import logging
from .models import Config, ConfigNodeBinding, BindingVersion, ConfigSyncSetting
from django.utils import timezone

logger = logging.getLogger(__name__)

SKIP_FILES = {"mime.types"}


def get_or_create_sync_setting(node, user=None):
    setting, created = ConfigSyncSetting.objects.get_or_create(
        node=node,
        defaults={"main_conf_path": "/etc/nginx/nginx.conf", "updated_by": user},
    )
    return setting


def save_sync_path(node, main_conf_path, user=None):
    setting, _ = ConfigSyncSetting.objects.get_or_create(
        node=node,
        defaults={"main_conf_path": main_conf_path, "updated_by": user},
    )
    if setting.main_conf_path != main_conf_path or setting.updated_by != user:
        setting.main_conf_path = main_conf_path
        setting.updated_by = user
        setting.save(update_fields=["main_conf_path", "updated_by", "updated_at"])
    return setting


def _ensure_binding(config, node, remote_path, content, request_user, source="discovered", task_id=None):
    """确保绑定存在并更新内容，已标记删除的绑定会被跳过"""
    now = timezone.now()
    existing = ConfigNodeBinding.objects.filter(
        config=config, node=node
    ).exclude(sync_status="marked_deleted").first()

    if existing:
        binding = existing
        created = False
    else:
        binding = ConfigNodeBinding.objects.create(
            config=config,
            node=node,
            remote_path=remote_path,
            content=content,
            current_version=1,
            sync_status="synced",
            synced_version=1,
            last_sync_time=now,
            last_sync_task_id=task_id,
            source=source,
            created_by=request_user,
        )
        created = True

    if created:
        BindingVersion.objects.create(
            binding=binding,
            version=1,
            content=content,
            remark="发现导入" if source == "discovered" else "手动导入",
            created_by=request_user,
        )
        return "created", binding

    # 已存在，检查内容是否变化
    if binding.content != content:
        new_version = binding.current_version + 1
        binding.content = content
        binding.current_version = new_version
        binding.sync_status = "synced"
        binding.synced_version = new_version
        binding.last_sync_time = now
        binding.last_sync_task_id = task_id
        binding.remote_path = remote_path
        binding.save()
        BindingVersion.objects.create(
            binding=binding,
            version=new_version,
            content=content,
            remark="远程同步更新",
            created_by=request_user,
        )
        return "updated", binding
    else:
        # 内容未变，更新同步状态
        binding.sync_status = "synced"
        binding.last_sync_time = now
        binding.last_sync_task_id = task_id
        binding.remote_path = remote_path
        binding.save(update_fields=["sync_status", "last_sync_time", "last_sync_task_id", "remote_path", "updated_at"])
        return "skipped", binding


def sync_discovered_configs(
    node,
    discovered,
    request_user,
    remark="从远程节点同步",
    mark_orphaned=True,
    progress_callback=None,
    task_id=None,
):
    """同步发现的配置到绑定"""
    created = []
    updated = []
    skipped = []

    for item in discovered:
        if item["name"] in SKIP_FILES:
            if progress_callback:
                progress_callback("skipped", item["name"])
            continue

        # 查找或创建 Config 标签
        config = Config.objects.filter(name=item["name"]).first()
        if not config:
            config = Config.objects.create(
                name=item["name"],
                default_remote_path=item["path"],
                source="discovered",
                created_by=request_user,
            )

        status, _ = _ensure_binding(
            config=config,
            node=node,
            remote_path=item["path"],
            content=item["content"],
            request_user=request_user,
            source="discovered",
            task_id=task_id,
        )

        if status == "created":
            created.append(item["name"])
            if progress_callback:
                progress_callback("created", item["name"])
        elif status == "updated":
            updated.append(item["name"])
            if progress_callback:
                progress_callback("updated", item["name"])
        else:
            skipped.append(item["name"])
            if progress_callback:
                progress_callback("skipped", item["name"])

    orphaned = []
    if mark_orphaned:
        discovered_paths = {item["path"] for item in discovered}
        orphaned = _mark_orphaned_bindings(node, discovered_paths)

    _cleanup_marked_deleted_bindings(node, request_user)

    return created, updated, skipped, orphaned


def _mark_orphaned_bindings(node, discovered_paths):
    """标记远程已删除的绑定"""
    orphaned = []
    bindings = ConfigNodeBinding.objects.filter(
        node=node, sync_status="synced"
    ).exclude(remote_path__in=discovered_paths)

    for binding in bindings:
        binding.sync_status = "orphaned"
        binding.save(update_fields=["sync_status", "updated_at"])
        orphaned.append(binding.config.name)

    return orphaned


def _cleanup_marked_deleted_bindings(node, request_user):
    """清理节点上已标记删除的绑定：SSH删除远程文件后物理删除本地记录"""
    from utils.ssh import SSHClient

    marked = ConfigNodeBinding.objects.filter(node=node, sync_status="marked_deleted")
    if not marked:
        return

    credential = node.credential
    if not credential:
        logger.warning(f"节点 {node.hostname} 无SSH凭证，跳过清理标记删除的绑定")
        return

    auth_kwargs = {}
    if credential.auth_type == "password":
        auth_kwargs["password"] = credential.get_password()
    else:
        auth_kwargs["private_key"] = credential.get_private_key()

    ssh = SSHClient(node.ip, node.port, credential.username, **auth_kwargs)
    ok, err = ssh.connect()
    if not ok:
        logger.warning(f"SSH连接 {node.hostname} 失败，跳过清理: {err}")
        ssh.close()
        return

    for binding in marked:
        try:
            success, output = ssh.execute_command(f"rm -f {binding.remote_path}")
            if success:
                binding.delete()
                logger.info(f"已清理标记删除绑定: {binding.config.name} @ {node.hostname}")
            else:
                logger.warning(f"删除远程文件失败 {binding.remote_path}: {output}")
        except Exception as e:
            logger.error(f"清理绑定异常 {binding.config.name} @ {node.hostname}: {str(e)}")

    ssh.close()


def sync_selected_configs(
    node,
    selected_paths,
    discovered,
    request_user,
    remark="部分配置同步",
    progress_callback=None,
    task_id=None,
):
    selected_set = set(selected_paths)
    filtered = [item for item in discovered if item["path"] in selected_set]
    return sync_discovered_configs(
        node,
        filtered,
        request_user,
        remark=remark,
        mark_orphaned=False,
        progress_callback=progress_callback,
        task_id=task_id,
    )


def mark_sync_failed(node, error_message):
    failed = []
    bindings = ConfigNodeBinding.objects.filter(node=node).exclude(
        sync_status__in=["orphaned", "not_synced"]
    )

    for binding in bindings:
        binding.sync_status = "failed"
        binding.last_sync_error = error_message
        binding.save(update_fields=["sync_status", "last_sync_error", "updated_at"])
        failed.append(binding.config.name)

    return failed


def mark_discovery_failed_configs(node, errors, request_user=None, task_id=None):
    import re
    from django.utils import timezone

    failed = []
    pattern = re.compile(r"读取 (.+?) 失败:")
    for error in errors:
        match = pattern.match(error)
        if not match:
            continue
        failed_path = match.group(1)
        failed_name = failed_path.split("/")[-1]

        config = Config.objects.filter(name=failed_name).first()
        if not config and request_user:
            config = Config.objects.create(
                name=failed_name,
                default_remote_path=failed_path,
                source="discovered",
                created_by=request_user,
            )

        if config:
            binding, created = ConfigNodeBinding.objects.get_or_create(
                config=config,
                node=node,
                defaults={
                    "remote_path": failed_path,
                    "content": "",
                    "current_version": 0,
                    "sync_status": "failed",
                    "last_sync_error": error,
                    "last_sync_time": timezone.now(),
                    "last_sync_task_id": task_id,
                    "source": "discovered",
                    "created_by": request_user or node.created_by,
                },
            )
            if not created:
                binding.sync_status = "failed"
                binding.last_sync_error = error
                binding.last_sync_task_id = task_id
                binding.save(update_fields=["sync_status", "last_sync_error", "last_sync_task_id", "updated_at"])
            failed.append(failed_name)

    return failed