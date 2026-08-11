"""配置管理服务层 - 适配 ConfigNodeBinding 模型"""

import logging
from .models import Config, ConfigNodeBinding, BindingVersion, ConfigSyncSetting
from django.utils import timezone
from utils.setting_service import get_setting

logger = logging.getLogger(__name__)

SKIP_FILES = {"mime.types"}


def default_nginx_conf_path():
    """读取系统设置中的默认 nginx 主配置路径"""
    return get_setting("config.default_nginx_path", "/etc/nginx/nginx.conf") or "/etc/nginx/nginx.conf"


def discover_max_depth():
    """读取配置发现最大递归深度"""
    try:
        return max(1, int(get_setting("config.discover_max_depth", "3") or 3))
    except (TypeError, ValueError):
        return 3


def get_or_create_sync_setting(node, user=None):
    setting, created = ConfigSyncSetting.objects.get_or_create(
        node=node,
        defaults={"main_conf_path": default_nginx_conf_path(), "updated_by": user},
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
    # 已标记删除：本轮不重建导入，留给末尾 _cleanup_marked_deleted_bindings 清理
    marked = ConfigNodeBinding.objects.filter(
        config=config, node=node, sync_status="marked_deleted"
    ).first()
    if marked:
        return "skipped", marked

    existing = ConfigNodeBinding.objects.filter(config=config, node=node).first()

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

    deleted = _cleanup_marked_deleted_bindings(node, request_user)
    for name in deleted:
        if progress_callback:
            progress_callback("deleted", name)

    return created, updated, skipped, orphaned, deleted


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
    """清理节点上已标记删除的绑定：SSH删除远程文件后物理删除本地记录，返回已删配置名列表"""
    from utils.ssh import SSHClient

    deleted = []
    marked = ConfigNodeBinding.objects.filter(node=node, sync_status="marked_deleted")
    if not marked:
        return deleted

    credential = node.credential
    if not credential:
        logger.warning(f"节点 {node.hostname} 无SSH凭证，跳过清理标记删除的绑定")
        return deleted

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
        return deleted

    for binding in marked:
        try:
            name = binding.config.name
            success, output = ssh.execute_command(f"rm -f {binding.remote_path}")
            if success:
                binding.delete()
                deleted.append(name)
                logger.info(f"已清理标记删除绑定: {name} @ {node.hostname}")
            else:
                logger.warning(f"删除远程文件失败 {binding.remote_path}: {output}")
        except Exception as e:
            logger.error(f"清理绑定异常 {binding.config.name} @ {node.hostname}: {str(e)}")

    ssh.close()
    return deleted


def sync_selected_configs(
    node,
    selected_paths,
    discovered,
    request_user,
    remark="部分配置同步",
    progress_callback=None,
    task_id=None,
):
    """按选中路径同步配置，透传五元组结果"""
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


def preview_glob_configs(node, credential, nginx_conf_path):
    """通过 SSH 发现节点配置文件列表，供 Glob 预览使用"""
    from utils.ssh import discover_nginx_configs

    auth_kwargs = {}
    if credential.auth_type == "password":
        auth_kwargs["password"] = credential.get_password()
    else:
        auth_kwargs["private_key"] = credential.get_private_key()

    discovered, errors = discover_nginx_configs(
        node.ip, node.port, credential.username,
        nginx_conf_path=nginx_conf_path,
        max_include_depth=discover_max_depth(),
        **auth_kwargs,
    )
    files = [{"path": item["path"], "name": item["name"]} for item in discovered]
    return files, errors


def _sync_step_label(status):
    """同步进度状态中文文案"""
    return {
        "created": "新建",
        "updated": "更新",
        "deleted": "清理删除",
        "skipped": "跳过",
    }.get(status, status)


def _sync_one_node(node, task_center_id, request_user):
    """对单个节点执行发现与同步，返回结构化结果字典"""
    from django.db import close_old_connections
    from apps.nodes.services import _get_node_credential
    from apps.releases.task_cancel import is_cancelled
    from apps.releases.task_progress import _set_current_step
    from utils.ssh import discover_nginx_configs

    close_old_connections()
    result = {
        "node_id": node.id, "hostname": node.hostname, "ip": node.ip,
        "success": False, "message": "", "created": 0, "updated": 0,
        "orphaned": 0, "deleted": 0, "errors": [],
        "created_names": [], "updated_names": [], "orphaned_names": [],
        "deleted_names": [], "skipped_names": [],
    }
    hostname = node.hostname
    try:
        if node.is_locked:
            result["message"] = "节点已锁定"
            return result
        if node.status != "online":
            result["message"] = f"节点 {node.hostname} 非在线状态"
            return result
        credential = _get_node_credential(node)
        if not credential:
            result["message"] = "未配置SSH凭证"
            return result
        setting = get_or_create_sync_setting(node)
        nginx_conf_path = setting.main_conf_path or default_nginx_conf_path()
        if not nginx_conf_path:
            result["message"] = "未配置nginx路径"
            return result

        _set_current_step(task_center_id, hostname, "连接远程")
        depth = discover_max_depth()
        _set_current_step(task_center_id, hostname, "发现配置")
        if is_cancelled(task_center_id):
            result["message"] = "任务已取消"
            return result
        cancel_check = lambda: is_cancelled(task_center_id)
        if credential.auth_type == "password":
            discovered, errors = discover_nginx_configs(
                node.ip, node.port, credential.username,
                password=credential.get_password(), nginx_conf_path=nginx_conf_path,
                max_include_depth=depth,
                cancel_check=cancel_check,
            )
        else:
            discovered, errors = discover_nginx_configs(
                node.ip, node.port, credential.username,
                private_key=credential.get_private_key(), nginx_conf_path=nginx_conf_path,
                max_include_depth=depth,
                cancel_check=cancel_check,
            )

        if errors:
            mark_discovery_failed_configs(node, errors, request_user, task_id=task_center_id)
            result["errors"].extend(errors)

        def progress_callback(status, name):
            """批量同步：更新该主机精简阶段"""
            _set_current_step(
                task_center_id, hostname, f"{_sync_step_label(status)} · {name}"
            )

        if discovered:
            created, updated, skipped, orphaned, deleted = sync_discovered_configs(
                node, discovered, request_user, remark="批量节点全量同步",
                progress_callback=progress_callback,
                task_id=task_center_id,
            )
            save_sync_path(node, nginx_conf_path, request_user)
            result["created"] = len(created)
            result["updated"] = len(updated)
            result["orphaned"] = len(orphaned)
            result["deleted"] = len(deleted)
            result["created_names"] = created
            result["updated_names"] = updated
            result["orphaned_names"] = orphaned
            result["deleted_names"] = deleted
            result["skipped_names"] = skipped
        else:
            _set_current_step(task_center_id, hostname, "清理标记删除")
            deleted = _cleanup_marked_deleted_bindings(node, request_user)
            result["deleted"] = len(deleted)
            result["deleted_names"] = deleted

        if result["errors"]:
            result["message"] = "; ".join(result["errors"][:3])
            result["success"] = False
        elif not discovered:
            if result["deleted"]:
                result["success"] = True
                result["message"] = f"已删除 {result['deleted']} 个标记删除配置"
            else:
                result["message"] = "未发现配置文件"
                result["success"] = False
        else:
            result["success"] = True
            result["message"] = (
                f"已同步 {len(discovered)} 个配置文件"
                f"（新增 {result['created']}，更新 {result['updated']}，删除 {result['deleted']}）"
            )
        return result
    finally:
        _set_current_step(task_center_id, hostname, None)


def run_batch_config_sync_task(task_id, sync_nodes, request_user, max_workers):
    """批量同步线程体：并行同步节点并写入标准结果树"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading as _threading

    from django.utils import timezone
    from apps.releases.models import TaskCenterTask
    from apps.releases.task_cancel import finish_if_active, is_cancelled, update_if_active
    from apps.releases.task_progress import _clear_release_progress_state
    from apps.releases.task_result import (
        build_tree_result,
        item_failed,
        item_success,
        node_header,
    )

    total = len(sync_nodes)
    live_lock = _threading.Lock()
    live_blocks = []

    def _flush_live():
        """刷入进行中结果树供进度遮罩展示"""
        with live_lock:
            text = "\n".join(live_blocks) if live_blocks else ""
        update_if_active(task_id, result=text)

    try:
        TaskCenterTask.objects.filter(pk=task_id).update(
            status="running", started_at=timezone.now(), progress=0,
            detail=f"执行中：0/{len(sync_nodes)}",
        )
        success_count = 0
        fail_count = 0
        done = 0
        node_blocks = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_node = {
                executor.submit(_sync_one_node, node, task_id, request_user): node
                for node in sync_nodes
            }
            for future in as_completed(future_to_node):
                if is_cancelled(task_id):
                    break
                node = future_to_node[future]
                header = node_header(node.ip, node.hostname)
                node_blocks.append(header)
                chunk = [header]
                try:
                    result = future.result()
                except Exception as exc:
                    fail_count += 1
                    done += 1
                    fail_line = item_failed("同步", str(exc)[:200])
                    node_blocks.append(fail_line)
                    chunk.append(fail_line)
                    with live_lock:
                        live_blocks.extend(chunk)
                    _flush_live()
                    update_if_active(
                        task_id,
                        progress=int(done * 100 / total) if total else 100,
                        detail=f"执行中：成功 {success_count}，失败 {fail_count}，已完成 {done}/{total}",
                    )
                    continue
                done += 1
                if result["success"]:
                    success_count += 1
                    created_names = list(result.get("created_names") or [])
                    updated_names = list(result.get("updated_names") or [])
                    deleted_names = list(result.get("deleted_names") or [])
                    skipped_names = list(result.get("skipped_names") or [])
                    has_items = False
                    for name in created_names:
                        line = item_success(f"{name} (新建)")
                        node_blocks.append(line)
                        chunk.append(line)
                        has_items = True
                    for name in updated_names:
                        line = item_success(f"{name} (更新)")
                        node_blocks.append(line)
                        chunk.append(line)
                        has_items = True
                    for name in deleted_names:
                        line = item_success(f"{name} (删除)")
                        node_blocks.append(line)
                        chunk.append(line)
                        has_items = True
                    for name in skipped_names:
                        line = item_success(f"{name} (跳过)")
                        node_blocks.append(line)
                        chunk.append(line)
                        has_items = True
                    if not has_items:
                        line = item_success(result.get("message") or "同步")
                        node_blocks.append(line)
                        chunk.append(line)
                else:
                    fail_count += 1
                    deleted_names = list(result.get("deleted_names") or [])
                    for name in deleted_names:
                        line = item_success(f"{name} (删除)")
                        node_blocks.append(line)
                        chunk.append(line)
                    fail_line = item_failed("同步", result.get("message") or "失败")
                    node_blocks.append(fail_line)
                    chunk.append(fail_line)
                with live_lock:
                    live_blocks.extend(chunk)
                _flush_live()
                update_if_active(
                    task_id,
                    progress=int(done * 100 / total) if total else 100,
                    detail=f"执行中：成功 {success_count}，失败 {fail_count}，已完成 {done}/{total}",
                )

        if is_cancelled(task_id):
            return
        status = "success" if fail_count == 0 else "failed"
        finish_if_active(
            task_id,
            status=status, progress=100, finished_at=timezone.now(),
            result=build_tree_result(success_count, fail_count, total, node_blocks),
            detail=f"执行完成：成功 {success_count}，失败 {fail_count}，共 {total}",
        )
    except Exception as exc:
        # 兜底：避免任务永久停在 running 导致进度遮罩卡住
        finish_if_active(
            task_id,
            status="failed",
            progress=100,
            finished_at=timezone.now(),
            result=build_tree_result(
                0, 1, 1,
                [item_failed("同步", f"批量同步异常: {str(exc)[:200]}")],
            ),
            detail=f"同步异常: {str(exc)[:120]}",
        )
    finally:
        _clear_release_progress_state(task_id)


def run_single_config_sync_task(
    task_id,
    node,
    request_user,
    username,
    nginx_conf_path,
    auth_kwargs,
    selected_paths,
    is_partial,
):
    """单节点同步线程体：写精简阶段与活树，异常时任务标 failed"""
    from django.db import close_old_connections
    from django.utils import timezone
    from apps.releases.models import TaskCenterTask
    from apps.releases.task_cancel import finish_if_active, is_cancelled
    from apps.releases.task_progress import _set_current_step, _clear_release_progress_state
    from apps.releases.task_result import (
        build_tree_result,
        item_failed,
        item_success,
        node_header,
    )
    from utils.ssh import discover_nginx_configs

    close_old_connections()
    task = TaskCenterTask.objects.get(pk=task_id)
    hostname = node.hostname
    live_blocks = [node_header(node.ip, node.hostname)]

    def _flush_live():
        """刷入进行中结果树"""
        TaskCenterTask.objects.filter(pk=task.id).update(
            result="\n".join(live_blocks),
            updated_at=timezone.now(),
        )

    try:
        task.status = "running"
        task.started_at = timezone.now()
        task.progress = 5
        task.detail = "正在连接远程节点..."
        task.save(update_fields=["status", "started_at", "progress", "detail", "updated_at"])
        _set_current_step(task.id, hostname, "连接远程")
        _flush_live()

        _set_current_step(task.id, hostname, "发现配置")
        task.detail = "正在发现配置..."
        task.progress = 10
        task.save(update_fields=["progress", "detail", "updated_at"])

        discovered, errors = discover_nginx_configs(
            node.ip, node.port, username,
            nginx_conf_path=nginx_conf_path,
            max_include_depth=discover_max_depth(),
            cancel_check=lambda: is_cancelled(task.id),
            **auth_kwargs,
        )

        if is_cancelled(task.id):
            return

        if is_partial:
            selected_set = set(selected_paths)
            discovered = [item for item in discovered if item["path"] in selected_set]

        if errors:
            task.progress = 15
            task.detail = f"发现错误: {errors[0][:80]}"
            task.save(update_fields=["progress", "detail", "updated_at"])
            mark_discovery_failed_configs(node, errors, request_user, task_id=task.id)

        if not discovered:
            _set_current_step(task.id, hostname, "清理标记删除")
            deleted = _cleanup_marked_deleted_bindings(node, request_user)
            item_ok = 0
            item_fail = 0
            for name in deleted:
                live_blocks.append(item_success(f"{name} (删除)"))
                item_ok += 1
            if errors:
                reason = "未发现配置文件；" + "；".join(errors[:3])
                live_blocks.append(item_failed("同步", reason))
                item_fail = 1
                task.status = "failed"
                task.detail = (
                    f"同步失败: 0 新增, 0 更新, {len(deleted)} 删除"
                )
            elif deleted:
                task.status = "success"
                task.detail = (
                    f"同步完成: 0 新增, 0 更新, {len(deleted)} 删除"
                )
            else:
                live_blocks.append(item_failed("同步", "未发现配置文件"))
                item_fail = 1
                task.status = "failed"
                task.detail = "同步失败：未发现配置文件"
            task.progress = 100
            task.finished_at = timezone.now()
            task.result = build_tree_result(
                item_ok, item_fail, item_ok + item_fail, live_blocks,
            )
            task.save(update_fields=[
                "status", "progress", "finished_at", "result", "detail", "updated_at",
            ])
            return

        total = len(discovered)
        processed = 0

        def progress_callback(status, name):
            nonlocal processed
            processed += 1
            pct = min(99, 15 + int(processed * 85 / max(total, 1)))
            label = _sync_step_label(status)
            _set_current_step(task.id, hostname, f"{label} · {name}")
            # 活树仅追加新建/更新/删除，跳过不落树
            if status == "created":
                live_blocks.append(item_success(f"{name} (新建)"))
            elif status == "updated":
                live_blocks.append(item_success(f"{name} (更新)"))
            elif status == "deleted":
                live_blocks.append(item_success(f"{name} (删除)"))
            TaskCenterTask.objects.filter(pk=task.id).update(
                progress=pct,
                detail=f"{label}: {name} ({processed}/{total})",
                result="\n".join(live_blocks),
                updated_at=timezone.now(),
            )

        if is_partial:
            created, updated, skipped, orphaned, deleted = sync_selected_configs(
                node, selected_paths, discovered, request_user,
                remark="单节点部分同步", progress_callback=progress_callback,
                task_id=task.id,
            )
        else:
            created, updated, skipped, orphaned, deleted = sync_discovered_configs(
                node, discovered, request_user, remark="单节点全量同步",
                progress_callback=progress_callback, task_id=task.id,
            )

        save_sync_path(node, nginx_conf_path, request_user)

        success = len(errors) == 0
        node_blocks = [node_header(node.ip, node.hostname)]
        item_ok = 0
        item_fail = 0
        for name in created or []:
            node_blocks.append(item_success(f"{name} (新建)"))
            item_ok += 1
        for name in updated or []:
            node_blocks.append(item_success(f"{name} (更新)"))
            item_ok += 1
        for name in deleted or []:
            node_blocks.append(item_success(f"{name} (删除)"))
            item_ok += 1
        for name in skipped or []:
            node_blocks.append(item_success(f"{name} (跳过)"))
            item_ok += 1
        for err in errors or []:
            node_blocks.append(item_failed("同步", err))
            item_fail += 1
        if item_ok == 0 and item_fail == 0:
            node_blocks.append(item_success(f"共发现 {total} 个配置文件"))
            item_ok = 1

        finish_if_active(
            task.id,
            status="success" if success else "failed",
            progress=100,
            finished_at=timezone.now(),
            result=build_tree_result(item_ok, item_fail, item_ok + item_fail, node_blocks),
            detail=(
                f"同步{'完成' if success else '失败'}: "
                f"{len(created)} 新增, {len(updated)} 更新, {len(deleted)} 删除"
            ),
        )
    except Exception as exc:
        # 兜底：避免任务永久停在 running 导致进度遮罩卡住
        finish_if_active(
            task.id,
            status="failed",
            progress=100,
            finished_at=timezone.now(),
            result=build_tree_result(
                0, 1, 1,
                [
                    node_header(node.ip, node.hostname),
                    item_failed("同步", f"同步异常: {str(exc)[:200]}"),
                ],
            ),
            detail=f"同步异常: {str(exc)[:120]}",
        )
    finally:
        _clear_release_progress_state(task.id)
