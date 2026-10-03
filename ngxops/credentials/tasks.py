"""在统一任务执行器中测试凭证关联节点。"""

import re
import shlex
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

import paramiko
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ngxops.configs.services import mark_node_bindings_orphaned
from ngxops.credentials.crypto import CredentialDecryptionError
from ngxops.credentials.models import Credential
from ngxops.database.session import session_scope
from ngxops.nodes.models import Node
from ngxops.tasks.executor import TaskContext, TaskOutcome
from ngxops.settings.service import read_setting


_NGINX_VERSION = re.compile(r"nginx/([0-9]+(?:\.[0-9]+){1,3})")
_PRIVATE_KEY_TYPES = (paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key)
_MAX_TEST_WORKERS = 3


def _load_private_key(value: str) -> paramiko.PKey:
    """将已解密的私钥文本解析为 Paramiko 密钥对象。"""
    last_error = None
    for key_type in _PRIVATE_KEY_TYPES:
        try:
            return key_type.from_private_key(StringIO(value))
        except (paramiko.SSHException, ValueError, TypeError) as exc:
            last_error = exc
    raise ValueError("私钥无法解析") from last_error


def _connect_ssh(
    host: str,
    port: int,
    username: str,
    auth_type: str,
    password: str,
    private_key: str,
    context: TaskContext,
    timeout: int = 10,
    retries: int = 1,
) -> Tuple[Optional[paramiko.SSHClient], str]:
    """在远程主机建立单次 SSH 连接并返回安全错误摘要。"""
    retry_count = min(max(int(retries), 0), 10)
    for attempt in range(retry_count + 1):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        context.register_cancel_callback(client.close)
        try:
            options: Dict[str, Any] = {
                "hostname": host,
                "port": port,
                "username": username,
                "timeout": timeout,
                "banner_timeout": timeout,
                "auth_timeout": timeout,
                "look_for_keys": False,
                "allow_agent": False,
            }
            if auth_type == "password":
                options["password"] = password
            else:
                options["pkey"] = _load_private_key(private_key)
            client.connect(**options)
            return client, ""
        except paramiko.AuthenticationException:
            client.close()
            return None, "SSH 认证失败"
        except Exception:
            client.close()
            if attempt >= retry_count:
                return None, "SSH 连接失败"
    return None, "SSH 连接失败"


def _probe_nginx(client: paramiko.SSHClient, nginx_path: str) -> Tuple[bool, str]:
    """执行远程 Nginx 版本探测并只保留规范化版本号。"""
    command = "{} -v".format(shlex.quote(nginx_path or "/usr/sbin/nginx"))
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=20)
        exit_status = stdout.channel.recv_exit_status()
        output = (stderr.read() + stdout.read()).decode("utf-8", "replace")
    except Exception:
        return False, ""
    if exit_status != 0:
        return False, ""
    match = _NGINX_VERSION.search(output)
    return True, match.group(1) if match else ""


def _test_node(
    node: dict,
    auth: dict,
    context: TaskContext,
) -> dict:
    """测试单个节点的 SSH 和 Nginx 可用性。"""
    context.check_cancelled()
    client, error = _connect_ssh(
        node["ip"],
        node["port"],
        auth["username"],
        auth["auth_type"],
        auth["password"],
        auth["private_key"],
        context,
        auth.get("ssh_timeout", 10),
        auth.get("detect_retries", 1),
    )
    if client is None:
        return {"node_id": node["id"], "hostname": node["hostname"], "ip": node["ip"], "ssh_success": False, "message": error}
    try:
        nginx_available, nginx_version = _probe_nginx(client, node["nginx_path"])
    finally:
        client.close()
    message = "SSH 连接成功，检测到 Nginx" if nginx_available else "SSH 连接成功，未检测到 Nginx"
    return {
        "node_id": node["id"],
        "hostname": node["hostname"],
        "ip": node["ip"],
        "ssh_success": True,
        "nginx_available": nginx_available,
        "nginx_version": nginx_version,
        "message": message,
    }


def _load_test_targets(
    session_factory: sessionmaker,
    credential_id: int,
    encryption_key: bytes,
) -> Tuple[Optional[dict], List[dict]]:
    """读取凭证明文和活动关联节点快照后关闭数据库会话。"""
    with session_scope(session_factory) as session:
        credential = session.get(Credential, credential_id)
        if credential is None:
            return None, []
        try:
            secret = (
                credential.get_password(encryption_key)
                if credential.auth_type == "password"
                else credential.get_private_key(encryption_key)
            )
        except CredentialDecryptionError:
            return {"error": "凭证解密失败", "id": credential.id}, []
        auth = {
            "id": credential.id,
            "name": credential.name,
            "username": credential.username,
            "auth_type": credential.auth_type,
            "password": secret if credential.auth_type == "password" else "",
            "private_key": secret if credential.auth_type == "key" else "",
            "ssh_timeout": read_setting(session, "node.ssh_connect_timeout", 10),
            "detect_retries": read_setting(session, "node.detect_retries", 1),
        }
        nodes = session.scalars(
            select(Node)
            .where(Node.credential_id == credential_id, Node.is_deleted.is_(False))
            .order_by(Node.id.asc())
        ).all()
        targets = [
            {
                "id": node.id,
                "hostname": node.hostname,
                "ip": node.ip,
                "port": node.port,
                "nginx_path": node.nginx_path,
                "is_locked": node.is_locked,
            }
            for node in nodes
        ]
    return auth, targets


def _apply_node_test(
    session_factory: sessionmaker,
    credential_id: int,
    result: dict,
) -> None:
    """将连接探测结果写回仍关联凭证的活跃节点。"""
    now = datetime.utcnow()
    with session_scope(session_factory) as session:
        with session.begin():
            node = session.scalar(
                select(Node).where(
                    Node.id == result["node_id"],
                    Node.credential_id == credential_id,
                    Node.is_deleted.is_(False),
                )
            )
            if node is None:
                return
            node.updated_at = now
            if not result["ssh_success"]:
                node.status = "offline"
                return
            node.status = "online"
            node.last_probe_at = now
            node.nginx_available = result["nginx_available"]
            node.last_nginx_probe_at = now
            node.nginx_version = result["nginx_version"] if result["nginx_available"] else ""
            if not result["nginx_available"]:
                mark_node_bindings_orphaned(session, node.id, now)


def _save_credential_test_result(
    session_factory: sessionmaker,
    credential_id: int,
    tested_count: int,
    failed_count: int,
) -> str:
    """保存凭证最近测试时间与 success、partial 或 failed 状态。"""
    if tested_count == 0:
        result = "unknown"
    elif failed_count == 0:
        result = "success"
    elif failed_count >= tested_count:
        result = "failed"
    else:
        result = "partial"
    with session_scope(session_factory) as session:
        with session.begin():
            credential = session.get(Credential, credential_id)
            if credential is not None:
                credential.last_test_time = datetime.utcnow()
                credential.last_test_result = result
    return result


def build_credential_enable_runner(
    session_factory: sessionmaker,
    credential_id: int,
    encryption_key: bytes,
    max_workers: int = _MAX_TEST_WORKERS,
):
    """构造在线程池内执行关联节点测试的任务函数。"""
    def run(context: TaskContext) -> TaskOutcome:
        """逐台测试未锁定节点并持久化进度、结果和日志。"""
        auth, all_nodes = _load_test_targets(session_factory, credential_id, encryption_key)
        if auth is None:
            return TaskOutcome("failed", "凭证已不存在", {"status": "failed"})
        if auth.get("error"):
            _save_credential_test_result(session_factory, credential_id, 0, 0)
            return TaskOutcome("failed", auth["error"], {"status": "failed", "tested": 0})

        nodes = [node for node in all_nodes if not node["is_locked"]]
        skipped_count = len(all_nodes) - len(nodes)
        outcomes = []
        failures = 0
        context.append_log("已发现 {} 台待测节点，锁定跳过 {} 台".format(len(nodes), skipped_count))
        if not nodes:
            _save_credential_test_result(session_factory, credential_id, 0, 0)
            result_tree = {"credential": auth["name"], "summary": {"tested": 0, "success": 0, "failed": 0, "skipped": skipped_count}, "nodes": []}
            context.update_progress(99, "没有可测试节点")
            return TaskOutcome("success", "无可测试节点", result_tree)

        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(nodes)),
            thread_name_prefix="ngxops-credential-test",
        ) as pool:
            futures = {
                pool.submit(_test_node, node, auth, context): node
                for node in nodes
            }
            for index, future in enumerate(as_completed(futures), start=1):
                context.check_cancelled()
                result = future.result()
                _apply_node_test(session_factory, credential_id, result)
                outcomes.append(result)
                if not result["ssh_success"]:
                    failures += 1
                context.append_log(
                    "节点 {}/{} {}：{}".format(
                        index,
                        len(nodes),
                        result["ip"],
                        "SSH 成功" if result["ssh_success"] else "SSH 失败",
                    ),
                    "info" if result["ssh_success"] else "warning",
                )
                context.update_progress(
                    min(99, int(index * 100 / len(nodes))),
                    "已测试 {} / {} 台节点".format(index, len(nodes)),
                )

        _save_credential_test_result(session_factory, credential_id, len(nodes), failures)
        success_count = len(nodes) - failures
        result_tree = {
            "credential": auth["name"],
            "summary": {
                "tested": len(nodes),
                "success": success_count,
                "failed": failures,
                "skipped": skipped_count,
            },
            "nodes": outcomes,
        }
        status = "failed" if failures else "success"
        detail = "测试完成：成功 {}，失败 {}，锁定跳过 {}".format(success_count, failures, skipped_count)
        return TaskOutcome(status, detail, result_tree)

    return run
