"""Nginx 启停后台执行与批次号生成。"""
import logging

from django.db import transaction
from django.utils import timezone

from apps.nodes.models import Node
from apps.releases.models import TaskCenterTask
from apps.releases.task_cancel import finish_if_active, is_cancelled, update_if_active
from apps.releases.task_progress import (
    _append_task_center_log,
    _clear_release_progress_state,
    _set_current_step,
)
from apps.releases.task_result import (
    build_tree_result,
    item_failed,
    item_success,
    node_header,
)
from utils.nginx_ops import reload_nginx, restart_nginx, start_nginx, stop_nginx

logger = logging.getLogger(__name__)

# 支持的服务动作
_ACTION_MAP = {
    "start": ("启动", start_nginx),
    "stop": ("停止", stop_nginx),
    "reload": ("重载", reload_nginx),
    "restart": ("重启", restart_nginx),
}

# 动作码 → 展示名（历史/最近任务表）
ACTION_LABELS = {k: v[0] for k, v in _ACTION_MAP.items()}


def generate_service_batch_number():
    """生成启停批次号，格式 OP-YYMMDD-NNNN（当日自增）"""
    today = timezone.now().strftime("%y%m%d")
    prefix = f"OP-{today}-"
    with transaction.atomic():
        last = (
            TaskCenterTask.objects.select_for_update()
            .filter(
                operation_type="nginx_service_control",
                source_batch__startswith=prefix,
            )
            .order_by("-source_batch")
            .first()
        )
        if last and last.source_batch:
            seq = int(last.source_batch[-4:]) + 1
        else:
            seq = 1
        return f"{prefix}{seq:04d}"


def _auth_kwargs(credential):
    """按凭证类型组装 nginx_ops 认证参数"""
    if credential.auth_type == "password":
        return {"password": credential.get_password()}
    return {"private_key": credential.get_private_key()}


def _append_node_log(task_id, hostname, msg):
    """向启停任务日志追加带时间与主机名的一行"""
    stamp = timezone.now().strftime("%H:%M:%S")
    _append_task_center_log(task_id, f"[{stamp}] {hostname} {msg}")


def _ops_log_fn(task_id, hostname):
    """构造 nginx_ops 过程日志回调，写入同一任务流水"""

    def _log(msg):
        if msg:
            _append_node_log(task_id, hostname, msg)

    return _log


def _run_nginx_service_task(task_id, node_ids, action):
    """后台串行逐节点执行启停，刷活进度步骤与结果树"""
    action_label, action_fn = _ACTION_MAP[action]
    TaskCenterTask.objects.filter(pk=task_id).update(
        status="running",
        progress=5,
        detail=f"正在执行 Nginx {action_label}...",
        started_at=timezone.now(),
    )

    nodes = list(
        Node.objects.filter(id__in=node_ids)
        .select_related("credential")
        .order_by("id")
    )
    total = len(nodes)
    success_count = 0
    fail_count = 0
    done = 0
    node_blocks = []
    item_label = f"Nginx {action_label}"

    try:
        for node in nodes:
            if is_cancelled(task_id):
                return

            hostname = node.hostname or node.ip
            _set_current_step(task_id, hostname, item_label)
            node_blocks.append(node_header(node.ip, node.hostname))
            try:
                cred = node.credential
                if not cred or not cred.is_enabled:
                    fail_count += 1
                    node_blocks.append(item_failed(item_label, "凭证不可用"))
                    _append_node_log(task_id, hostname, f"{item_label} 失败：凭证不可用")
                else:
                    nginx_path = (node.nginx_path or "").strip() or "PATH 中的 nginx"
                    _append_node_log(
                        task_id,
                        hostname,
                        f"SSH {cred.username}@{node.ip}:{node.port}  nginx_path={nginx_path}",
                    )
                    ok, msg = action_fn(
                        node.ip,
                        node.port,
                        cred.username,
                        nginx_path=node.nginx_path or None,
                        log_fn=_ops_log_fn(task_id, hostname),
                        **_auth_kwargs(cred),
                    )
                    if ok:
                        success_count += 1
                        node_blocks.append(item_success(item_label))
                        extra = f"：{msg}" if msg else ""
                        _append_node_log(task_id, hostname, f"{item_label} 成功{extra}")
                    else:
                        fail_count += 1
                        node_blocks.append(item_failed(item_label, msg or "执行失败"))
                        _append_node_log(
                            task_id, hostname, f"{item_label} 失败：{msg or '执行失败'}"
                        )
            except Exception as exc:
                logger.exception("Nginx %s 失败 node=%s", action, node.id)
                fail_count += 1
                node_blocks.append(item_failed(item_label, str(exc)))
                _append_node_log(task_id, hostname, f"{item_label} 失败：{exc}")

            done += 1
            _set_current_step(task_id, hostname, None)
            # 刷入已完成节点的活树，供进度遮罩动态展示
            update_if_active(
                task_id,
                progress=int(done * 100 / total) if total else 100,
                detail=(
                    f"执行中：成功 {success_count}，失败 {fail_count}，"
                    f"已完成 {done}/{total}"
                ),
                result="\n".join(node_blocks),
            )

        if is_cancelled(task_id):
            return

        status = "success" if fail_count == 0 else "failed"
        finish_if_active(
            task_id,
            status=status,
            progress=100,
            finished_at=timezone.now(),
            detail=f"执行完成：成功 {success_count}，失败 {fail_count}，共 {total}",
            result=build_tree_result(success_count, fail_count, total, node_blocks),
        )
    finally:
        _clear_release_progress_state(task_id)
