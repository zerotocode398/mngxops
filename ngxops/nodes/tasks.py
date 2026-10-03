"""执行节点 SSH 探测、Nginx 能力识别和系统信息采集任务。"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import paramiko
from sqlalchemy import select
from sqlalchemy.orm import joinedload, sessionmaker

from ngxops.configs.services import mark_node_bindings_orphaned
from ngxops.credentials.crypto import CredentialDecryptionError
from ngxops.credentials.models import Credential
from ngxops.credentials.tasks import _connect_ssh, _probe_nginx
from ngxops.database.session import session_scope
from ngxops.nodes.models import Node
from ngxops.tasks.executor import TaskContext, TaskOutcome
from ngxops.settings.service import read_setting


_MAX_PROBE_WORKERS = 3
_UNKNOWN = "未知"


def _load_targets(
    session_factory: sessionmaker,
    node_ids: List[int],
    encryption_key: bytes,
) -> List[dict]:
    """读取节点和凭证快照并在关闭数据库会话前解密凭证。"""
    with session_scope(session_factory) as session:
        nodes = session.scalars(
            select(Node)
            .options(joinedload(Node.credential))
            .where(Node.id.in_(set(node_ids)), Node.is_deleted.is_(False))
            .order_by(Node.id.asc())
        ).unique().all()
        targets = []
        for node in nodes:
            target = {
                "id": node.id,
                "hostname": node.hostname,
                "ip": node.ip,
                "port": node.port,
                "nginx_path": node.nginx_path,
                "credential_id": node.credential_id,
                "is_locked": node.is_locked,
                "username": "",
                "auth_type": "",
                "password": "",
                "private_key": "",
                "credential_error": "",
                "ssh_timeout": read_setting(session, "node.ssh_connect_timeout", 10),
                "detect_retries": read_setting(session, "node.detect_retries", 1),
            }
            credential = node.credential
            if credential is None:
                target["credential_error"] = "未配置 SSH 凭证"
            elif not credential.is_enabled:
                target["credential_error"] = "关联凭证已禁用"
            else:
                target["username"] = credential.username
                target["auth_type"] = credential.auth_type
                try:
                    secret = (
                        credential.get_password(encryption_key)
                        if credential.auth_type == "password"
                        else credential.get_private_key(encryption_key)
                    )
                    target[
                        "password" if credential.auth_type == "password" else "private_key"
                    ] = secret
                except CredentialDecryptionError:
                    target["credential_error"] = "凭证解密失败"
            targets.append(target)
    return targets


def _connect_target(
    target: dict,
    context: TaskContext,
) -> Tuple[Optional[paramiko.SSHClient], str]:
    """使用节点凭证建立 SSH 连接并返回不含明文的错误摘要。"""
    if target["credential_error"]:
        return None, target["credential_error"]
    return _connect_ssh(
        target["ip"],
        target["port"],
        target["username"],
        target["auth_type"],
        target["password"],
        target["private_key"],
        context,
        target["ssh_timeout"],
        target["detect_retries"],
    )


def _probe_target(target: dict, context: TaskContext) -> dict:
    """探测单个节点 SSH 和 Nginx 状态并生成脱敏结果。"""
    context.check_cancelled()
    result = {
        "node_id": target["id"],
        "hostname": target["hostname"],
        "ip": target["ip"],
        "ssh_success": False,
        "nginx_available": None,
        "nginx_version": "",
        "message": "",
    }
    if target["is_locked"]:
        result["message"] = "节点已锁定"
        return result
    client, error = _connect_target(target, context)
    if client is None:
        result["message"] = error
        return result
    try:
        available, version = _probe_nginx(client, target["nginx_path"])
    finally:
        client.close()
    available = available and bool(version)
    result.update(
        {
            "ssh_success": True,
            "nginx_available": available,
            "nginx_version": version if available else "",
            "message": "SSH 连接成功" if available else "SSH 连接成功，未检测到 Nginx",
        }
    )
    return result


def _single_result_tree(result: dict, success: bool) -> dict:
    """构造单节点任务使用的标准结果树摘要。"""
    return {
        "summary": {
            "total": 1,
            "success": 1 if success else 0,
            "failed": 0 if success else 1,
        },
        "nodes": [result],
    }


def _empty_result_tree() -> dict:
    """构造没有活动节点时的空结果树。"""
    return {"summary": {"total": 0, "success": 0, "failed": 0}, "nodes": []}


def _apply_probe_result(
    session_factory: sessionmaker,
    target: dict,
    result: dict,
    *,
    update_nginx: bool,
) -> None:
    """仅将仍关联原凭证的探测结果写回活动节点。"""
    now = datetime.utcnow()
    with session_scope(session_factory) as session:
        with session.begin():
            node = session.scalar(
                select(Node).where(
                    Node.id == target["id"],
                    Node.is_deleted.is_(False),
                    Node.credential_id == target["credential_id"],
                )
            )
            if node is None:
                return
            if target["is_locked"] or target["credential_error"]:
                return
            if result["ssh_success"]:
                node.status = "online"
                node.last_probe_at = now
            else:
                node.status = "offline"
            if update_nginx and result["ssh_success"]:
                node.nginx_available = result["nginx_available"]
                node.nginx_version = result["nginx_version"]
                node.last_nginx_probe_at = now
                if not result["nginx_available"]:
                    mark_node_bindings_orphaned(session, node.id, now)
            node.updated_at = now


def _run_single_probe(
    session_factory: sessionmaker,
    node_id: int,
    encryption_key: bytes,
):
    """构造单节点 SSH 与 Nginx 双维度探测任务。"""
    def run(context: TaskContext) -> TaskOutcome:
        """执行探测、写回节点状态并返回结果树。"""
        targets = _load_targets(session_factory, [node_id], encryption_key)
        if not targets:
            return TaskOutcome(
                "failed",
                "节点不存在或已删除",
                _empty_result_tree(),
            )
        target = targets[0]
        context.update_progress(10, "正在测试 SSH 连接")
        result = _probe_target(target, context)
        _apply_probe_result(session_factory, target, result, update_nginx=True)
        context.append_log(
            "节点 {} SSH {}".format(
                result["ip"], "连接成功" if result["ssh_success"] else "连接失败"
            ),
            "info" if result["ssh_success"] else "warning",
        )
        status = "success" if result["ssh_success"] else "failed"
        detail = "SSH 探测完成：{}".format(result["message"])
        return TaskOutcome(
            status,
            detail,
            _single_result_tree(result, result["ssh_success"]),
        )

    return run


def _run_batch_probe(
    session_factory: sessionmaker,
    node_ids: List[int],
    encryption_key: bytes,
    max_workers: int = _MAX_PROBE_WORKERS,
):
    """构造并发批量 SSH 与 Nginx 探测任务。"""
    def run(context: TaskContext) -> TaskOutcome:
        """并发探测节点并逐项保存结果与任务进度。"""
        targets = _load_targets(session_factory, node_ids, encryption_key)
        if not targets:
            return TaskOutcome(
                "failed",
                "没有可探测的活动节点",
                _empty_result_tree(),
            )
        results = []
        failures = 0
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(targets)),
            thread_name_prefix="ngxops-node-probe",
        ) as pool:
            future_targets = {
                pool.submit(_probe_target, target, context): target
                for target in targets
            }
            for index, future in enumerate(as_completed(future_targets), start=1):
                context.check_cancelled()
                target = future_targets[future]
                result = future.result()
                _apply_probe_result(session_factory, target, result, update_nginx=True)
                results.append(result)
                if not result["ssh_success"]:
                    failures += 1
                context.append_log(
                    "节点 {}/{} {}：{}".format(
                        index,
                        len(targets),
                        result["ip"],
                        "SSH 成功" if result["ssh_success"] else result["message"],
                    ),
                    "info" if result["ssh_success"] else "warning",
                )
                context.update_progress(
                    min(99, int(index * 100 / len(targets))),
                    "已探测 {} / {} 台节点".format(index, len(targets)),
                )
        results.sort(key=lambda item: item["node_id"])
        success_count = len(results) - failures
        tree = {
            "summary": {
                "total": len(results),
                "success": success_count,
                "failed": failures,
            },
            "nodes": results,
        }
        status = "failed" if failures else "success"
        detail = "批量 SSH 探测完成：成功 {}，失败 {}".format(
            success_count, failures
        )
        return TaskOutcome(status, detail, tree)

    return run


def _run_system_info(
    session_factory: sessionmaker,
    node_id: int,
    encryption_key: bytes,
):
    """构造采集节点操作系统、CPU、内存、磁盘和运行时间的任务。"""
    def run(context: TaskContext) -> TaskOutcome:
        """通过单条 SSH 会话采集系统信息并更新 SSH 在线状态。"""
        targets = _load_targets(session_factory, [node_id], encryption_key)
        if not targets:
            return TaskOutcome(
                "failed",
                "节点不存在或已删除",
                _empty_result_tree(),
            )
        target = targets[0]
        context.update_progress(10, "正在连接节点并采集系统信息")
        client, error = _connect_target(target, context)
        if client is None:
            result = {
                "node_id": node_id,
                "hostname": target["hostname"],
                "ip": target["ip"],
                "ssh_success": False,
                "message": error,
                "system_info": {},
            }
            _apply_probe_result(session_factory, target, result, update_nginx=False)
            return TaskOutcome(
                "failed",
                "系统信息采集失败：{}".format(error),
                _single_result_tree(result, False),
            )
        try:
            info = _collect_system_info(client)
        finally:
            client.close()
        result = {
            "node_id": node_id,
            "hostname": target["hostname"],
            "ip": target["ip"],
            "ssh_success": True,
            "message": "系统信息采集成功",
            "system_info": info,
        }
        _apply_probe_result(session_factory, target, result, update_nginx=False)
        context.append_log("节点 {} 系统信息采集完成".format(target["ip"]))
        return TaskOutcome("success", "系统信息采集成功", _single_result_tree(result, True))

    return run


def _collect_system_info(client: paramiko.SSHClient) -> Dict[str, str]:
    """使用安全固定命令采集系统信息并为不可用字段填入未知。"""
    commands = {
        "os": (
            "grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 "
            "| tr -d '\"' || cat /etc/redhat-release 2>/dev/null || uname -s"
        ),
        "kernel": "uname -r | cut -d- -f1",
        "cpu": "lscpu 2>/dev/null | grep -m1 'Model name' | cut -d: -f2 | xargs",
        "cpu_cores": "nproc 2>/dev/null",
        "memory_total": "free -h 2>/dev/null | awk '/^Mem:/ {print $2}'",
        "memory_used": "free -h 2>/dev/null | awk '/^Mem:/ {print $3}'",
        "disk_total": "df -h / 2>/dev/null | awk 'NR==2 {print $2}'",
        "disk_used": "df -h / 2>/dev/null | awk 'NR==2 {print $3}'",
        "uptime": "uptime -p 2>/dev/null | sed 's/^up //'",
    }
    info = {}
    for field, command in commands.items():
        info[field] = _execute_output(client, command) or _UNKNOWN
    if info["cpu"] == _UNKNOWN:
        info["cpu"] = _execute_output(
            client,
            "grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs",
        ) or _UNKNOWN
    if info["uptime"] == _UNKNOWN:
        info["uptime"] = _execute_output(
            client,
            "uptime 2>/dev/null | awk '{print $3,$4}' | sed 's/,//'",
        ) or _UNKNOWN
    return info


def _execute_output(client: paramiko.SSHClient, command: str) -> str:
    """执行固定只读系统信息命令并隐藏命令错误详情。"""
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=20)
        exit_status = stdout.channel.recv_exit_status()
        output = (stdout.read() + stderr.read()).decode("utf-8", "replace").strip()
        return output if exit_status == 0 else ""
    except Exception:
        return ""


def _run_nginx_probe(
    session_factory: sessionmaker,
    node_id: int,
    encryption_key: bytes,
):
    """构造独立 Nginx 版本探测任务并保持 SSH 状态与 Nginx 状态分离。"""
    def run(context: TaskContext) -> TaskOutcome:
        """探测 Nginx 可用性并更新对应状态维度。"""
        targets = _load_targets(session_factory, [node_id], encryption_key)
        if not targets:
            return TaskOutcome(
                "failed",
                "节点不存在或已删除",
                _empty_result_tree(),
            )
        target = targets[0]
        context.update_progress(10, "正在连接节点并检测 Nginx")
        client, error = _connect_target(target, context)
        if client is None:
            result = {
                "node_id": node_id,
                "hostname": target["hostname"],
                "ip": target["ip"],
                "ssh_success": False,
                "nginx_available": None,
                "nginx_version": "",
                "message": error,
            }
            _apply_probe_result(session_factory, target, result, update_nginx=False)
            return TaskOutcome(
                "failed",
                "SSH 连接失败，Nginx 状态未更改",
                _single_result_tree(result, False),
            )
        try:
            available, output = _probe_nginx(client, target["nginx_path"])
        finally:
            client.close()
        available = available and bool(output)
        version = output if available else ""
        result = {
            "node_id": node_id,
            "hostname": target["hostname"],
            "ip": target["ip"],
            "ssh_success": True,
            "nginx_available": available,
            "nginx_version": version if available else "",
            "message": "检测到 Nginx" if available else "SSH 在线，未检测到 Nginx",
        }
        _apply_probe_result(session_factory, target, result, update_nginx=True)
        if not available:
            return TaskOutcome(
                "failed",
                "SSH 在线，但未检测到 Nginx",
                _single_result_tree(result, False),
            )
        context.append_log("节点 {} Nginx {}".format(target["ip"], version))
        return TaskOutcome(
            "success",
            "Nginx 版本检测成功",
            _single_result_tree(result, True),
        )

    return run
