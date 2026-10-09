"""探测卸载范围并通过统一任务执行器移除 Nginx。"""

import json
import posixpath
import re
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.orm import joinedload, sessionmaker

from ngxops.configs.services import mark_node_bindings_orphaned
from ngxops.credentials.crypto import CredentialDecryptionError
from ngxops.database.session import session_scope
from ngxops.nginx_install.models import NginxInstallRun
from ngxops.nginx_uninstall.models import NginxUninstallRun
from ngxops.nodes.models import Node
from ngxops.settings.service import read_setting
from ngxops.tasks.executor import TaskCancelled, TaskContext, TaskOutcome
from ngxops.tasks.models import ACTIVE_STATUSES, Task, TaskLog
from ngxops.upgrade.services import _connect_target, _run_remote_command, parse_nginx_v_output


_BATCH_LOCK = threading.Lock()
_BATCH_LIMIT = 3
_WORK_DIR = "/tmp/nginx-upgrade"
_BACKUP_DIR = "/opt/app/mascloud/ansible/mngxops"
_FORBIDDEN_PATHS = frozenset(
    (
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
    )
)
_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")
_PATH_KEY = re.compile(r"^--[A-Za-z0-9-]*-path$")
_TERMINAL = ("success", "failed", "cancelled")


def normalize_remote_path(value: str) -> str:
    """校验并规范化远程绝对路径。"""
    raw = (value or "").strip()
    if (
        not raw.startswith("/")
        or "\x00" in raw
        or "\n" in raw
        or "\r" in raw
        or "\\" in raw
        or ".." in raw.split("/")
    ):
        return ""
    return posixpath.normpath(raw)


def is_dangerous_path(value: str) -> bool:
    """判断目标是否为空或属于禁止删除的系统根目录。"""
    path = normalize_remote_path(value)
    return not path or path in _FORBIDDEN_PATHS


def resolve_nginx_tree_path(value: str) -> str:
    """将配置、模块或日志路径收敛到最右侧的 nginx 目录。"""
    path = normalize_remote_path(value)
    if not path:
        return ""
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[-1] == "nginx" and parts[-2] in ("bin", "sbin"):
        return path
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] == "nginx":
            candidate = "/" + "/".join(parts[: index + 1])
            return path if is_dangerous_path(candidate) else candidate
    return path


def is_file_like_path(value: str) -> bool:
    """判断远程目标是否更像文件而不是目录。"""
    path = normalize_remote_path(value)
    if not path:
        return False
    parts = path.split("/")
    name = parts[-1]
    if name == "nginx" and len(parts) > 1 and parts[-2] in ("bin", "sbin"):
        return True
    return "." in name


def coalesce_delete_targets(targets: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """合并完全重复或被父目录覆盖的删除目标。"""
    kept: List[Tuple[str, str]] = []
    for path, kind in sorted(targets, key=lambda item: (len(item[0]), item[0])):
        if any(path == parent or path.startswith(parent.rstrip("/") + "/") for parent, _ in kept):
            continue
        kept.append((path, kind))
    return kept


def is_valid_package_name(value: str) -> bool:
    """校验 rpm 或 dpkg 查询得到的软件包名。"""
    return bool(value and _PACKAGE_NAME.fullmatch(value.strip()))


def _node_gate(node: Node) -> str:
    """返回节点不能执行卸载时的原因。"""
    if node.is_deleted:
        return "节点已删除"
    if node.is_locked:
        return "节点已锁定"
    if node.status != "online":
        return "节点 SSH 非在线状态"
    if node.nginx_available is not True:
        return "未检测到 Nginx"
    if node.credential is None or not node.credential.is_enabled:
        return "没有可用的 SSH 凭证"
    return ""


def _load_target(
    session_factory: sessionmaker,
    node_id: int,
    encryption_key: bytes,
    *,
    require_available: bool = True,
) -> Tuple[Optional[dict], str]:
    """读取节点快照并在关闭 Session 前解密 SSH 凭证。"""
    with session_scope(session_factory) as session:
        node = session.scalar(
            select(Node)
            .options(joinedload(Node.credential))
            .where(Node.id == node_id, Node.is_deleted.is_(False))
        )
        if node is None:
            return None, "节点不存在或已删除"
        gate = _node_gate(node)
        if not require_available and gate == "未检测到 Nginx":
            gate = ""
        if gate:
            return None, gate
        credential = node.credential
        try:
            secret = (
                credential.get_password(encryption_key)
                if credential.auth_type == "password"
                else credential.get_private_key(encryption_key)
            )
        except CredentialDecryptionError:
            return None, "SSH 凭证解密失败"
        return {
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "port": node.port,
            "nginx_path": node.nginx_path or "",
            "credential_id": node.credential_id,
            "username": credential.username,
            "auth_type": credential.auth_type,
            "password": secret if credential.auth_type == "password" else "",
            "private_key": secret if credential.auth_type != "password" else "",
            "locked": node.is_locked,
            "ssh_timeout": read_setting(session, "node.ssh_connect_timeout", 10),
            "detect_retries": read_setting(session, "node.detect_retries", 1),
        }, ""


def _prefix_fallback(session_factory: sessionmaker, node_id: int, nginx_path: str) -> str:
    """从节点二进制路径或最近成功安装记录推导源码 prefix。"""
    binary = normalize_remote_path(nginx_path)
    if binary.endswith("/sbin/nginx"):
        prefix = binary[: -len("/sbin/nginx")]
        if prefix and not is_dangerous_path(prefix):
            return prefix
    with session_scope(session_factory) as session:
        run = session.scalar(
            select(NginxInstallRun)
            .join(Task, Task.id == NginxInstallRun.task_id)
            .where(
                NginxInstallRun.node_id == node_id,
                Task.status == "success",
            )
            .order_by(NginxInstallRun.created_at.desc(), NginxInstallRun.id.desc())
            .limit(1)
        )
        prefix = normalize_remote_path(run.target_prefix) if run else ""
    return prefix if prefix and not is_dangerous_path(prefix) else ""


def _backup_path(hostname: str, backup_dir: str = _BACKUP_DIR) -> str:
    """按节点主机名生成发布备份目录。"""
    safe_host = re.sub(r"[^A-Za-z0-9._-]", "_", hostname or "node")[:100]
    return posixpath.join(backup_dir, safe_host)


def _run_command(client, command: str, context: Optional[TaskContext] = None, timeout: int = 45):
    """运行有超时限制的 SSH 命令并返回退出码和输出。"""
    return _run_remote_command(client, command, timeout=timeout, context=context)


def _package_origin(client, binary: str, context: Optional[TaskContext] = None) -> dict:
    """查询 nginx 二进制的软件包归属并返回安全摘要。"""
    path = normalize_remote_path(binary)
    if not path:
        status, output = _run_command(client, "command -v nginx 2>/dev/null || true", context)
        lines = output.strip().splitlines()
        path = normalize_remote_path(lines[-1].strip()) if status == 0 and lines else ""
    result = {"origin": "source", "manager": "", "package": "", "binary": path}
    if not path:
        return result
    quoted = shlex.quote(path)
    status, output = _run_command(
        client,
        "rpm -qf --queryformat '%{NAME}' {} 2>/dev/null".format(quoted),
        context,
    )
    name = output.strip().splitlines()[-1].strip() if output.strip() else ""
    if status == 0 and is_valid_package_name(name):
        result.update({"origin": "package", "manager": "rpm", "package": name})
        return result
    status, output = _run_command(client, "dpkg -S {} 2>/dev/null".format(quoted), context)
    first = output.strip().splitlines()[0] if output.strip() else ""
    name = first.split(":", 1)[0].split(",", 1)[0].strip()
    if status == 0 and is_valid_package_name(name):
        result.update({"origin": "package", "manager": "deb", "package": name})
    return result


def _configure_paths(output: str, prefix_fallback: str) -> Tuple[str, List[dict]]:
    """解析 nginx -V 的 prefix 和可选绝对路径。"""
    parsed = parse_nginx_v_output(output or "")
    paths = []
    seen = set()
    prefix = ""
    for token in parsed.get("params", []):
        if not isinstance(token, str) or "=" not in token:
            continue
        key, raw_path = token.split("=", 1)
        key = key.strip()
        path = normalize_remote_path(raw_path.strip().strip("'\""))
        if not path or (key != "--prefix" and not _PATH_KEY.fullmatch(key)):
            continue
        required = key == "--prefix"
        path = path if required else resolve_nginx_tree_path(path)
        if path in seen:
            continue
        seen.add(path)
        if required:
            prefix = path
        paths.append(
            {
                "key": "prefix" if required else key,
                "label": "--prefix" if required else key,
                "path": path,
                "kind": "dir" if required or not is_file_like_path(path) else "file",
                "required": required,
                "checked": required or key in ("--sbin-path", "--modules-path", "--conf-path", "--pid-path"),
                "editable": required,
            }
        )
    if not prefix:
        parsed_prefix = (
            normalize_remote_path(parsed.get("prefix", ""))
            if parsed.get("configure_opts")
            else ""
        )
        prefix = parsed_prefix or prefix_fallback
        if prefix:
            paths.insert(
                0,
                {
                    "key": "prefix",
                    "label": "--prefix",
                    "path": prefix,
                    "kind": "dir",
                    "required": True,
                    "checked": True,
                    "editable": True,
                },
            )
    return prefix, paths


def _systemd_info(client, context: Optional[TaskContext] = None) -> dict:
    """识别活动或启用的 nginx systemd unit 及管理能力。"""
    status, _ = _run_command(client, "command -v systemctl >/dev/null 2>&1", context)
    info = {"mode": "binary", "unit": "", "running": False, "can_manage": False, "use_sudo": False}
    if status != 0:
        return info
    unit = ""
    running = False
    for candidate in ("nginx", "nginx.service"):
        _status, output = _run_command(
            client, "systemctl is-active {} 2>/dev/null || true".format(shlex.quote(candidate)), context
        )
        state = output.strip().splitlines()[-1] if output.strip() else ""
        if state in ("active", "activating", "reloading"):
            unit, running = candidate, True
            break
        _status, output = _run_command(
            client, "systemctl is-enabled {} 2>/dev/null || true".format(shlex.quote(candidate)), context
        )
        state = output.strip().splitlines()[-1] if output.strip() else ""
        if state in ("enabled", "enabled-runtime", "static"):
            unit = candidate
    if unit:
        info.update({"mode": "systemctl", "unit": unit, "running": running})
    status, _ = _run_command(client, "test -w /etc/systemd/system", context)
    if status == 0:
        info.update({"can_manage": True, "use_sudo": False})
    else:
        status, _ = _run_command(client, "sudo -n true >/dev/null 2>&1", context)
        info.update({"can_manage": status == 0, "use_sudo": status == 0})
    return info


def _inspect_target(target: dict, context: Optional[TaskContext] = None) -> dict:
    """使用一条 SSH 会话探测安装来源、删除路径和运行状态。"""
    fallback = _prefix_fallback(
        target["session_factory"], target["id"], target["nginx_path"]
    ) if target.get("session_factory") else ""
    client = _connect_target(target, context)
    try:
        binary = target["nginx_path"] or "nginx"
        status, output = _run_command(
            client, "{} -V 2>&1".format(shlex.quote(binary)), context, timeout=45
        )
        prefix, paths = _configure_paths(output if status == 0 else "", fallback)
        package = _package_origin(client, binary, context)
        systemd = _systemd_info(client, context)
        if systemd["mode"] == "systemctl":
            running = systemd["running"]
        else:
            run_status, _output = _run_command(
                client, "pgrep -x nginx >/dev/null 2>&1", context
            )
            running = run_status == 0
        return {
            "prefix": prefix,
            "paths": paths,
            "package": package,
            "systemd": systemd,
            "running": running,
            "version": parse_nginx_v_output(output or "").get("version", "") if status == 0 else "",
        }
    finally:
        client.close()


def _preview_target(
    session_factory: sessionmaker,
    node_id: int,
    encryption_key: bytes,
    work_dir: str,
    backup_dir: str,
) -> dict:
    """构造单节点卸载预览，不将凭证或远程异常原文返回给页面。"""
    target, gate = _load_target(session_factory, node_id, encryption_key)
    with session_scope(session_factory) as session:
        node = session.get(Node, node_id)
        if node is None:
            return {"id": node_id, "eligible": False, "gate_message": gate or "节点不存在"}
        snapshot = {
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "nginx_path": node.nginx_path,
            "nginx_available": node.nginx_available,
        }
    result = {
        **snapshot,
        "eligible": False,
        "gate_message": gate,
        "prefix": "",
        "prefix_source": "",
        "paths": [],
        "running": False,
        "running_error": "",
        "install_origin": "unknown",
        "package_manager": "",
        "package_name": "",
        "manage_mode": "unknown",
        "manage_unit": "",
        "can_manage_systemd": False,
        "credential_username": "",
        "backup_path": _backup_path(snapshot["hostname"], backup_dir),
        "work_dir": work_dir,
        "modules_dir": posixpath.join(work_dir, "nginx-modules"),
        "shallow_prefix": False,
    }
    if gate:
        return result
    result["credential_username"] = target["username"]
    target["session_factory"] = session_factory
    try:
        inspected = _inspect_target(target)
    except Exception as exc:
        result["gate_message"] = "远程预览失败（{}）".format(type(exc).__name__)
        result["running_error"] = result["gate_message"]
        return result
    package = inspected["package"]
    prefix = inspected["prefix"]
    origin = package["origin"]
    paths = inspected["paths"]
    if origin == "package":
        for item in paths:
            item.update({"checked": True, "required": True, "editable": False, "package_owned": True})
    result.update(
        {
            "eligible": origin == "package" or bool(prefix and not is_dangerous_path(prefix)),
            "gate_message": "" if origin == "package" or (prefix and not is_dangerous_path(prefix)) else "无法解析安全的源码安装路径",
            "prefix": prefix,
            "prefix_source": "nginx -V" if prefix else "",
            "paths": paths + _setting_paths(result),
            "running": inspected["running"],
            "install_origin": origin,
            "package_manager": package["manager"],
            "package_name": package["package"],
            "manage_mode": inspected["systemd"]["mode"],
            "manage_unit": inspected["systemd"]["unit"],
            "can_manage_systemd": inspected["systemd"]["can_manage"],
            "shallow_prefix": origin == "source" and len([part for part in prefix.split("/") if part]) == 1,
        }
    )
    return result


def _setting_paths(preview: dict) -> List[dict]:
    """追加发布备份和编译目录清理选项。"""
    return [
        {"key": "release_backup", "label": "发布备份目录", "path": preview["backup_path"], "kind": "dir", "required": False, "checked": False, "editable": False},
        {"key": "work_dir", "label": "编译工作目录", "path": preview["work_dir"], "kind": "dir", "required": False, "checked": False, "editable": False},
        {"key": "nginx_modules", "label": "第三方模块源码目录", "path": preview["modules_dir"], "kind": "dir", "required": False, "checked": False, "editable": False},
    ]


def preview_nodes(
    session_factory: sessionmaker,
    encryption_key: bytes,
    node_ids: List[int],
    batch_limit: int = _BATCH_LIMIT,
    work_dir: str = _WORK_DIR,
    backup_dir: str = _BACKUP_DIR,
) -> dict:
    """并发预览选中节点的卸载来源、路径、托管方式和运行态。"""
    unique_ids = list(dict.fromkeys(node_ids))
    if not unique_ids:
        raise ValueError("请选择至少一个目标节点")
    if len(unique_ids) > batch_limit:
        raise ValueError("单次最多选择 {} 台节点".format(batch_limit))
    results = {}
    with ThreadPoolExecutor(max_workers=min(batch_limit, len(unique_ids))) as pool:
        futures = {
            pool.submit(
                _preview_target,
                session_factory,
                node_id,
                encryption_key,
                work_dir,
                backup_dir,
            ): node_id
            for node_id in unique_ids
        }
        for future in as_completed(futures):
            node_id = futures[future]
            try:
                results[node_id] = future.result()
            except Exception as exc:
                results[node_id] = {
                    "id": node_id,
                    "eligible": False,
                    "gate_message": "预览失败（{}）".format(type(exc).__name__),
                    "paths": [],
                }
    return {"nodes": [results[node_id] for node_id in unique_ids], "batch_max_count": batch_limit}


def _validate_run_options(
    item: dict, node: Node, work_dir: str, backup_dir: str
) -> dict:
    """校验页面确认的路径并只保存服务端认可的清理选项。"""
    origin = item.get("install_origin")
    if origin not in ("source", "package"):
        raise ValueError("卸载来源无效")
    prefix = normalize_remote_path(item.get("prefix", ""))
    if origin == "source" and is_dangerous_path(prefix):
        raise ValueError("源码安装路径为空或属于禁止删除的系统目录")
    package_name = str(item.get("package_name") or "").strip()
    package_manager = str(item.get("package_manager") or "").strip()
    if origin == "package" and (
        not is_valid_package_name(package_name) or package_manager not in ("rpm", "deb")
    ):
        raise ValueError("软件包归属信息无效，请重新执行预览")
    selected = item.get("extra_paths") or []
    if not isinstance(selected, list) or len(selected) > 100:
        raise ValueError("额外路径选择无效")
    extra_paths = []
    if origin == "source":
        for entry in selected:
            key = str(entry.get("key") or "").strip()
            path = normalize_remote_path(entry.get("path") or "")
            if not _PATH_KEY.fullmatch(key) or is_dangerous_path(path):
                raise ValueError("额外路径选择无效或不安全")
            if path != prefix and not path.startswith(prefix.rstrip("/") + "/"):
                extra_paths.append({"key": key, "path": resolve_nginx_tree_path(path)})
    backup_path = _backup_path(node.hostname, backup_dir)
    if item.get("remove_backup") and is_dangerous_path(backup_path):
        raise ValueError("发布备份路径不安全")
    if item.get("remove_workdir") and is_dangerous_path(work_dir):
        raise ValueError("编译工作目录路径不安全")
    if item.get("remove_modules") and is_dangerous_path(posixpath.join(work_dir, "nginx-modules")):
        raise ValueError("第三方模块路径不安全")
    return {
        "install_origin": origin,
        "package_manager": package_manager if origin == "package" else "",
        "package_name": package_name if origin == "package" else "",
        "prefix": prefix,
        "backup_path": backup_path if item.get("remove_backup") else "",
        "work_dir": work_dir,
        "remove_backup": bool(item.get("remove_backup")),
        "remove_workdir": bool(item.get("remove_workdir")),
        "remove_modules": bool(item.get("remove_modules")),
        "stop_if_running": bool(item.get("stop_if_running", True)),
        "extra_paths": extra_paths,
    }


def _next_batch_number(session, now: datetime) -> str:
    """生成当天递增的卸载批次号。"""
    prefix = "UN-{}-".format(now.strftime("%y%m%d"))
    latest = session.scalar(
        select(NginxUninstallRun.batch_number)
        .where(NginxUninstallRun.batch_number.like(prefix + "%"))
        .order_by(NginxUninstallRun.batch_number.desc())
        .limit(1)
    )
    try:
        sequence = int(latest.rsplit("-", 1)[-1]) + 1 if latest else 1
    except (TypeError, ValueError):
        sequence = 1
    return "{}{:04d}".format(prefix, sequence)


def create_uninstall_batch(
    session_factory: sessionmaker,
    executor,
    encryption_key: bytes,
    user_id: int,
    items: List[dict],
    batch_limit: int = _BATCH_LIMIT,
) -> dict:
    """校验卸载确认数据、创建历史快照并提交每节点任务。"""
    if not items:
        raise ValueError("请选择至少一个目标节点")
    if len(items) > batch_limit:
        raise ValueError("单次最多选择 {} 台节点".format(batch_limit))
    node_ids = [int(item.get("id") or 0) for item in items]
    if any(node_id < 1 for node_id in node_ids) or len(set(node_ids)) != len(node_ids):
        raise ValueError("目标节点无效或重复")
    task_ids = []
    skipped = []
    with session_scope(session_factory) as settings_session:
        work_dir = read_setting(
            settings_session, "upgrade.default_work_dir", _WORK_DIR
        )
        backup_dir = read_setting(
            settings_session, "release.backup_dir", _BACKUP_DIR
        )
    with _BATCH_LOCK:
        with session_scope(session_factory) as session:
            with session.begin():
                nodes = session.scalars(
                    select(Node)
                    .options(joinedload(Node.credential))
                    .where(Node.id.in_(node_ids), Node.is_deleted.is_(False))
                    .order_by(Node.id.asc())
                ).unique().all()
                by_id = {node.id: node for node in nodes}
                if set(by_id) != set(node_ids):
                    raise ValueError("部分目标节点不存在或已删除")
                active_ids = set(
                    session.scalars(
                        select(NginxUninstallRun.node_id)
                        .join(Task, Task.id == NginxUninstallRun.task_id)
                        .where(
                            NginxUninstallRun.node_id.in_(node_ids),
                            Task.status.in_(ACTIVE_STATUSES),
                        )
                    ).all()
                )
                prepared = []
                for item in items:
                    node = by_id[int(item["id"])]
                    gate = _node_gate(node)
                    if gate:
                        skipped.append({"id": node.id, "hostname": node.hostname, "reason": gate})
                        continue
                    if node.id in active_ids:
                        skipped.append({"id": node.id, "hostname": node.hostname, "reason": "已有进行中的卸载任务"})
                        continue
                    try:
                        options = _validate_run_options(
                            item, node, work_dir, backup_dir
                        )
                    except ValueError as exc:
                        skipped.append({"id": node.id, "hostname": node.hostname, "reason": str(exc)})
                        continue
                    prepared.append((node, options))
                if not prepared:
                    raise ValueError("没有可执行的节点：{}".format("；".join(item["reason"] for item in skipped[:5])))
                batch_number = _next_batch_number(session, datetime.utcnow())
                for node, options in prepared:
                    task = Task(
                        operation_type="nginx_uninstall",
                        detail="卸载任务已创建，等待执行",
                        source_batch=batch_number,
                        target_hostnames=node.hostname,
                        target_ips=node.ip,
                        target_configs="{}:{}".format(options["install_origin"], options["package_name"] or options["prefix"]),
                        subject_type="node",
                        subject_id=node.id,
                        trigger_user_id=user_id,
                    )
                    session.add(task)
                    session.flush()
                    session.add(TaskLog(task_id=task.id, message="卸载任务已加入执行队列"))
                    session.add(
                        NginxUninstallRun(
                            task_id=task.id,
                            node_id=node.id,
                            batch_number=batch_number,
                            node_hostname=node.hostname,
                            node_ip=node.ip,
                            install_origin=options["install_origin"],
                            package_manager=options["package_manager"],
                            package_name=options["package_name"],
                            resolved_prefix=options["prefix"],
                            backup_path=options["backup_path"],
                            work_dir=options["work_dir"],
                            options_json=json.dumps(options, ensure_ascii=False),
                        )
                    )
                    task_ids.append(task.id)
        for task_id in task_ids:
            try:
                executor.submit(
                    task_id,
                    create_uninstall_runner(session_factory, encryption_key, task_id),
                )
            except RuntimeError:
                with session_scope(session_factory) as session:
                    with session.begin():
                        session.execute(
                            update(Task)
                            .where(Task.id == task_id, Task.status == "pending")
                            .values(status="failed", progress=100, detail="卸载任务无法启动", finished_at=datetime.utcnow())
                        )
                        session.add(TaskLog(task_id=task_id, level="error", message="卸载任务无法启动"))
    return {"batch_number": batch_number, "task_ids": task_ids, "skipped": skipped}


def _load_run_target(session_factory: sessionmaker, task_id: int, encryption_key: bytes):
    """加载卸载任务、运行快照和仍可用的节点凭证。"""
    with session_scope(session_factory) as session:
        run = session.scalar(
            select(NginxUninstallRun)
            .options(joinedload(NginxUninstallRun.node).joinedload(Node.credential))
            .where(NginxUninstallRun.task_id == task_id)
        )
        if run is None or run.node is None:
            return None, None, "节点或卸载任务不存在"
        gate = _node_gate(run.node)
        if gate:
            return None, None, gate
        credential = run.node.credential
        try:
            secret = (
                credential.get_password(encryption_key)
                if credential.auth_type == "password"
                else credential.get_private_key(encryption_key)
            )
        except CredentialDecryptionError:
            return None, None, "SSH 凭证解密失败"
        target = {
            "id": run.node.id,
            "hostname": run.node.hostname,
            "ip": run.node.ip,
            "port": run.node.port,
            "nginx_path": run.node.nginx_path or "",
            "credential_id": run.node.credential_id,
            "username": credential.username,
            "auth_type": credential.auth_type,
            "password": secret if credential.auth_type == "password" else "",
            "private_key": secret if credential.auth_type != "password" else "",
            "resolved_prefix": run.resolved_prefix,
            "ssh_timeout": read_setting(session, "node.ssh_connect_timeout", 10),
            "detect_retries": read_setting(session, "node.detect_retries", 1),
        }
        snapshot = {
            "origin": run.install_origin,
            "package_manager": run.package_manager,
            "package": run.package_name,
            "prefix": run.resolved_prefix,
            "backup_path": run.backup_path,
            "work_dir": run.work_dir,
            "options": json.loads(run.options_json or "{}"),
        }
    return target, snapshot, ""


def _phase(context: TaskContext, progress: int, detail: str) -> None:
    """更新任务进度并将当前阶段写入任务摘要。"""
    context.update_progress(progress, detail=detail)
    context.append_log(detail)


def _privileged(command: str, use_sudo: bool) -> str:
    """为需要管理员权限的命令添加免密 sudo。"""
    return "sudo -n {}".format(command) if use_sudo else command


def _require_command(client, command: str, context: TaskContext, label: str, *, use_sudo: bool = False, timeout: int = 120):
    """执行远程命令并把非零退出统一转换为业务错误。"""
    status, output = _run_command(client, _privileged(command, use_sudo), context, timeout)
    if status != 0:
        detail = " ".join((output or "").split())[-800:]
        raise ValueError("{}失败{}".format(label, "：" + detail if detail else ""))
    return output


def _systemd_remove(client, unit: str, use_sudo: bool, context: TaskContext) -> None:
    """禁用 unit 并仅清理 /etc/systemd/system 下可管理的文件。"""
    safe_unit = shlex.quote(unit.replace(".service", ""))
    _require_command(client, "systemctl disable {} 2>&1 || true".format(safe_unit), context, "禁用 systemd unit", use_sudo=use_sudo)
    _status, output = _run_command(
        client,
        "systemctl show -p FragmentPath --value {} 2>/dev/null || true".format(safe_unit),
        context,
    )
    fragment = output.strip().splitlines()[-1].strip() if output.strip() else ""
    paths = {"/etc/systemd/system/nginx.service"}
    if fragment.startswith("/etc/systemd/system/"):
        paths.add(fragment)
    for path in sorted(paths):
        if is_dangerous_path(path):
            raise ValueError("拒绝清理不安全的 systemd 路径")
        quoted = shlex.quote(path)
        status, _ = _run_command(client, "test -e {}".format(quoted), context)
        if status == 0:
            _require_command(client, "rm -f {}".format(quoted), context, "删除 systemd unit", use_sudo=use_sudo)
    _require_command(client, "systemctl daemon-reload", context, "systemd daemon-reload", use_sudo=use_sudo)


def _delete_remote_path(client, path: str, kind: str, context: TaskContext) -> str:
    """删除一个通过安全校验的文件或目录，不存在时按成功跳过。"""
    normalized = normalize_remote_path(path)
    if is_dangerous_path(normalized):
        raise ValueError("拒绝删除危险路径：{}".format(path))
    quoted = shlex.quote(normalized)
    status, _output = _run_command(client, "test -e {}".format(quoted), context)
    if status != 0:
        context.append_log("路径不存在，跳过：{}".format(normalized))
        return "skipped"
    command = "rm -f {}".format(quoted) if kind == "file" else "rm -rf {}".format(quoted)
    _require_command(client, command, context, "删除路径 {}".format(normalized), timeout=120)
    context.append_log("已删除：{}".format(normalized))
    return "removed"


def _apply_uninstall_state(session_factory: sessionmaker, target: dict) -> None:
    """在一个事务中清除节点 Nginx 信息并将绑定转为 orphaned。"""
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
            node.nginx_path = ""
            node.nginx_version = ""
            node.nginx_available = False
            node.last_nginx_probe_at = now
            node.updated_at = now
            mark_node_bindings_orphaned(session, node.id, now)


def _run_uninstall(
    session_factory: sessionmaker,
    encryption_key: bytes,
    task_id: int,
    context: TaskContext,
) -> TaskOutcome:
    """执行单节点卸载并返回脱敏结果树。"""
    target, snapshot, error = _load_run_target(session_factory, task_id, encryption_key)
    result = {"node_id": None, "hostname": "", "ip": "", "install_origin": "", "removed_paths": [], "message": ""}
    if error:
        return TaskOutcome("failed", error, {"nodes": [dict(result, message=error)]})
    result.update({"node_id": target["id"], "hostname": target["hostname"], "ip": target["ip"], "install_origin": snapshot["origin"]})
    options = snapshot["options"]
    removed = False
    client = None
    try:
        context.check_cancelled()
        _phase(context, 8, "正在连接远程节点")
        client = _connect_target(target, context)
        inspected = _inspect_client(client, target, context)
        if inspected["package"]["origin"] != snapshot["origin"]:
            raise ValueError("安装来源与预览结果不同，请重新预览后再执行")
        if snapshot["origin"] == "package" and inspected["package"]["package"] != snapshot["package"]:
            raise ValueError("软件包归属已变化，请重新预览后再执行")
        if inspected["running"]:
            if not options.get("stop_if_running", True):
                raise ValueError("Nginx 仍在运行，请确认先停止服务后再卸载")
            _phase(context, 20, "停止 Nginx")
            if inspected["systemd"]["mode"] == "systemctl" and inspected["systemd"]["unit"]:
                use_sudo = inspected["systemd"]["use_sudo"]
                _require_command(
                    client,
                    "systemctl stop {}".format(shlex.quote(inspected["systemd"]["unit"])),
                    context,
                    "停止 Nginx",
                    use_sudo=use_sudo,
                )
            else:
                binary = shlex.quote(target["nginx_path"] or "nginx")
                status, _output = _run_command(client, "{} -s quit".format(binary), context, 90)
                if status != 0:
                    _require_command(client, "{} -s stop".format(binary), context, "停止 Nginx", timeout=90)
            context.append_log("Nginx 已停止")
        context.check_cancelled()
        if snapshot["origin"] == "package":
            _phase(context, 45, "通过包管理器卸载 Nginx")
            systemd = inspected["systemd"]
            use_sudo = systemd["use_sudo"]
            if target["username"] != "root" and not use_sudo:
                status, _ = _run_command(client, "sudo -n true >/dev/null 2>&1", context)
                if status != 0:
                    raise ValueError("软件包卸载需要 root 或可用的免密 sudo")
                use_sudo = True
            package = shlex.quote(snapshot["package"])
            if snapshot["package_manager"] == "deb":
                command = "env DEBIAN_FRONTEND=noninteractive apt-get remove -y {} 2>&1".format(package)
            else:
                status, _ = _run_command(client, "command -v dnf >/dev/null 2>&1", context)
                if status == 0:
                    manager = "dnf"
                else:
                    status, _ = _run_command(client, "command -v yum >/dev/null 2>&1", context)
                    if status != 0:
                        raise ValueError("未找到 dnf 或 yum，无法卸载软件包")
                    manager = "yum"
                command = "{} remove -y {} 2>&1".format(manager, package)
            _require_command(client, command, context, "包管理器卸载", use_sudo=use_sudo, timeout=900)
            removed = True
            result["removed_paths"].append("package:{}".format(snapshot["package"]))
        else:
            prefix = snapshot["prefix"]
            if is_dangerous_path(prefix):
                raise ValueError("禁止删除路径：{}".format(prefix))
            _phase(context, 45, "删除源码安装目录")
            outcome = _delete_remote_path(client, prefix, "dir", context)
            result["removed_paths"].append({"path": prefix, "outcome": outcome})
            removed = True
        context.check_cancelled()
        extra_targets = []
        if options.get("remove_backup"):
            extra_targets.append((snapshot["backup_path"], "dir"))
        if options.get("remove_workdir"):
            extra_targets.append((snapshot["work_dir"], "dir"))
        if options.get("remove_modules"):
            extra_targets.append((posixpath.join(snapshot["work_dir"], "nginx-modules"), "dir"))
        if snapshot["origin"] == "source":
            discovered = {item["key"]: item["path"] for item in inspected["paths"]}
            for item in options.get("extra_paths", []):
                if discovered.get(item["key"]) != item["path"]:
                    raise ValueError("Nginx 路径与预览时不同，请重新预览")
                extra_targets.append((item["path"], "file" if is_file_like_path(item["path"]) else "dir"))
        prefix = snapshot["prefix"]
        coalesced = []
        for path, kind in extra_targets:
            normalized = resolve_nginx_tree_path(path)
            if not normalized or normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/"):
                continue
            if is_dangerous_path(normalized):
                raise ValueError("拒绝清理危险路径：{}".format(normalized))
            coalesced.append((normalized, kind))
        backup = snapshot["backup_path"]
        backup_targets = [target_item for target_item in coalesce_delete_targets(coalesced) if target_item[0] == backup]
        other_targets = [target_item for target_item in coalesce_delete_targets(coalesced) if target_item[0] != backup]
        if backup_targets:
            _phase(context, 65, "清理发布备份")
            for path, kind in backup_targets:
                context.check_cancelled()
                outcome = _delete_remote_path(client, path, kind, context)
                result["removed_paths"].append({"path": path, "outcome": outcome})
        if other_targets:
            _phase(context, 75, "清理额外路径")
            for path, kind in other_targets:
                context.check_cancelled()
                outcome = _delete_remote_path(client, path, kind, context)
                result["removed_paths"].append({"path": path, "outcome": outcome})
        if snapshot["origin"] == "source" and inspected["systemd"]["mode"] == "systemctl":
            _phase(context, 85, "清理 systemd 托管")
            if not inspected["systemd"]["can_manage"]:
                raise ValueError("当前账号无权限清理 systemd unit")
            _systemd_remove(
                client,
                inspected["systemd"]["unit"] or "nginx",
                inspected["systemd"]["use_sudo"],
                context,
            )
        elif snapshot["origin"] == "package":
            status, output = _run_command(
                client,
                "test -e /etc/systemd/system/nginx.service && echo EXISTS || echo MISSING",
                context,
            )
            if status == 0 and "EXISTS" in output:
                _phase(context, 85, "清理平台托管 systemd unit")
                systemd = inspected["systemd"]
                if not systemd["can_manage"]:
                    raise ValueError("存在平台 systemd unit，但当前账号无权限清理")
                _systemd_remove(client, "nginx", systemd["use_sudo"], context)
        context.check_cancelled()
        _phase(context, 95, "更新节点 Nginx 状态与配置绑定")
        _apply_uninstall_state(session_factory, target)
        result["message"] = "Nginx 已卸载，配置绑定已标记为远程已删除"
        context.append_log(result["message"])
        return TaskOutcome("success", result["message"], {"nodes": [result]})
    except TaskCancelled:
        if removed:
            _apply_uninstall_state(session_factory, target)
        raise
    except Exception as exc:
        if removed:
            _apply_uninstall_state(session_factory, target)
        message = str(exc) if isinstance(exc, ValueError) else "卸载失败（{}）".format(type(exc).__name__)
        result["message"] = message[:1000]
        context.append_log(message[:1000], "error")
        return TaskOutcome("failed", message[:2000], {"nodes": [result]})
    finally:
        if client is not None:
            client.close()


def _inspect_client(client, target: dict, context: Optional[TaskContext] = None) -> dict:
    """复用已建立的 SSH 会话完成来源、路径、systemd 和运行态探测。"""
    fallback = normalize_remote_path(target.get("resolved_prefix", ""))
    binary = target["nginx_path"] or "nginx"
    status, output = _run_command(client, "{} -V 2>&1".format(shlex.quote(binary)), context)
    prefix, paths = _configure_paths(output if status == 0 else "", fallback)
    package = _package_origin(client, binary, context)
    systemd = _systemd_info(client, context)
    if systemd["mode"] == "systemctl":
        running = systemd["running"]
    else:
        run_status, _ = _run_command(client, "pgrep -x nginx >/dev/null 2>&1", context)
        running = run_status == 0
    return {"prefix": prefix, "paths": paths, "package": package, "systemd": systemd, "running": running}


def create_uninstall_runner(session_factory, encryption_key: bytes, task_id: int):
    """构造绑定卸载任务 ID 的统一异步执行回调。"""
    def run(context: TaskContext) -> TaskOutcome:
        """执行一个节点的卸载流水线。"""
        return _run_uninstall(session_factory, encryption_key, task_id, context)

    return run


def load_uninstall_batch(session_factory: sessionmaker, batch_number: str) -> List[dict]:
    """读取卸载批次内各节点任务的实时状态。"""
    with session_scope(session_factory) as session:
        rows = session.execute(
            select(NginxUninstallRun, Task)
            .join(Task, Task.id == NginxUninstallRun.task_id)
            .where(NginxUninstallRun.batch_number == batch_number)
            .order_by(NginxUninstallRun.id.asc())
        ).all()
        return [
            {
                "task_id": task.id,
                "node_id": run.node_id,
                "hostname": run.node_hostname,
                "ip": run.node_ip,
                "status": task.status,
                "progress": task.progress,
                "detail": task.detail,
                "finished": task.status in _TERMINAL,
            }
            for run, task in rows
        ]
