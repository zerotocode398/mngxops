from apps.configs.models import Config, ConfigVersion, ConfigSyncSetting
from django.utils import timezone

SKIP_FILES = {"mime.types"}


def get_or_create_sync_setting(node, user=None):
    setting, created = ConfigSyncSetting.objects.get_or_create(
        node=node,
        defaults={"main_conf_path": "", "updated_by": user},
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


def sync_discovered_configs(
    node,
    discovered,
    request_user,
    remark="从远程节点同步",
    mark_orphaned=True,
    progress_callback=None,
):
    created = []
    updated = []
    skipped = []

    now = timezone.now()

    for item in discovered:
        if item["name"] in SKIP_FILES:
            if progress_callback:
                progress_callback("skipped", item["name"])
            continue

        config = Config.objects.filter(node=node, file_path=item["path"]).first()
        if not config:
            config = Config.objects.create(
                node=node,
                name=item["name"],
                file_path=item["path"],
                content=item["content"],
                current_version=1,
                sync_status="success",
                last_sync_time=now,
                created_by=request_user,
            )
            ConfigVersion.objects.create(
                config=config,
                version=1,
                content=item["content"],
                remark=remark,
                created_by=request_user,
            )
            created.append(item["name"])
            if progress_callback:
                progress_callback("created", item["name"])
        elif config.content != item["content"]:
            new_version = config.current_version + 1
            config.name = item["name"]
            config.content = item["content"]
            config.current_version = new_version
            config.sync_status = "success"
            config.last_sync_time = now
            config.save()
            ConfigVersion.objects.create(
                config=config,
                version=new_version,
                content=item["content"],
                remark=remark,
                created_by=request_user,
            )
            config.prune_old_versions()
            updated.append(item["name"])
            if progress_callback:
                progress_callback("updated", item["name"])
        else:
            config.sync_status = "success"
            config.last_sync_time = now
            config.save(update_fields=["sync_status", "last_sync_time"])
            skipped.append(item["name"])
            if progress_callback:
                progress_callback("skipped", item["name"])

    orphaned = []
    if mark_orphaned:
        discovered_paths = {item["path"] for item in discovered}
        orphaned = _mark_orphaned_configs(node, discovered_paths)

    return created, updated, skipped, orphaned


def _mark_orphaned_configs(node, discovered_paths):
    orphaned = []
    stale_configs = Config.objects.filter(node=node, sync_status="success").exclude(
        file_path__in=discovered_paths
    )

    for config in stale_configs:
        config.sync_status = "orphaned"
        config.save(update_fields=["sync_status", "updated_at"])
        orphaned.append(config.name)

    return orphaned


def sync_selected_configs(
    node,
    selected_paths,
    discovered,
    request_user,
    remark="部分配置同步",
    progress_callback=None,
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
    )


def mark_sync_failed(node, error_message):
    failed = []
    now = timezone.now()

    configs = Config.objects.filter(node=node).exclude(
        sync_status__in=["orphaned", "pending"]
    )

    for config in configs:
        config.sync_status = "failed"
        config.last_sync_error = error_message
        config.save(update_fields=["sync_status", "last_sync_error", "updated_at"])
        failed.append(config.name)

    return failed


def mark_discovery_failed_configs(node, errors, request_user=None):
    import re
    from django.utils import timezone

    failed = []
    pattern = re.compile(r"读取 (.+?) 失败:")
    for error in errors:
        match = pattern.match(error)
        if not match:
            continue
        failed_path = match.group(1)
        config = Config.objects.filter(node=node, file_path=failed_path).first()
        if config:
            config.sync_status = "failed"
            config.last_sync_error = error
            config.save(update_fields=["sync_status", "last_sync_error", "updated_at"])
            failed.append(config.name)
        elif request_user:
            failed_name = failed_path.split("/")[-1]
            Config.objects.create(
                node=node,
                name=failed_name,
                file_path=failed_path,
                content="",
                current_version=0,
                sync_status="failed",
                last_sync_error=error,
                last_sync_time=timezone.now(),
                created_by=request_user,
            )
            failed.append(failed_name)
    return failed
