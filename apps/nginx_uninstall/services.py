"""Nginx 卸载：路径解析、安全校验与后台执行流水线。"""
import json
import logging
import re
import shlex
import threading

from django.utils import timezone

from apps.nodes.models import Node
from apps.releases.models import TaskCenterTask
from apps.releases.task_cancel import finish_if_active, is_cancelled, update_if_active
from apps.releases.task_progress import _clear_release_progress_state, _set_current_step
from apps.releases.task_result import (
    build_tree_result,
    item_failed,
    item_success,
    node_header,
)
from utils.nginx_ops import is_nginx_running, stop_nginx
from utils.setting_service import get_setting
from utils.ssh import SSHClient, _safe_backup_hostname

logger = logging.getLogger(__name__)

# 禁止作为卸载目标的过短/系统根路径
_FORBIDDEN_PATHS = frozenset({
    "",
    "/",
    "/usr",
    "/usr/local",
    "/etc",
    "/var",
    "/home",
    "/opt",
    "/tmp",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/boot",
    "/root",
    "/dev",
    "/proc",
    "/sys",
    "/run",
    "/media",
    "/mnt",
})


def batch_max_count():
    """读取批量操作最大节点数"""
    try:
        return max(1, int(get_setting("node.batch_max_count", "3") or 3))
    except (TypeError, ValueError):
        return 3


def _default_work_dir():
    """读取默认编译工作目录"""
    return (
        get_setting("upgrade.default_work_dir", "/tmp/nginx-upgrade")
        or "/tmp/nginx-upgrade"
    )


def _default_backup_dir():
    """读取远程发布备份根目录"""
    return (
        get_setting("release.backup_dir", "/opt/app/mascloud/ansible/mngxops")
        or "/opt/app/mascloud/ansible/mngxops"
    )


def normalize_remote_path(path):
    """规范化远程绝对路径（去尾部斜杠，保留根 /）"""
    raw = (path or "").strip()
    if not raw:
        return ""
    if not raw.startswith("/"):
        return ""
    cleaned = re.sub(r"/+", "/", raw)
    if cleaned != "/" and cleaned.endswith("/"):
        cleaned = cleaned.rstrip("/")
    return cleaned


def is_dangerous_path(path):
    """判断路径是否禁止删除（过短系统根等）"""
    p = normalize_remote_path(path)
    if not p:
        return True
    if p in _FORBIDDEN_PATHS:
        return True
    # 至少两级路径，如 /opt/app
    parts = [x for x in p.split("/") if x]
    if len(parts) < 2:
        return True
    return False


def derive_prefix_from_nginx_path(nginx_path):
    """由二进制路径推导 prefix（…/sbin/nginx → …）"""
    p = normalize_remote_path(nginx_path)
    if not p:
        return ""
    if p.endswith("/sbin/nginx"):
        return p[: -len("/sbin/nginx")] or ""
    if p.endswith("/nginx"):
        parent = p.rsplit("/", 1)[0]
        if parent.endswith("/sbin"):
            return parent[: -len("/sbin")] or ""
    return ""


def resolve_prefix_for_node(node):
    """按优先级解析节点 --prefix：-V → nginx_path → 最近成功安装。

    Returns:
        (prefix, source_label, error_message)
    """
    # 1) nginx -V
    try:
        from apps.upgrade.services import fetch_nginx_v_from_node

        ok, parsed = fetch_nginx_v_from_node(node)
        if ok and isinstance(parsed, dict):
            prefix = normalize_remote_path(parsed.get("prefix") or "")
            if prefix and not is_dangerous_path(prefix):
                return prefix, "nginx -V", ""
    except Exception:
        logger.exception("解析节点 %s nginx -V 失败", getattr(node, "id", None))

    # 2) Node.nginx_path 推导
    derived = derive_prefix_from_nginx_path(node.nginx_path or "")
    if derived and not is_dangerous_path(derived):
        return derived, "nginx_path", ""

    # 3) 最近成功安装任务
    try:
        from apps.nginx_install.models import NginxInstallTask

        last = (
            NginxInstallTask.objects.filter(node_id=node.id, status="success")
            .order_by("-finished_at", "-id")
            .first()
        )
        if last:
            prefix = normalize_remote_path(last.target_prefix or "")
            if prefix and not is_dangerous_path(prefix):
                return prefix, "install_task", ""
    except Exception:
        logger.exception("读取节点 %s 安装历史失败", getattr(node, "id", None))

    return "", "", "无法解析安装路径，请手工填写合法 --prefix"


def backup_subdir_for_node(node):
    """组装该节点远程发布备份子目录路径"""
    root = normalize_remote_path(_default_backup_dir())
    host = _safe_backup_hostname(node.hostname)
    if not root or is_dangerous_path(root):
        # 根目录本身过短时仍允许拼子目录，但需整体通过校验
        pass
    path = f"{root.rstrip('/')}/{host}"
    return normalize_remote_path(path)


def uninstall_gate_message(node):
    """返回禁止卸载的原因；允许时返回 None。"""
    if node.is_locked:
        return "节点已锁定"
    if node.status != "online":
        return "节点非在线状态"
    cred = node.credential
    if not cred:
        return "未配置凭证"
    if not cred.is_enabled:
        return "凭证已禁用"
    # online +（Nginx 可用 或 仍有 nginx_path）才可卸
    if node.nginx_available is True:
        return None
    if (node.nginx_path or "").strip():
        return None
    return "未检测到 Nginx 且无已知安装路径"


def _auth_kwargs(credential):
    """按凭证类型组装认证参数"""
    if credential.auth_type == "password":
        return {"password": credential.get_password()}
    return {"private_key": credential.get_private_key()}


def _parse_options(raw):
    """解析删除选项 JSON"""
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
    return {
        "remove_backup": bool(data.get("remove_backup", True)),
        "remove_workdir": bool(data.get("remove_workdir", False)),
        "remove_modules": bool(data.get("remove_modules", False)),
        "stop_if_running": bool(data.get("stop_if_running", True)),
    }


def preview_nodes(node_ids):
    """预览选中节点的卸载路径与运行状态。

    Returns:
        dict: success / nodes / message
    """
    if not node_ids:
        return {"success": False, "message": "未选择任何节点"}
    try:
        node_ids = [int(x) for x in node_ids]
    except (TypeError, ValueError):
        return {"success": False, "message": "节点 ID 无效"}

    max_batch = batch_max_count()
    if len(node_ids) > max_batch:
        return {"success": False, "message": f"最多只能操作 {max_batch} 个节点"}

    nodes = list(
        Node.objects.filter(id__in=node_ids, is_deleted=False)
        .select_related("credential")
        .order_by("id")
    )
    if not nodes:
        return {"success": False, "message": "节点不存在"}

    work_dir = normalize_remote_path(_default_work_dir())
    items = []
    for node in nodes:
        gate = uninstall_gate_message(node)
        prefix, source, err = ("", "", "")
        running = False
        running_error = ""
        if not gate:
            prefix, source, err = resolve_prefix_for_node(node)
            cred = node.credential
            try:
                running = is_nginx_running(
                    node.ip,
                    node.port,
                    cred.username,
                    nginx_path=node.nginx_path or None,
                    **_auth_kwargs(cred),
                )
            except Exception as exc:
                running_error = str(exc)
        backup_path = backup_subdir_for_node(node)
        items.append({
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "nginx_path": node.nginx_path or "",
            "nginx_available": node.nginx_available,
            "eligible": gate is None and bool(prefix) and not is_dangerous_path(prefix),
            "gate_message": gate or err or "",
            "prefix": prefix,
            "prefix_source": source,
            "backup_path": backup_path,
            "work_dir": work_dir,
            "modules_dir": f"{work_dir.rstrip('/')}/nginx-modules" if work_dir else "",
            "running": running,
            "running_error": running_error,
        })

    return {
        "success": True,
        "nodes": items,
        "defaults": {
            "remove_backup": True,
            "remove_workdir": False,
            "remove_modules": False,
            "work_dir": work_dir,
        },
    }


def create_uninstall_batch_from_data(user, data):
    """校验并创建卸载批次，启动后台线程。

    Returns:
        dict: API 响应字段
    """
    from apps.audit.utils import log_task_center_created

    from .models import NginxUninstallTask, generate_uninstall_batch_number

    items = data.get("nodes") or []
    if not isinstance(items, list) or not items:
        return {"success": False, "message": "请选择至少一个节点"}

    options = _parse_options({
        "remove_backup": data.get("remove_backup", True),
        "remove_workdir": data.get("remove_workdir", False),
        "remove_modules": data.get("remove_modules", False),
        "stop_if_running": data.get("stop_if_running", True),
    })
    work_dir = normalize_remote_path(
        (data.get("work_dir") or "").strip() or _default_work_dir()
    )

    max_batch = batch_max_count()
    if len(items) > max_batch:
        return {"success": False, "message": f"最多只能操作 {max_batch} 个节点"}

    prepared = []
    rejected = []
    for item in items:
        try:
            nid = int(item.get("id") or item.get("node_id"))
        except (TypeError, ValueError, AttributeError):
            rejected.append("无效节点 ID")
            continue
        try:
            node = Node.objects.select_related("credential").get(id=nid, is_deleted=False)
        except Node.DoesNotExist:
            rejected.append(f"节点 {nid} 不存在")
            continue
        gate = uninstall_gate_message(node)
        if gate:
            rejected.append(f"{node.hostname}（{gate}）")
            continue
        prefix = normalize_remote_path(
            (item.get("prefix") or "").strip()
        ) or resolve_prefix_for_node(node)[0]
        if not prefix:
            rejected.append(f"{node.hostname}（无法解析安装路径）")
            continue
        if is_dangerous_path(prefix):
            rejected.append(f"{node.hostname}（禁止删除路径 {prefix}）")
            continue
        backup_path = backup_subdir_for_node(node)
        if options["remove_backup"] and is_dangerous_path(backup_path):
            rejected.append(f"{node.hostname}（发布备份路径不安全：{backup_path}）")
            continue
        if options["remove_workdir"] and is_dangerous_path(work_dir):
            rejected.append(f"{node.hostname}（工作目录路径不安全：{work_dir}）")
            continue
        modules_dir = f"{work_dir.rstrip('/')}/nginx-modules"
        if options["remove_modules"] and is_dangerous_path(modules_dir):
            rejected.append(f"{node.hostname}（模块目录路径不安全：{modules_dir}）")
            continue
        prepared.append((node, prefix, backup_path))

    if not prepared:
        msg = "没有可执行的节点"
        if rejected:
            msg += "：" + "；".join(rejected[:5])
        return {"success": False, "message": msg, "skipped": rejected}

    batch_number = generate_uninstall_batch_number()
    hostnames = ",".join(n.hostname for n, _, _ in prepared)
    ips = ",".join(n.ip for n, _, _ in prepared)
    tc = TaskCenterTask.objects.create(
        operation_type="nginx_uninstall",
        status="pending",
        detail="任务已创建，等待执行 Nginx 卸载",
        target_hostnames=hostnames,
        target_ips=ips,
        target_configs="uninstall",
        source_batch=batch_number,
        trigger_user=user,
    )
    log_task_center_created(tc, user=user)

    options_payload = dict(options)
    options_payload["work_dir"] = work_dir
    options_json = json.dumps(options_payload, ensure_ascii=False)
    uninstall_ids = []
    for node, prefix, backup_path in prepared:
        ut = NginxUninstallTask.objects.create(
            batch_number=batch_number,
            node=node,
            resolved_prefix=prefix,
            backup_path=backup_path if options["remove_backup"] else "",
            work_dir=work_dir,
            options_json=options_json,
            task_center=tc,
            operator=user,
        )
        uninstall_ids.append(ut.id)

    thread = threading.Thread(
        target=_run_uninstall_batch,
        args=(tc.id, uninstall_ids),
        daemon=True,
    )
    thread.start()

    message = f"已创建 Nginx 卸载任务（{len(prepared)} 台）"
    if rejected:
        message += f"；已跳过 {len(rejected)} 台"
    return {
        "success": True,
        "async": True,
        "message": message,
        "task_center_id": tc.id,
        "source_batch": batch_number,
        "skipped": rejected,
    }


def _append_log(task, line):
    """追加卸载任务日志并保存"""
    stamp = timezone.now().strftime("%H:%M:%S")
    task.log_output = (task.log_output or "") + f"[{stamp}] {line}\n"
    task.save(update_fields=["log_output", "updated_at"])


def _set_task_status(task, status, progress=None, step="", error=""):
    """更新单节点卸载任务状态"""
    fields = ["status", "current_step", "updated_at"]
    task.status = status
    task.current_step = step or ""
    if progress is not None:
        task.progress = progress
        fields.append("progress")
    if error:
        task.error_message = error
        fields.append("error_message")
    task.save(update_fields=fields)


def _rm_rf_remote(ssh, path, log_fn):
    """远程删除目录；不存在则跳过成功。"""
    path = normalize_remote_path(path)
    if is_dangerous_path(path):
        return False, f"拒绝删除危险路径: {path}"
    quoted = shlex.quote(path)
    ok, out = ssh.execute_command(f"test -e {quoted} && echo EXISTS || echo MISSING")
    if not ok:
        return False, out or "探测路径失败"
    if "MISSING" in (out or ""):
        log_fn(f"路径不存在，跳过: {path}")
        return True, "skipped"
    log_fn(f"删除: {path}")
    ok, out = ssh.execute_command(f"rm -rf {quoted}")
    if not ok:
        return False, out or "删除失败"
    return True, "removed"


def _bookkeep_node_after_uninstall(node, operator=None):
    """卸载成功后回写节点与配置同步路径。"""
    from apps.configs.models import ConfigSyncSetting
    from apps.configs.services import save_sync_path
    from apps.nodes.services import apply_nginx_probe_result

    node.nginx_path = ""
    node.nginx_version = ""
    apply_nginx_probe_result(node, False)
    node.save(
        update_fields=[
            "nginx_path",
            "nginx_version",
            "nginx_available",
            "last_nginx_probe_at",
            "updated_at",
        ]
    )
    # 清空主配置路径，避免指向已删 conf
    try:
        setting = ConfigSyncSetting.objects.filter(node=node).first()
        if setting and setting.main_conf_path:
            save_sync_path(node, "", user=operator)
    except Exception:
        logger.exception("清空节点 %s main_conf_path 失败", node.id)


def _run_one_uninstall(task, stop_if_running):
    """执行单节点卸载，成功返回 None，失败返回错误信息。"""
    node = task.node
    cred = node.credential
    if not cred or not cred.is_enabled:
        return "凭证不可用"
    options = _parse_options(task.options_json)
    auth = _auth_kwargs(cred)
    hostname = node.hostname or node.ip

    def log(msg):
        _append_log(task, msg)

    with SSHClient(node.ip, node.port, cred.username, **auth) as ssh:
        conn = getattr(ssh, "_connect_result", None)
        if isinstance(conn, tuple) and not conn[0]:
            return f"SSH 连接失败: {conn[1] or '未知错误'}"
        # 运行检测与停止（复用同一 SSH 会话）
        try:
            running = is_nginx_running(
                node.ip,
                node.port,
                cred.username,
                nginx_path=node.nginx_path or None,
                client=ssh.client,
                **auth,
            )
        except Exception as exc:
            return f"检测运行状态失败: {exc}"

        if running:
            if not stop_if_running:
                return "Nginx 仍在运行，请先停止服务或确认「停止并继续卸载」"
            _set_task_status(task, "stopping", progress=20, step="停止 Nginx")
            log("Nginx 运行中，执行停止…")
            ok, msg = stop_nginx(
                node.ip,
                node.port,
                cred.username,
                nginx_path=node.nginx_path or None,
                client=ssh.client,
                **auth,
            )
            if not ok:
                return f"停止 Nginx 失败: {msg or '未知错误'}"
            log("已停止 Nginx")

        # 删除 prefix
        _set_task_status(task, "removing_prefix", progress=45, step="删除安装目录")
        ok, msg = _rm_rf_remote(ssh, task.resolved_prefix, log)
        if not ok:
            return msg

        # 发布备份
        if options.get("remove_backup") and task.backup_path:
            _set_task_status(task, "removing_backup", progress=65, step="清理发布备份")
            ok, msg = _rm_rf_remote(ssh, task.backup_path, log)
            if not ok:
                return f"清理发布备份失败: {msg}"

        # 额外目录
        extras = []
        work_dir = normalize_remote_path(task.work_dir or options.get("work_dir") or "")
        if options.get("remove_workdir") and work_dir:
            extras.append(work_dir)
        if options.get("remove_modules") and work_dir:
            extras.append(f"{work_dir.rstrip('/')}/nginx-modules")
        # 去重且避免重复删同一路径
        seen = set()
        unique_extras = []
        for p in extras:
            np = normalize_remote_path(p)
            if np and np not in seen and np != normalize_remote_path(task.resolved_prefix):
                seen.add(np)
                unique_extras.append(np)
        if unique_extras:
            _set_task_status(task, "removing_extra", progress=80, step="清理额外目录")
            for p in unique_extras:
                ok, msg = _rm_rf_remote(ssh, p, log)
                if not ok:
                    return f"清理额外目录失败: {msg}"

    # 平台回写（SSH 已关闭）
    _set_task_status(task, "updating_node", progress=90, step="更新节点状态")
    log("回写节点 Nginx 状态…")
    try:
        _bookkeep_node_after_uninstall(node, operator=task.operator)
        log("节点状态已更新（nginx_available=False，绑定 orphaned）")
    except Exception as exc:
        logger.exception("卸载后回写节点失败 node=%s", node.id)
        return f"文件已删除但回写节点失败: {exc}"

    return None


def _run_uninstall_batch(task_center_id, uninstall_task_ids):
    """后台串行执行批次内各节点卸载。"""
    from .models import NginxUninstallTask

    TaskCenterTask.objects.filter(pk=task_center_id).update(
        status="running",
        progress=5,
        detail="正在执行 Nginx 卸载…",
        started_at=timezone.now(),
    )

    tasks = list(
        NginxUninstallTask.objects.filter(id__in=uninstall_task_ids)
        .select_related("node", "node__credential", "operator")
        .order_by("id")
    )
    total = len(tasks)
    success_count = 0
    fail_count = 0
    done = 0
    node_blocks = []

    try:
        for task in tasks:
            if is_cancelled(task_center_id):
                NginxUninstallTask.objects.filter(
                    id__in=uninstall_task_ids,
                    status__in=[
                        "pending", "stopping", "removing_prefix",
                        "removing_backup", "removing_extra", "updating_node",
                    ],
                ).update(
                    status="cancelled",
                    error_message="用户手动取消",
                    finished_at=timezone.now(),
                )
                return

            node = task.node
            hostname = node.hostname or node.ip
            options = _parse_options(task.options_json)
            stop_if_running = options.get("stop_if_running", True)
            _set_current_step(task_center_id, hostname, "卸载 Nginx")
            node_blocks.append(node_header(node.ip, node.hostname))
            _set_task_status(task, "pending", progress=5, step="连接远程")

            try:
                err = _run_one_uninstall(task, stop_if_running=stop_if_running)
                if err:
                    fail_count += 1
                    _set_task_status(
                        task, "failed", progress=100, step="", error=err,
                    )
                    task.finished_at = timezone.now()
                    task.save(update_fields=["finished_at", "updated_at"])
                    node_blocks.append(item_failed("Nginx 卸载", err))
                else:
                    success_count += 1
                    task.status = "success"
                    task.progress = 100
                    task.current_step = ""
                    task.finished_at = timezone.now()
                    task.save(
                        update_fields=[
                            "status", "progress", "current_step",
                            "finished_at", "updated_at",
                        ]
                    )
                    node_blocks.append(item_success("Nginx 卸载"))
            except Exception as exc:
                logger.exception("Nginx 卸载失败 node=%s", node.id)
                fail_count += 1
                _set_task_status(
                    task, "failed", progress=100, step="", error=str(exc),
                )
                task.finished_at = timezone.now()
                task.save(update_fields=["finished_at", "updated_at"])
                node_blocks.append(item_failed("Nginx 卸载", str(exc)))

            done += 1
            _set_current_step(task_center_id, hostname, None)
            update_if_active(
                task_center_id,
                progress=int(done * 100 / total) if total else 100,
                detail=(
                    f"执行中：成功 {success_count}，失败 {fail_count}，"
                    f"已完成 {done}/{total}"
                ),
                result="\n".join(node_blocks),
            )

        if is_cancelled(task_center_id):
            return

        status = "success" if fail_count == 0 else "failed"
        finish_if_active(
            task_center_id,
            status=status,
            progress=100,
            finished_at=timezone.now(),
            detail=f"执行完成：成功 {success_count}，失败 {fail_count}，共 {total}",
            result=build_tree_result(success_count, fail_count, total, node_blocks),
        )
    finally:
        _clear_release_progress_state(task_center_id)
