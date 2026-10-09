"""执行配置发现、单节点同步和批量节点同步任务。"""

import shlex
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Set, Tuple

import paramiko
from sqlalchemy import select, update
from sqlalchemy.orm import joinedload, sessionmaker

from ngxops.configs.discovery import discover_remote_configs
from ngxops.configs.models import (
    BindingVersion,
    Config,
    ConfigBinding,
)
from ngxops.credentials.crypto import CredentialDecryptionError
from ngxops.credentials.models import Credential
from ngxops.credentials.tasks import _connect_ssh
from ngxops.database.session import session_scope
from ngxops.nodes.models import Node, NodeSyncSetting
from ngxops.tasks.executor import (
    TaskContext,
    TaskOutcome,
    create_task,
)
from ngxops.settings.service import read_setting


DEFAULT_MAIN_CONF_PATH = "/etc/nginx/nginx.conf"
MAX_SYNC_WORKERS = 3
MAX_RESULT_DETAIL_ITEMS = 75
MAX_RESULT_ERROR_ITEMS = 20
MAX_PREVIEW_PATHS = 300


def _load_target(
    session_factory: sessionmaker,
    node_id: int,
    encryption_key: bytes,
    path_override: Optional[str] = None,
) -> Tuple[Optional[dict], str]:
    """加载节点与凭证运行快照并在 Session 关闭前解密凭证。"""
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
            return None, "节点 {} 非在线状态".format(node.hostname)
        if node.nginx_available is not True:
            return None, "节点 {} 未确认 Nginx 可用".format(node.hostname)
        credential = node.credential
        if credential is None:
            return None, "节点未配置 SSH 凭证"
        if not credential.is_enabled:
            return None, "关联凭证已禁用"
        setting = session.get(NodeSyncSetting, node.id)
        main_conf_path = (
            path_override
            or (
                setting.main_conf_path
                if setting is not None and setting.main_conf_path
                else ""
            )
            or read_setting(
                session, "config.default_nginx_path", DEFAULT_MAIN_CONF_PATH
            )
        )
        target = {
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "port": node.port,
            "credential_id": node.credential_id,
            "username": credential.username,
            "auth_type": credential.auth_type,
            "password": "",
            "private_key": "",
            "main_conf_path": main_conf_path,
            "max_discover_depth": read_setting(
                session, "config.discover_max_depth", 3
            ),
            "ssh_timeout": read_setting(session, "node.ssh_connect_timeout", 10),
            "detect_retries": read_setting(session, "node.detect_retries", 1),
        }
        try:
            if credential.auth_type == "password":
                target["password"] = credential.get_password(encryption_key)
            else:
                target["private_key"] = credential.get_private_key(encryption_key)
        except CredentialDecryptionError:
            return None, "SSH 凭证解密失败"
    return target, ""


def _public_files(files: Sequence[dict]) -> List[dict]:
    """移除远程配置正文并构造有界的路径清单。"""
    return [
        {"path": item["path"]}
        for item in files[:MAX_PREVIEW_PATHS]
    ]


def _discovery_result_tree(
    node: dict,
    files: Sequence[dict],
    errors: Sequence[dict],
) -> dict:
    """构造不含配置正文的发现结果树。"""
    return {
        "summary": {
            "total": len(files) + len(errors),
            "success": len(files),
            "failed": len(errors),
        },
        "nodes": [
            {
                "node_id": node["id"],
                "hostname": node["hostname"],
                "ip": node["ip"],
                "files": _public_files(files),
                "omitted_file_count": max(len(files) - MAX_PREVIEW_PATHS, 0),
                "errors": list(errors),
            }
        ],
    }


def _run_discovery(
    context: TaskContext,
    session_factory: sessionmaker,
    node_id: int,
    encryption_key: bytes,
    main_conf_path: str,
) -> TaskOutcome:
    """执行只读远程发现并将路径清单写入任务结果。"""
    target, error = _load_target(
        session_factory,
        node_id,
        encryption_key,
        path_override=main_conf_path,
    )
    if target is None:
        return TaskOutcome(
            "failed",
            error,
            {"summary": {"total": 1, "success": 0, "failed": 1}, "nodes": []},
        )
    context.update_progress(5, "正在连接 {}".format(target["hostname"]))

    def report_progress(count: int, current_path: str) -> None:
        """记录发现数量和当前路径的实际扫描进度。"""
        progress = min(90, 10 + count // 5)
        context.update_progress(progress, "已发现 {} 个文件".format(count))
        context.append_log("发现配置 {}".format(current_path))

    files, errors = discover_remote_configs(
        target,
        main_conf_path,
        context,
        progress_callback=report_progress,
        max_depth=target["max_discover_depth"],
    )
    context.check_cancelled()
    for error in errors:
        context.append_log(
            "发现失败 {}：{}".format(error["path"], error["message"]),
            "warning",
        )
    tree = _discovery_result_tree(target, files, errors)
    if not files and not errors:
        errors = [{"path": main_conf_path, "message": "未发现配置文件"}]
        tree = _discovery_result_tree(target, files, errors)
    detail = "发现 {} 个配置文件，{} 项错误".format(len(files), len(errors))
    status = "failed" if errors else "success"
    return TaskOutcome(status, detail, tree)


def create_discovery_task(
    session_factory: sessionmaker,
    executor,
    encryption_key: bytes,
    *,
    node_id: int,
    hostname: str,
    ip: str,
    main_conf_path: str,
    trigger_user_id: int,
    trigger_ip: str = "",
) -> int:
    """创建只读配置发现任务并排入统一执行器。"""
    def run(context: TaskContext) -> TaskOutcome:
        """在任务线程中连接节点并发现配置路径。"""
        return _run_discovery(
            context,
            session_factory,
            node_id,
            encryption_key,
            main_conf_path,
        )

    return create_task(
        session_factory,
        executor,
        run,
        operation_type="config_discover",
        detail="正在发现节点 {} 的 Nginx 配置".format(hostname),
        target_hostnames=(hostname,),
        target_ips=(ip,),
        subject_type="node",
        subject_id=node_id,
        trigger_user_id=trigger_user_id,
        trigger_ip=trigger_ip,
    )


def _sync_discovered_file(
    session_factory: sessionmaker,
    node_id: int,
    user_id: int,
    task_id: int,
    item: dict,
) -> str:
    """将一个已发现远程文件写入配置绑定和不可变版本快照。"""
    now = datetime.utcnow()
    with session_scope(session_factory) as session:
        with session.begin():
            config = session.scalar(
                select(Config)
                .where(Config.name == item["name"])
                .order_by(Config.id.asc())
                .limit(1)
            )
            if config is None:
                config = Config(
                    name=item["name"],
                    default_remote_path=item["path"],
                    source="discovered",
                    created_by=user_id,
                )
                session.add(config)
                session.flush()
            binding = session.scalar(
                select(ConfigBinding).where(
                    ConfigBinding.config_id == config.id,
                    ConfigBinding.node_id == node_id,
                )
            )
            if binding is not None and binding.sync_status == "marked_deleted":
                return "skipped"
            if binding is None:
                binding = ConfigBinding(
                    config_id=config.id,
                    node_id=node_id,
                    remote_path=item["path"],
                    content=item["content"],
                    current_version=1,
                    sync_status="synced",
                    synced_version=1,
                    last_sync_time=now,
                    last_sync_task_id=task_id,
                    source="discovered",
                    created_by=user_id,
                )
                session.add(binding)
                session.flush()
                session.add(
                    BindingVersion(
                        binding_id=binding.id,
                        version=1,
                        content=item["content"],
                        remark="发现导入",
                        created_by=user_id,
                    )
                )
                if item["content"] and not config.template_content:
                    config.template_content = item["content"]
                return "created"

            changed = binding.content != item["content"]
            if changed:
                binding.current_version += 1
                binding.content = item["content"]
                session.add(
                    BindingVersion(
                        binding_id=binding.id,
                        version=binding.current_version,
                        content=item["content"],
                        remark="远程同步更新",
                        created_by=user_id,
                    )
                )
            binding.remote_path = item["path"]
            binding.sync_status = "synced"
            binding.synced_version = binding.current_version
            binding.last_sync_time = now
            binding.last_sync_error = ""
            binding.last_sync_task_id = task_id
            binding.updated_at = now
            return "updated" if changed else "skipped"


def _mark_failed_paths(
    session_factory: sessionmaker,
    node_id: int,
    task_id: int,
    errors: Sequence[dict],
) -> None:
    """将已知绑定对应的远程读取失败状态写回数据库。"""
    failed_paths = {item["path"] for item in errors}
    if not failed_paths:
        return
    now = datetime.utcnow()
    with session_scope(session_factory) as session:
        with session.begin():
            bindings = session.scalars(
                select(ConfigBinding).where(
                    ConfigBinding.node_id == node_id,
                    ConfigBinding.remote_path.in_(failed_paths),
                    ConfigBinding.sync_status.notin_(
                        ("marked_deleted", "orphaned")
                    ),
                )
            ).all()
            for binding in bindings:
                binding.sync_status = "failed"
                binding.last_sync_error = "远程配置读取失败"
                binding.last_sync_time = now
                binding.last_sync_task_id = task_id
                binding.updated_at = now


def _mark_missing_bindings(
    session_factory: sessionmaker,
    node_id: int,
    discovered_paths: Set[str],
) -> List[dict]:
    """全量发现成功后将不再存在的已同步绑定标为远程已删除。"""
    now = datetime.utcnow()
    missing = []
    with session_scope(session_factory) as session:
        with session.begin():
            bindings = session.scalars(
                select(ConfigBinding)
                .options(joinedload(ConfigBinding.config))
                .where(
                    ConfigBinding.node_id == node_id,
                    ConfigBinding.sync_status == "synced",
                )
            ).all()
            for binding in bindings:
                if binding.remote_path in discovered_paths:
                    continue
                binding.sync_status = "orphaned"
                binding.updated_at = now
                missing.append(
                    {"name": binding.config.name, "path": binding.remote_path}
                )
    return missing


def _cleanup_marked_bindings(
    session_factory: sessionmaker,
    target: dict,
    context: TaskContext,
    task_id: int,
    client: Optional[paramiko.SSHClient] = None,
) -> Tuple[List[dict], List[dict]]:
    """安全删除远程已标记文件并仅在远程成功后删除本地绑定。"""
    with session_scope(session_factory) as session:
        bindings = session.scalars(
            select(ConfigBinding)
            .options(joinedload(ConfigBinding.config))
            .where(
                ConfigBinding.node_id == target["id"],
                ConfigBinding.sync_status == "marked_deleted",
            )
        ).all()
        pending = [
            {"id": item.id, "name": item.config.name, "path": item.remote_path}
            for item in bindings
        ]
    if not pending:
        return [], []

    owns_client = client is None
    if client is None:
        client, error = _connect_ssh(
            target["ip"],
            target["port"],
            target["username"],
            target["auth_type"],
            target["password"],
            target["private_key"],
            context,
            target.get("ssh_timeout", 10),
            target.get("detect_retries", 1),
        )
        if client is None:
            errors = [
                {"path": item["path"], "message": error}
                for item in pending
            ]
            _record_cleanup_errors(session_factory, target["id"], task_id, errors)
            return [], errors

    deleted = []
    errors = []
    try:
        for item in pending:
            context.check_cancelled()
            command = "rm -f -- {}".format(shlex.quote(item["path"]))
            try:
                _stdin, stdout, _stderr = client.exec_command(command, timeout=20)
                exit_status = stdout.channel.recv_exit_status()
            except Exception:
                exit_status = -1
            if exit_status != 0:
                errors.append(
                    {"path": item["path"], "message": "远程删除失败，绑定仍保留"}
                )
                continue
            with session_scope(session_factory) as session:
                with session.begin():
                    binding = session.scalar(
                        select(ConfigBinding).where(
                            ConfigBinding.id == item["id"],
                            ConfigBinding.node_id == target["id"],
                            ConfigBinding.sync_status == "marked_deleted",
                        )
                    )
                    if binding is not None:
                        session.delete(binding)
                        deleted.append(
                            {"name": item["name"], "path": item["path"]}
                        )
    finally:
        if owns_client:
            client.close()
    _record_cleanup_errors(session_factory, target["id"], task_id, errors)
    return deleted, errors


def _record_cleanup_errors(
    session_factory: sessionmaker,
    node_id: int,
    task_id: int,
    errors: Sequence[dict],
) -> None:
    """为远程删除失败的标记绑定保留最近任务和错误摘要。"""
    messages = {item["path"]: item["message"] for item in errors}
    if not messages:
        return
    now = datetime.utcnow()
    with session_scope(session_factory) as session:
        with session.begin():
            bindings = session.scalars(
                select(ConfigBinding).where(
                    ConfigBinding.node_id == node_id,
                    ConfigBinding.remote_path.in_(set(messages)),
                    ConfigBinding.sync_status == "marked_deleted",
                )
            ).all()
            for binding in bindings:
                binding.last_sync_error = messages[binding.remote_path]
                binding.last_sync_time = now
                binding.last_sync_task_id = task_id
                binding.updated_at = now


def _sync_node(
    context: TaskContext,
    session_factory: sessionmaker,
    encryption_key: bytes,
    node_id: int,
    user_id: int,
    task_id: int,
    mode: str,
    selected_paths: Sequence[str],
    main_conf_path: Optional[str],
    ssh_client: Optional[paramiko.SSHClient] = None,
) -> dict:
    """发现并同步一个节点，按模式应用远程缺失和部分选择规则。"""
    target, target_error = _load_target(
        session_factory,
        node_id,
        encryption_key,
        path_override=main_conf_path,
    )
    result = {
        "node_id": node_id,
        "hostname": "",
        "ip": "",
        "mode": mode,
        "created": [],
        "updated": [],
        "skipped": [],
        "orphaned": [],
        "deleted": [],
        "errors": [],
    }
    if target is None:
        result["errors"].append({"path": "", "message": target_error})
        return result
    result["hostname"] = target["hostname"]
    result["ip"] = target["ip"]
    owns_client = ssh_client is None
    connection_error = ""
    if ssh_client is None:
        ssh_client, connection_error = _connect_ssh(
            target["ip"],
            target["port"],
            target["username"],
            target["auth_type"],
            target["password"],
            target["private_key"],
            context,
            target.get("ssh_timeout", 10),
            target.get("detect_retries", 1),
        )
    if ssh_client is None:
        errors = [{"path": target["main_conf_path"], "message": connection_error}]
        return _apply_sync_results(
            context,
            session_factory,
            user_id,
            task_id,
            mode,
            selected_paths,
            main_conf_path,
            target,
            result,
            [],
            errors,
            None,
        )
    try:
        files, errors = discover_remote_configs(
            target,
            target["main_conf_path"],
            context,
            max_depth=target["max_discover_depth"],
            client=ssh_client,
        )
        return _apply_sync_results(
            context,
            session_factory,
            user_id,
            task_id,
            mode,
            selected_paths,
            main_conf_path,
            target,
            result,
            files,
            errors,
            ssh_client,
        )
    finally:
        if owns_client:
            ssh_client.close()


def _apply_sync_results(
    context: TaskContext,
    session_factory: sessionmaker,
    user_id: int,
    task_id: int,
    mode: str,
    selected_paths: Sequence[str],
    main_conf_path: Optional[str],
    target: dict,
    result: dict,
    files: List[dict],
    errors: List[dict],
    ssh_client: Optional[paramiko.SSHClient],
) -> dict:
    """將遠端發現結果寫入本地綁定，並按模式處理缺失路徑。"""
    node_id = target["id"]
    result["errors"].extend(errors)
    discovered_paths = {item["path"] for item in files}
    selected_set = set(selected_paths)
    if mode == "partial":
        missing_selected = sorted(selected_set - discovered_paths)
        result["errors"].extend(
            {
                "path": path,
                "message": "所选路径未在本次发现结果中找到",
            }
            for path in missing_selected
        )
        files = [item for item in files if item["path"] in selected_set]

    for index, item in enumerate(files):
        context.check_cancelled()
        status = _sync_discovered_file(
            session_factory,
            node_id,
            user_id,
            task_id,
            item,
        )
        result[status].append({"name": item["name"], "path": item["path"]})
        context.append_log(
            "{}配置 {}".format(
                {"created": "新增", "updated": "更新", "skipped": "跳过"}[status],
                item["path"],
            )
        )
        if main_conf_path is not None:
            progress = min(90, 45 + int((index + 1) * 45 / max(len(files), 1)))
            context.update_progress(
                progress,
                "{}：{}".format(target["hostname"], item["name"]),
            )

    _mark_failed_paths(session_factory, node_id, task_id, errors)
    if mode == "full" and not errors and ssh_client is not None:
        result["orphaned"] = _mark_missing_bindings(
            session_factory,
            node_id,
            discovered_paths,
        )
        deleted, delete_errors = _cleanup_marked_bindings(
            session_factory,
            target,
            context,
            task_id,
            ssh_client,
        )
        result["deleted"] = deleted
        result["errors"].extend(delete_errors)
    elif mode == "partial":
        # 与参考实现一致，部分同步也清理 marked_deleted；只在发现完整时执行。
        if not errors and ssh_client is not None:
            deleted, delete_errors = _cleanup_marked_bindings(
                session_factory,
                target,
                context,
                task_id,
                ssh_client,
            )
            result["deleted"] = deleted
            result["errors"].extend(delete_errors)
    for item in result["orphaned"]:
        context.append_log("远程配置已缺失 {}".format(item["path"]), "warning")
    for item in result["deleted"]:
        context.append_log("已清理远程配置 {}".format(item["path"]))
    for error in result["errors"]:
        context.append_log(
            "配置同步失败 {}：{}".format(error["path"], error["message"]),
            "error",
        )
    if not any(
        result[key]
        for key in ("created", "updated", "skipped", "orphaned", "deleted")
    ) and not result["errors"]:
        result["errors"].append(
            {"path": target["main_conf_path"], "message": "未发现可同步配置文件"}
        )
    return result


def _node_result_summary(result: dict) -> dict:
    """构造一个节点同步结果的成功、失败和处理总量。"""
    successful = sum(
        len(result[key])
        for key in ("created", "updated", "skipped", "orphaned", "deleted")
    )
    failed = len(result["errors"])
    return {
        "total": successful + failed,
        "success": successful,
        "failed": failed,
    }


def _compact_node_result(result: dict) -> dict:
    """限制任务结果树的逐项明细并保留所有类别的完整计数。"""
    keys = ("created", "updated", "orphaned", "deleted", "skipped")
    counts = {key: len(result[key]) for key in keys}
    remaining = MAX_RESULT_DETAIL_ITEMS
    compact = {
        key: []
        for key in keys
    }
    for key in keys:
        selected = result[key][:remaining]
        compact[key] = selected
        remaining -= len(selected)
        if remaining == 0:
            break
    errors = list(result["errors"][:MAX_RESULT_ERROR_ITEMS])
    compact.update(
        {
            "node_id": result["node_id"],
            "hostname": result["hostname"],
            "ip": result["ip"],
            "mode": result["mode"],
            "counts": counts,
            "summary": result.get("summary", _node_result_summary(result)),
            "errors": errors,
            "omitted_detail_count": max(
                sum(counts.values()) - sum(len(compact[key]) for key in keys),
                0,
            ),
            "omitted_error_count": max(len(result["errors"]) - len(errors), 0),
        }
    )
    return compact


def _run_single_sync(
    context: TaskContext,
    session_factory: sessionmaker,
    encryption_key: bytes,
    node_id: int,
    user_id: int,
    task_id: int,
    mode: str,
    selected_paths: Sequence[str],
    main_conf_path: str,
) -> TaskOutcome:
    """运行单节点全量或部分同步并持久化最终结果树。"""
    context.update_progress(5, "正在准备远程配置发现")
    result = _sync_node(
        context,
        session_factory,
        encryption_key,
        node_id,
        user_id,
        task_id,
        mode,
        selected_paths,
        main_conf_path,
    )
    summary = _node_result_summary(result)
    failed = summary["failed"] > 0
    if summary["total"] == 0:
        failed = True
        result["errors"].append(
            {"path": "", "message": "未发现可同步配置文件"}
        )
        summary = _node_result_summary(result)
    tree = {
        "summary": summary,
        "nodes": [_compact_node_result(result)],
    }
    detail = "同步完成：新增 {}，更新 {}，跳过 {}，远程删除 {}，清理 {}，错误 {}".format(
        len(result["created"]),
        len(result["updated"]),
        len(result["skipped"]),
        len(result["orphaned"]),
        len(result["deleted"]),
        len(result["errors"]),
    )
    return TaskOutcome("failed" if failed else "success", detail, tree)


def create_sync_task(
    session_factory: sessionmaker,
    executor,
    encryption_key: bytes,
    *,
    node_id: int,
    hostname: str,
    ip: str,
    main_conf_path: str,
    trigger_user_id: int,
    trigger_ip: str = "",
    mode: str = "full",
    selected_paths: Sequence[str] = (),
) -> int:
    """创建单节点配置同步任务并保存受限资源关联。"""
    def run(context: TaskContext) -> TaskOutcome:
        """在后台线程执行单节点配置同步。"""
        return _run_single_sync(
            context,
            session_factory,
            encryption_key,
            node_id,
            trigger_user_id,
            context.task_id,
            mode,
            tuple(selected_paths),
            main_conf_path,
        )

    task_id = create_task(
        session_factory,
        executor,
        run,
        operation_type="config_batch_sync",
        detail="正在同步节点 {} 的配置".format(hostname),
        target_hostnames=(hostname,),
        target_ips=(ip,),
        subject_type="node",
        subject_id=node_id,
        trigger_user_id=trigger_user_id,
        trigger_ip=trigger_ip,
    )
    return task_id


def create_batch_sync_task(
    session_factory: sessionmaker,
    executor,
    encryption_key: bytes,
    *,
    targets: Sequence[dict],
    trigger_user_id: int,
    trigger_ip: str = "",
    max_workers: int = MAX_SYNC_WORKERS,
) -> int:
    """创建并行度受限的多节点全量配置同步任务。"""
    target_ids = tuple(item["id"] for item in targets)
    hostnames = tuple(item["hostname"] for item in targets)
    ips = tuple(item["ip"] for item in targets)
    def run(context: TaskContext) -> TaskOutcome:
        """并行同步目标节点并持续写入批次结果树。"""
        results = []
        done = 0
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(target_ids)),
            thread_name_prefix="ngxops-config-sync",
        ) as pool:
            futures = {
                pool.submit(
                    _sync_node,
                    context,
                    session_factory,
                    encryption_key,
                    node_id,
                    trigger_user_id,
                    context.task_id,
                    "full",
                    (),
                    None,
                ): node_id
                for node_id in target_ids
            }
            for future in as_completed(futures):
                context.check_cancelled()
                try:
                    result = future.result()
                except Exception:
                    result = {
                        "node_id": futures[future],
                        "hostname": "",
                        "ip": "",
                        "mode": "full",
                        "created": [],
                        "updated": [],
                        "skipped": [],
                        "orphaned": [],
                        "deleted": [],
                        "errors": [{"path": "", "message": "节点同步执行失败"}],
                    }
                result["summary"] = _node_result_summary(result)
                compact_result = _compact_node_result(result)
                results.append(compact_result)
                done += 1
                success_count = sum(
                    1 for item in results if item["summary"]["failed"] == 0
                )
                current_tree = {
                    "summary": {
                        "total": len(target_ids),
                        "success": success_count,
                        "failed": len(results) - success_count,
                    },
                    "nodes": results,
                }
                context.set_result_tree(current_tree)
                context.update_progress(
                    min(95, int(done * 95 / max(len(target_ids), 1))),
                    "已完成 {}/{} 个节点".format(done, len(target_ids)),
                )
        success_count = sum(
            1 for item in results if item["summary"]["failed"] == 0
        )
        failed_count = len(results) - success_count
        status = "failed" if failed_count else "success"
        detail = "批量同步完成：成功 {} 个节点，失败 {} 个节点".format(
            success_count,
            failed_count,
        )
        return TaskOutcome(
            status,
            detail,
            {
                "summary": {
                    "total": len(target_ids),
                    "success": success_count,
                    "failed": failed_count,
                },
                "nodes": results,
            },
        )

    return create_task(
        session_factory,
        executor,
        run,
        operation_type="config_batch_sync",
        detail="正在批量同步 {} 个节点".format(len(target_ids)),
        target_hostnames=hostnames,
        target_ips=ips,
        target_configs=("远程配置发现",),
        trigger_user_id=trigger_user_id,
        trigger_ip=trigger_ip,
    )
