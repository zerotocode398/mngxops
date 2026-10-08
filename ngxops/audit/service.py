"""提供请求级审计上下文、ORM 变更捕获和安全日志写入。"""

from contextlib import contextmanager
import logging
from typing import Dict, Iterator, Optional

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from ngxops.accounts.models import User
from ngxops.audit.models import AuditLog


_TRACKED_TABLES = {
    "auth_user": "用户管理",
    "ngxops_credentials": "凭证管理",
    "ngxops_nodes": "节点管理",
    "ngxops_node_sync_settings": "节点管理",
    "ngxops_node_groups": "节点分组",
    "ngxops_configs": "配置管理",
    "ngxops_config_bindings": "配置绑定",
    "ngxops_binding_versions": "绑定版本",
    "ngxops_roles": "角色管理",
    "ngxops_user_teams": "用户组管理",
    "ngxops_user_profiles": "用户管理",
    "ngxops_nginx_source_packages": "Nginx 升级",
    "ngxops_nginx_module_packages": "Nginx 升级",
}

_NODE_PROFILE_FIELDS = {
    "hostname": "主机名",
    "ip": "IP 地址",
    "port": "SSH 端口",
    "credential_id": "SSH 凭证",
    "environment": "环境",
    "nginx_path": "Nginx 路径",
    "description": "备注",
}

_TASK_AUDIT_MAP = {
    "release_publish": ("发布管理", "发布配置"),
    "release_rollback": ("发布管理", "回滚配置"),
    "credential_enable_test": ("凭证管理", "凭证启用测试"),
    "node_ssh_test": ("节点管理", "节点 SSH 测试"),
    "node_batch_test": ("节点管理", "节点批量测试"),
    "node_system_info": ("节点管理", "节点系统信息采集"),
    "node_nginx_version": ("节点管理", "Nginx 版本检测"),
    "config_batch_sync": ("配置管理", "配置批量同步"),
    "config_discover": ("配置管理", "配置发现扫描"),
    "config_drift_check": ("配置管理", "配置漂移检测"),
    "config_glob_preview": ("配置管理", "配置 Glob 预览"),
    "nginx_upgrade": ("Nginx 升级", "Nginx 编译升级"),
    "nginx_rollback": ("Nginx 升级", "Nginx 升级回滚"),
    "nginx_service_control": ("Nginx 启停", "Nginx 服务启停"),
    "nginx_install": ("Nginx 安装", "Nginx 全新安装"),
    "nginx_uninstall": ("Nginx 卸载", "Nginx 卸载"),
    "other": ("任务中心", "其他任务"),
}
logger = logging.getLogger(__name__)


def request_client_ip(request) -> str:
    """提取并限制审计日志使用的请求来源地址。"""
    address = request.client.host if request.client is not None else ""
    return address[:50]


def prepare_audit_session(
    session: Session,
    actor_id: Optional[int] = None,
    username: Optional[str] = None,
    ip: Optional[str] = None,
) -> None:
    """为当前 Session 保存可供 ORM 变更捕获使用的身份信息。"""
    if actor_id is not None:
        session.info["audit_actor_id"] = actor_id
    if username is not None:
        session.info["audit_username"] = username[:150]
    if ip is not None:
        session.info["audit_ip"] = ip[:50]


def write_audit_log(
    session: Session,
    module: str,
    action: str,
    detail: str,
    *,
    result: str = "success",
    task_id: Optional[int] = None,
    source_batch: str = "",
) -> Optional[AuditLog]:
    """将不含凭证明文的操作摘要写入当前事务。"""
    actor_id = session.info.get("audit_actor_id")
    username = session.info.get("audit_username", "")
    if actor_id is None:
        return None
    if not username:
        actor = session.get(User, actor_id)
        username = actor.username if actor is not None else ""
    log = AuditLog(
        user_id=actor_id,
        username=username,
        module=module[:100],
        action=action[:255],
        ip=session.info.get("audit_ip", "")[:50],
        result=result if result in ("success", "failed") else "success",
        detail=detail[:4000],
        task_id=task_id,
        source_batch=source_batch[:64],
    )
    session.add(log)
    _queue_runtime_audit(
        session,
        {
            "username": username,
            "module": module[:100],
            "action": action[:255],
            "ip": session.info.get("audit_ip", "")[:50],
            "result": result if result in ("success", "failed") else "success",
            "detail": detail[:4000],
        },
    )
    return log


@contextmanager
def suppress_model_audit(session: Session) -> Iterator[None]:
    """暂时停用逐对象自动记录以便批量操作写入一条摘要。"""
    was_suppressed = session.info.get("audit_suppressed", False)
    session.info["audit_suppressed"] = True
    try:
        yield
    finally:
        session.info["audit_suppressed"] = was_suppressed


def _object_label(obj, table_name: str) -> str:
    """只从非敏感身份字段生成审计对象标签。"""
    if table_name == "ngxops_config_bindings":
        return "绑定 #{}（配置 #{} / 节点 #{}）".format(
            getattr(obj, "id", "?"),
            getattr(obj, "config_id", "?"),
            getattr(obj, "node_id", "?"),
        )
    if table_name == "ngxops_binding_versions":
        return "绑定 #{} · V{}".format(
            getattr(obj, "binding_id", "?"), getattr(obj, "version", "?")
        )
    if table_name == "ngxops_user_profiles":
        return "用户 #{}".format(getattr(obj, "user_id", "?"))
    if table_name == "ngxops_nodes":
        return "{} ({})".format(
            getattr(obj, "hostname", "节点"), getattr(obj, "ip", "")
        )[:180]
    for attribute in ("name", "hostname", "username"):
        value = getattr(obj, attribute, None)
        if value:
            return str(value).replace("\r", " ").replace("\n", " ")[:180]
    identity = inspect(obj).identity
    return "#{}".format(identity[0]) if identity else "新对象"


def _task_event(obj, actor_id: Optional[int], ip: str) -> Optional[Dict[str, object]]:
    """将统一任务创建转换成带任务和批次关联的审计条目。"""
    actor_id = actor_id or getattr(obj, "trigger_user_id", None)
    if actor_id is None:
        return None
    operation_type = getattr(obj, "operation_type", "other") or "other"
    module, action = _TASK_AUDIT_MAP.get(
        operation_type, ("任务中心", operation_type[:100])
    )
    hosts = [
        part.strip()
        for part in (getattr(obj, "target_hostnames", "") or "").split(",")
        if part.strip()
    ]
    ips = [
        part.strip()
        for part in (getattr(obj, "target_ips", "") or "").split(",")
        if part.strip()
    ]
    total = max(len(hosts), len(ips))
    targets = []
    for index in range(total):
        host = hosts[index] if index < len(hosts) else ""
        address = ips[index] if index < len(ips) else ""
        if host and address:
            targets.append("{} ({})".format(host, address))
        else:
            targets.append(host or address)
    target_summary = ""
    if targets:
        preview = targets[:5]
        remaining = total - len(preview)
        target_summary = "；目标 {} 台：{}".format(
            total,
            "、".join(preview)
            + ("；另有 {} 台".format(remaining) if remaining else ""),
        )
    task_detail = (getattr(obj, "detail", "") or "").strip()
    detail = "创建任务：{}{}".format(task_detail or action, target_summary)
    return {
        "user_id": actor_id,
        "username": "",
        "module": module,
        "action": action,
        "ip": ip,
        "result": "success",
        "detail": detail,
        "task_id": getattr(obj, "id", None),
        "source_batch": getattr(obj, "source_batch", "") or "",
    }


def _capture_before_flush(session: Session, flush_context, instances) -> None:
    """缓存本次 ORM 写入中可追溯的业务对象摘要。"""
    if session.info.get("audit_suppressed"):
        return
    actor_id = session.info.get("audit_actor_id")
    username = session.info.get("audit_username", "")
    ip = session.info.get("audit_ip", "")
    pending = []
    seen = set()

    for collection, action in (
        (session.new, "create"),
        (session.dirty, "update"),
        (session.deleted, "delete"),
    ):
        for obj in collection:
            if id(obj) in seen:
                continue
            seen.add(id(obj))
            object_action = action
            table_name = getattr(getattr(obj, "__table__", None), "name", "")
            if table_name == "ngxops_tasks":
                if object_action == "create":
                    task_entry = _task_event(obj, actor_id, ip)
                    if task_entry is not None:
                        pending.append(task_entry)
                continue
            module = _TRACKED_TABLES.get(table_name)
            if module is None or actor_id is None:
                continue
            state = inspect(obj)
            if object_action == "update" and not session.is_modified(
                obj, include_collections=True
            ):
                continue
            if table_name == "ngxops_nodes" and object_action == "update":
                changed_fields = [
                    label
                    for field, label in _NODE_PROFILE_FIELDS.items()
                    if state.attrs[field].history.has_changes()
                ]
                if state.attrs.groups.history.has_changes():
                    changed_fields.append("节点组")
                if changed_fields:
                    pending.append(
                        {
                            "user_id": actor_id,
                            "username": username,
                            "module": "节点管理",
                            "action": "节点调整",
                            "ip": ip,
                            "result": "success",
                            "detail": "调整节点资料：{}；变更字段：{}".format(
                                _object_label(obj, table_name), "、".join(changed_fields)
                            ),
                            "task_id": None,
                            "source_batch": "",
                        }
                    )
                    continue
            if table_name == "ngxops_node_sync_settings":
                if object_action == "update" and state.attrs.main_conf_path.history.has_changes():
                    pending.append(
                        {
                            "user_id": actor_id,
                            "username": username,
                            "module": "节点管理",
                            "action": "节点调整",
                            "ip": ip,
                            "result": "success",
                            "detail": "调整节点 #{}；变更字段：Nginx 主配置路径".format(
                                getattr(obj, "node_id", "?")
                            ),
                            "task_id": None,
                            "source_batch": "",
                        }
                    )
                continue
            if (
                table_name == "ngxops_nodes"
                and object_action == "update"
                and getattr(obj, "is_deleted", False)
                and state.attrs.is_deleted.history.has_changes()
            ):
                object_action = "delete"
            verbs = {"create": "创建", "update": "更新", "delete": "删除"}
            label = _object_label(obj, table_name)
            pending.append(
                {
                    "user_id": actor_id,
                    "username": username,
                    "module": module,
                    "action": verbs[object_action] + module,
                    "ip": ip,
                    "result": "success",
                    "detail": "{}「{}」".format(verbs[object_action], label),
                    "task_id": None,
                    "source_batch": "",
                }
            )
    if pending:
        session.info.setdefault("audit_pending_entries", []).extend(pending)


def _write_after_flush(session: Session, flush_context) -> None:
    """在业务对象获得主键后将本次审计摘要加入同一事务。"""
    pending = session.info.pop("audit_pending_entries", [])
    for values in pending:
        actor_id = values["user_id"]
        username = values["username"]
        if not username and actor_id is not None:
            actor = session.get(User, actor_id)
            username = actor.username if actor is not None else ""
        session.add(
            AuditLog(
                username=username,
                **{key: value for key, value in values.items() if key != "username"},
            )
        )
        _queue_runtime_audit(session, dict(values, username=username))


def _queue_runtime_audit(session: Session, values: Dict[str, object]) -> None:
    """暫存業務審計摘要，僅在資料庫提交成功後寫入運行日誌。"""
    session.info.setdefault("runtime_audit_entries", []).append(values)


def _log_committed_audit(session: Session) -> None:
    """將已提交的業務操作摘要寫入輪轉文件日誌。"""
    entries = session.info.pop("runtime_audit_entries", [])
    for entry in entries:
        logger.info(
            "business_action user=%s module=%s action=%s result=%s ip=%s detail=%s",
            entry.get("username", ""),
            entry.get("module", ""),
            entry.get("action", ""),
            entry.get("result", "success"),
            entry.get("ip", ""),
            entry.get("detail", ""),
        )


def _discard_rolled_back_audit(session: Session) -> None:
    """丢弃未提交事务对应的运行审计摘要。"""
    session.info.pop("runtime_audit_entries", None)


event.listen(Session, "before_flush", _capture_before_flush)
event.listen(Session, "after_flush_postexec", _write_after_flush)
event.listen(Session, "after_commit", _log_committed_audit)
event.listen(Session, "after_rollback", _discard_rolled_back_audit)
