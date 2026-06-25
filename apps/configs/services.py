from apps.configs.models import Config, ConfigVersion, ConfigSyncSetting

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


def sync_discovered_configs(node, discovered, request_user, remark="从远程节点同步"):
    created = []
    updated = []
    skipped = []

    for item in discovered:
        if item["name"] in SKIP_FILES:
            continue

        config = Config.objects.filter(node=node, file_path=item["path"]).first()
        if not config:
            config = Config.objects.create(
                node=node,
                name=item["name"],
                file_path=item["path"],
                content=item["content"],
                current_version=1,
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
        elif config.content != item["content"]:
            new_version = config.current_version + 1
            config.name = item["name"]
            config.content = item["content"]
            config.current_version = new_version
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
        else:
            skipped.append(item["name"])

    return created, updated, skipped


def sync_selected_configs(node, selected_paths, discovered, request_user, remark="部分配置同步"):
    selected_set = set(selected_paths)
    filtered = [item for item in discovered if item["path"] in selected_set]
    return sync_discovered_configs(node, filtered, request_user, remark=remark)

