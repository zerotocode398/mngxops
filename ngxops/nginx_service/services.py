"""执行 Nginx 启停批次并持久化节点级结果。"""

import logging
import shlex
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.orm import joinedload, sessionmaker

from ngxops.credentials.crypto import CredentialDecryptionError
from ngxops.database.session import session_scope
from ngxops.logging_setup import log_exception
from ngxops.nodes.models import Node
from ngxops.settings.service import read_setting
from ngxops.tasks.executor import TaskCancelled, TaskContext, TaskOutcome
from ngxops.tasks.models import Task, TaskLog
from ngxops.upgrade.services import _connect_target, _run_remote_command


logger = logging.getLogger(__name__)
_BATCH_LOCK = threading.Lock()
ACTION_LABELS = {
    "start": "启动",
    "stop": "停止",
    "reload": "重载",
    "restart": "重启",
}
_ACTIVE_SYSTEMD_STATES = frozenset(("active", "activating", "reloading"))
_ENABLED_SYSTEMD_STATES = frozenset(("enabled", "enabled-runtime", "static"))


def _next_batch_number(session, now: datetime) -> str:
    """生成当天递增的启停批次号。"""
    prefix = "OP-{}-".format(now.strftime("%y%m%d"))
    existing = session.scalars(
        select(Task.source_batch)
        .where(
            Task.operation_type == "nginx_service_control",
            Task.source_batch.like(prefix + "%"),
        )
        .order_by(Task.source_batch.desc())
    ).first()
    try:
        sequence = int(existing.rsplit("-", 1)[-1]) + 1 if existing else 1
    except (TypeError, ValueError):
        sequence = 1
    return "{}{:04d}".format(prefix, sequence)


def create_service_task(
    session_factory: sessionmaker,
    executor,
    *,
    encryption_key: bytes,
    node_ids: List[int],
    hostnames: List[str],
    ips: List[str],
    action: str,
    trigger_user_id: int,
) -> Tuple[int, str]:
    """创建启停任务、日增批次并提交到统一线程池。"""
    with _BATCH_LOCK:
        with session_scope(session_factory) as session:
            with session.begin():
                batch_number = _next_batch_number(session, datetime.now())
                task = Task(
                    operation_type="nginx_service_control",
                    detail="已创建 Nginx {} 任务，等待执行".format(
                        ACTION_LABELS[action]
                    ),
                    source_batch=batch_number,
                    target_hostnames=", ".join(hostnames),
                    target_ips=", ".join(ips),
                    target_configs=action,
                    subject_type="nginx_service_batch",
                    trigger_user_id=trigger_user_id,
                )
                session.add(task)
                session.flush()
                task_id = task.id
                session.add(TaskLog(task_id=task_id, message="任务已加入执行队列"))
        try:
            executor.submit(
                task_id,
                lambda context: _run_service_batch(
                    session_factory, encryption_key, node_ids, action, context
                ),
            )
        except RuntimeError as exc:
            with session_scope(session_factory) as session:
                with session.begin():
                    session.execute(
                        update(Task)
                        .where(Task.id == task_id, Task.status == "pending")
                        .values(
                            status="failed",
                            detail="任务无法启动（{}）".format(type(exc).__name__),
                            progress=100,
                            finished_at=datetime.utcnow(),
                        )
                    )
                    session.add(
                        TaskLog(task_id=task_id, level="error", message="任务无法启动")
                    )
        return task_id, batch_number


def _load_target(
    session_factory: sessionmaker,
    node_id: int,
    encryption_key: bytes,
) -> Tuple[Optional[Dict[str, object]], str]:
    """重新检查节点门禁并临时解密 SSH 凭证。"""
    with session_scope(session_factory) as session:
        node = session.scalar(
            select(Node)
            .options(joinedload(Node.credential))
            .where(Node.id == node_id, Node.is_deleted.is_(False))
        )
        if node is None:
            return None, "节点不存在或已删除"
        if node.is_locked:
            return None, "节点已锁定"
        if node.status != "online":
            return None, "节点 SSH 非在线状态"
        if node.nginx_available is not True:
            return None, "节点 Nginx 未确认可用"
        credential = node.credential
        if credential is None or not credential.is_enabled:
            return None, "节点没有可用的 SSH 凭证"
        try:
            secret = (
                credential.get_password(encryption_key)
                if credential.auth_type == "password"
                else credential.get_private_key(encryption_key)
            )
        except CredentialDecryptionError:
            return None, "SSH 凭证解密失败"
        target = {
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "port": node.port,
            "nginx_path": node.nginx_path,
            "username": credential.username,
            "auth_type": credential.auth_type,
            "password": secret if credential.auth_type == "password" else "",
            "private_key": secret if credential.auth_type != "password" else "",
            "ssh_timeout": read_setting(session, "node.ssh_connect_timeout", 10),
            "detect_retries": read_setting(session, "node.detect_retries", 1),
        }
    return target, ""


def _systemd_unit(client, context: TaskContext) -> str:
    """识别正在使用或启用的 nginx systemd unit。"""
    status, _output = _run_remote_command(
        client,
        "command -v systemctl >/dev/null 2>&1",
        timeout=20,
        context=context,
    )
    if status != 0:
        context.append_log("未检测到 systemctl，使用 Nginx 二进制管理")
        return ""
    for unit in ("nginx", "nginx.service"):
        _status, active_output = _run_remote_command(
            client,
            "systemctl is-active {} 2>/dev/null || true".format(shlex.quote(unit)),
            timeout=20,
            context=context,
        )
        active_state = active_output.strip().splitlines()[-1] if active_output else ""
        if active_state in _ACTIVE_SYSTEMD_STATES:
            context.append_log("检测到 systemd 管理 unit {}".format(unit))
            return unit
        _status, enabled_output = _run_remote_command(
            client,
            "systemctl is-enabled {} 2>/dev/null || true".format(shlex.quote(unit)),
            timeout=20,
            context=context,
        )
        enabled_state = enabled_output.strip().splitlines()[-1] if enabled_output else ""
        if enabled_state in _ENABLED_SYSTEMD_STATES:
            context.append_log("检测到已启用的 systemd unit {}".format(unit))
            return unit
    context.append_log("未检测到托管中的 nginx unit，使用 Nginx 二进制管理")
    return ""


def _run_action(
    client,
    target: Dict[str, object],
    action: str,
    context: TaskContext,
) -> Tuple[bool, str]:
    """按 systemd 管理能力或 Nginx 二进制执行单个动作。"""
    unit = _systemd_unit(client, context)
    if unit:
        command = "systemctl {} {} 2>&1".format(action, shlex.quote(unit))
        context.append_log("执行: {}".format(command))
        status, output = _run_remote_command(client, command, timeout=90, context=context)
        if status == 0:
            return True, output or "systemctl {} {} 成功".format(action, unit)
        return False, _command_failure("systemctl {} 失败".format(action), output)

    nginx_path = str(target.get("nginx_path") or "nginx").strip() or "nginx"
    binary = shlex.quote(nginx_path)
    if action == "start":
        return _run_binary_command(client, binary, "启动 Nginx", context)
    if action == "reload":
        return _run_binary_command(
            client, "{} -s reload".format(binary), "重载 Nginx", context
        )
    if action == "stop":
        status, output = _run_remote_command(
            client, "{} -s quit".format(binary), timeout=90, context=context
        )
        if status == 0:
            return True, output or "nginx -s quit 成功"
        return _run_binary_command(
            client, "{} -s stop".format(binary), "停止 Nginx", context
        )
    if action == "restart":
        status, output = _run_remote_command(
            client, "{} -s quit".format(binary), timeout=90, context=context
        )
        if status != 0:
            context.append_log("quit 未成功，尝试 nginx -s stop")
            _run_remote_command(
                client, "{} -s stop".format(binary), timeout=90, context=context
            )
        return _run_binary_command(client, binary, "重启 Nginx", context)
    return False, "不支持的 Nginx 动作"


def _run_binary_command(
    client,
    command: str,
    label: str,
    context: TaskContext,
) -> Tuple[bool, str]:
    """执行单条 Nginx 二进制命令并规范化结果。"""
    context.append_log("执行: {}".format(command))
    status, output = _run_remote_command(client, command, timeout=90, context=context)
    if status == 0:
        return True, output or "{}成功".format(label)
    return False, _command_failure("{}失败".format(label), output)


def _command_failure(message: str, output: str) -> str:
    """组合远程命令失败摘要并限制结果长度。"""
    detail = (output or "").strip()
    return "{}：{}".format(message, detail[-1500:]) if detail else message


def _run_service_batch(
    session_factory: sessionmaker,
    encryption_key: bytes,
    node_ids: List[int],
    action: str,
    context: TaskContext,
) -> TaskOutcome:
    """逐节点执行启停动作并持续更新任务进度和结果树。"""
    action_label = ACTION_LABELS[action]
    nodes = []
    with session_scope(session_factory) as session:
        node_rows = session.scalars(
            select(Node)
            .where(Node.id.in_(node_ids))
            .order_by(Node.id.asc())
        ).all()
        by_id = {node.id: node for node in node_rows}
        for node_id in node_ids:
            node = by_id.get(node_id)
            nodes.append(
                {
                    "id": node_id,
                    "hostname": node.hostname if node else "节点 {}".format(node_id),
                    "ip": node.ip if node else "",
                    "status": "pending",
                    "message": "等待执行",
                }
            )

    results = {"summary": {"success": 0, "failed": 0, "total": len(nodes)}, "nodes": nodes}
    context.set_result_tree(results)
    total = len(nodes)
    success_count = 0
    fail_count = 0
    if not encryption_key:
        return TaskOutcome("failed", "凭证加密密钥不可用", results)

    for index, item in enumerate(nodes, start=1):
        context.check_cancelled()
        hostname = item["hostname"] or item["ip"]
        context.update_progress(
            min(98, int((index - 1) * 99 / max(total, 1))),
            "正在对 {} 执行 Nginx {}（{}/{}）".format(
                hostname, action_label, index, total
            ),
        )
        item["status"] = "running"
        item["message"] = "正在执行 Nginx {}".format(action_label)
        context.set_result_tree(results)
        context.append_log("{} ({}) 开始 Nginx {}".format(hostname, item["ip"], action_label))
        target, error = _load_target(session_factory, int(item["id"]), encryption_key)
        ok = False
        message = error
        if target is not None:
            client = None
            try:
                client = _connect_target(target, context)
                ok, message = _run_action(client, target, action, context)
            except TaskCancelled:
                raise
            except Exception as exc:
                log_exception(
                    logger,
                    "Nginx service action failed",
                    exc,
                    "node_id={} action={}".format(item["id"], action),
                )
                message = "SSH 或远程操作失败（{}）".format(type(exc).__name__)
            finally:
                if client is not None:
                    client.close()
        item["status"] = "success" if ok else "failed"
        item["message"] = message or ("操作成功" if ok else "操作失败")
        if ok:
            success_count += 1
        else:
            fail_count += 1
        results["summary"] = {
            "success": success_count,
            "failed": fail_count,
            "total": total,
        }
        context.set_result_tree(results)
        context.append_log(
            "{} ({}) Nginx {}{}".format(
                hostname,
                item["ip"],
                action_label,
                "成功: {}".format(item["message"])
                if ok
                else "失败: {}".format(item["message"]),
            ),
            level="info" if ok else "error",
        )
        context.update_progress(
            min(99, int(index * 99 / max(total, 1))),
            "Nginx {}：成功 {}，失败 {}，已完成 {}/{}".format(
                action_label, success_count, fail_count, index, total
            ),
        )

    status = "success" if fail_count == 0 else "failed"
    detail = "Nginx {}完成：成功 {}，失败 {}，共 {} 台".format(
        action_label, success_count, fail_count, total
    )
    return TaskOutcome(status, detail, results)
