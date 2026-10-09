"""提供发布中心的节点选择、版本预览和异步发布 API。"""

import hashlib
import json
import threading
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session, contains_eager, joinedload, selectinload

from ngxops.accounts.models import User
from ngxops.audit.service import request_client_ip
from ngxops.api.contracts import api_error_responses
from ngxops.configs.models import BindingVersion, Config, ConfigBinding
from ngxops.database.session import get_session
from ngxops.nodes.models import Node, NodeGroup
from ngxops.releases.permissions import require_release_access
from ngxops.releases.services import create_publish_task
from ngxops.security.dependencies import require_permission
from ngxops.settings.service import read_setting
from ngxops.tasks.models import Task


api_router = APIRouter(prefix="/api/releases", tags=["releases"])
MAX_BATCH_COUNT = 3
MAX_SELECTED_BINDINGS = 500
PAGE_SIZES = (10, 20, 50, 100)
_SUBMISSION_LOCK = threading.Lock()


class ReleaseGroupItem(BaseModel):
    """描述发布中心的节点组筛选项。"""

    id: int
    name: str


class ReleaseNodeItem(BaseModel):
    """描述发布中心节点列表的一行。"""

    id: int
    hostname: str
    ip: str
    port: int
    environment: str
    status: Literal["online", "offline", "unknown"]
    is_locked: bool
    nginx_available: Optional[bool]
    nginx_version: str
    has_credential: bool
    credential_enabled: bool
    group_names: List[str]
    total_bindings: int
    modified_bindings: int
    selectable_bindings: int
    can_publish: bool


class ReleaseStatusCounts(BaseModel):
    """描述当前活动节点上的绑定状态计数。"""

    total: int
    pending: int
    synced: int
    failed: int
    orphaned: int
    marked_deleted: int


class ReleaseNodeListResponse(BaseModel):
    """描述发布中心节点分页、分组和状态计数。"""

    success: bool = True
    items: List[ReleaseNodeItem]
    groups: List[ReleaseGroupItem]
    page: int
    page_size: int
    total: int
    total_pages: int
    status_counts: ReleaseStatusCounts
    max_node_count: int
    max_binding_count: int


class ReleaseVersionItem(BaseModel):
    """描述绑定可发布的一个版本。"""

    id: int
    version: int
    created_at: datetime


class ReleaseBindingItem(BaseModel):
    """描述发布中心节点展开区的一条绑定。"""

    id: int
    config_id: int
    config_name: str
    remote_path: str
    current_version: int
    sync_status: str
    synced_version: Optional[int]
    versions: List[ReleaseVersionItem]


class ReleaseBindingsResponse(BaseModel):
    """描述指定节点当前页发布绑定及分页信息。"""

    success: bool = True
    node_id: int
    can_publish: bool
    page: int
    page_size: int
    total: int
    total_pages: int
    selectable_total: int
    bindings: List[ReleaseBindingItem]


class ReleaseVersionContentResponse(BaseModel):
    """描述版本预览正文。"""

    success: bool = True
    version: int
    content: str


class PublishSelection(BaseModel):
    """描述一个绑定及其所选发布版本。"""

    binding_id: int = Field(gt=0)
    version: Optional[int] = Field(default=None, gt=0)


class PublishRequest(BaseModel):
    """描述一次发布批次中的绑定和版本选择。"""

    bindings: List[PublishSelection] = Field(
        default_factory=list,
        max_length=MAX_SELECTED_BINDINGS,
    )


class SkippedPublishItem(BaseModel):
    """描述因节点状态变化而跳过的发布绑定。"""

    binding_id: int
    node_hostname: str
    reason: str


class PublishCreatedResponse(BaseModel):
    """描述已排入执行器的发布批次。"""

    success: bool = True
    task_id: int
    task_url: str
    batch_number: str
    item_count: int
    node_count: int
    skipped: List[SkippedPublishItem]
    message: str


class ReleaseHistoryVersionItem(BaseModel):
    """描述历史配置项可预览或选择回滚的版本。"""

    id: int
    version: int
    remark: str = Field(max_length=200)
    created_at: datetime
    created_by: str


class ReleaseHistoryBindingItem(BaseModel):
    """描述发布历史树中的一个配置执行项。"""

    task_id: int
    binding_id: int
    config_id: int
    config_name: str
    node_id: int
    hostname: str
    ip: str
    remote_path: str
    action: str
    version: Optional[int]
    status: str
    message: str
    is_latest: bool
    can_rollback: bool
    can_batch_rollback: bool
    rollback_reason: str
    previous_version: Optional[int]


class ReleaseHistoryNodeItem(BaseModel):
    """描述历史批次树中的节点分组。"""

    id: int
    hostname: str
    ip: str
    is_deleted: bool
    status: str
    bindings: List[ReleaseHistoryBindingItem]


class ReleaseHistoryBatchItem(BaseModel):
    """描述按批次分组的发布或回滚历史。"""

    task_id: int
    batch_number: str
    operation_type: Literal["release_publish", "release_rollback"]
    status: str
    summary: str
    created_at: datetime
    operator: str
    detail: str
    nodes: List[ReleaseHistoryNodeItem]


class ReleaseHistoryResponse(BaseModel):
    """描述发布历史批次分页结果。"""

    success: bool = True
    items: List[ReleaseHistoryBatchItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class ReleaseHistoryVersionsResponse(BaseModel):
    """描述单个历史配置项可选择回滚的版本分页。"""

    success: bool = True
    versions: List[ReleaseHistoryVersionItem]
    page: int
    page_size: int
    total: int
    total_pages: int


class RollbackSelection(BaseModel):
    """描述从一条历史执行项创建回滚的目标版本。"""

    task_id: int = Field(gt=0)
    binding_id: int = Field(gt=0)
    version: Optional[int] = Field(default=None, gt=0)


class RollbackRequest(BaseModel):
    """描述单条或批量回滚请求。"""

    items: List[RollbackSelection] = Field(min_length=1, max_length=MAX_SELECTED_BINDINGS)


class RollbackCreatedResponse(BaseModel):
    """描述新建的异步回滚任务和被跳过的历史项。"""

    success: bool = True
    task_id: int
    task_url: str
    batch_number: str
    item_count: int
    node_count: int
    skipped: List[SkippedPublishItem]
    message: str


def _release_node_query(
    search: str,
    group_id: Optional[int],
    environment: str,
    node_status: str,
    sync_status: str,
    nginx_available: str,
):
    """构造发布中心节点搜索和组合筛选查询。"""
    query = select(Node).where(Node.is_deleted.is_(False))
    terms = [
        item.strip()
        for item in search.replace("，", ",").split(",")
        if item.strip()
    ]
    for term in terms:
        matching_binding = (
            select(ConfigBinding.id)
            .join(Config, Config.id == ConfigBinding.config_id)
            .where(
                ConfigBinding.node_id == Node.id,
                or_(
                    Config.name.contains(term, autoescape=True),
                    ConfigBinding.remote_path.contains(term, autoescape=True),
                ),
            )
            .exists()
        )
        query = query.where(
            or_(
                Node.hostname.contains(term, autoescape=True),
                Node.ip.contains(term, autoescape=True),
                Node.groups.any(NodeGroup.name.contains(term, autoescape=True)),
                matching_binding,
            )
        )
    if group_id:
        query = query.where(Node.groups.any(NodeGroup.id == group_id))
    if environment in ("dev", "test", "prod"):
        query = query.where(Node.environment == environment)
    if node_status in ("online", "offline", "unknown"):
        query = query.where(Node.status == node_status)
    if nginx_available == "true":
        query = query.where(Node.nginx_available.is_(True))
    elif nginx_available == "false":
        query = query.where(Node.nginx_available.is_(False))
    if sync_status:
        statuses = (
            ("not_synced", "modified")
            if sync_status == "pending"
            else (sync_status,)
        )
        has_status = select(ConfigBinding.id).where(
            ConfigBinding.node_id == Node.id,
            ConfigBinding.sync_status.in_(statuses),
        ).exists()
        query = query.where(has_status)
    return query


def _release_status_counts(session: Session) -> ReleaseStatusCounts:
    """汇总活动节点上的全部配置绑定状态。"""
    counts = {
        "total": 0,
        "pending": 0,
        "synced": 0,
        "failed": 0,
        "orphaned": 0,
        "marked_deleted": 0,
    }
    rows = session.execute(
        select(ConfigBinding.sync_status, func.count(ConfigBinding.id))
        .join(Node, Node.id == ConfigBinding.node_id)
        .where(Node.is_deleted.is_(False))
        .group_by(ConfigBinding.sync_status)
    ).all()
    for status, count in rows:
        counts["total"] += count
        if status in ("not_synced", "modified"):
            counts["pending"] += count
        elif status in counts:
            counts[status] += count
    return ReleaseStatusCounts(**counts)


def _node_binding_stats(
    session: Session,
    nodes: List[Node],
) -> Dict[int, Tuple[int, int, int]]:
    """一次查询聚合当前页节点的绑定、修改和可发布数量。"""
    node_ids = [node.id for node in nodes]
    if not node_ids:
        return {}
    has_versions = select(BindingVersion.id).where(
        BindingVersion.binding_id == ConfigBinding.id
    ).exists()
    rows = session.execute(
        select(
            ConfigBinding.node_id,
            func.count(ConfigBinding.id),
            func.sum(case((ConfigBinding.sync_status == "modified", 1), else_=0)),
            func.sum(
                case(
                    (
                        or_(
                            ConfigBinding.sync_status == "marked_deleted",
                            has_versions,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
        ).where(ConfigBinding.node_id.in_(node_ids)).group_by(ConfigBinding.node_id)
    ).all()
    return {
        node_id: (int(total or 0), int(modified or 0), int(selectable or 0))
        for node_id, total, modified, selectable in rows
    }


def _node_item(node: Node, binding_stats: Tuple[int, int, int]) -> ReleaseNodeItem:
    """构造节点发布能力、分组和绑定统计摘要。"""
    credential = node.credential
    can_publish_node = bool(
        not node.is_locked
        and node.status == "online"
        and node.nginx_available is True
        and credential is not None
        and credential.is_enabled
    )
    return ReleaseNodeItem(
        id=node.id,
        hostname=node.hostname,
        ip=node.ip,
        port=node.port,
        environment=node.environment,
        status=node.status,
        is_locked=node.is_locked,
        nginx_available=node.nginx_available,
        nginx_version=node.nginx_version,
        has_credential=credential is not None,
        credential_enabled=bool(credential and credential.is_enabled),
        group_names=[group.name for group in node.groups],
        total_bindings=binding_stats[0],
        modified_bindings=binding_stats[1],
        selectable_bindings=binding_stats[2],
        can_publish=can_publish_node,
    )


def _history_tree(task: Task) -> dict:
    """读取任务结果树并容忍损坏或尚未写入的历史数据。"""
    try:
        tree = json.loads(task.result_tree_json or "{}")
    except (TypeError, ValueError):
        return {}
    return tree if isinstance(tree, dict) else {}


def _history_groups(tasks: List[Task]) -> List[dict]:
    """将发布和回滚任务整理为批次到节点的历史树。"""
    groups: Dict[str, dict] = {}
    for task in tasks:
        batch_number = task.source_batch or "task-{}".format(task.id)
        group = groups.setdefault(
            batch_number,
            {"batch_number": batch_number, "tasks": [], "nodes": {}},
        )
        group["tasks"].append(task)
        tree = _history_tree(task)
        for node_data in tree.get("nodes", []):
            if not isinstance(node_data, dict):
                continue
            try:
                node_id = int(node_data.get("node_id"))
            except (TypeError, ValueError):
                continue
            node_group = group["nodes"].setdefault(
                node_id,
                {
                    "id": node_id,
                    "hostname": str(node_data.get("hostname") or ""),
                    "ip": str(node_data.get("ip") or ""),
                    "status": str(node_data.get("status") or "unknown"),
                    "bindings": [],
                },
            )
            for entry in node_data.get("bindings", []):
                if isinstance(entry, dict):
                    node_group["bindings"].append(
                        {
                            "task": task,
                            "node_id": node_id,
                            "hostname": node_group["hostname"],
                            "ip": node_group["ip"],
                            "entry": entry,
                        }
                    )
    latest_by_binding: Dict[int, int] = {}
    for group in groups.values():
        for node in group["nodes"].values():
            for row in node["bindings"]:
                try:
                    binding_id = int(row["entry"].get("binding_id"))
                except (TypeError, ValueError):
                    continue
                latest_by_binding.setdefault(binding_id, row["task"].id)
    for group in groups.values():
        for node in group["nodes"].values():
            for row in node["bindings"]:
                try:
                    binding_id = int(row["entry"].get("binding_id"))
                except (TypeError, ValueError):
                    continue
                row["is_latest"] = latest_by_binding.get(binding_id) == row["task"].id
    return sorted(
        groups.values(),
        key=lambda group: max(task.created_at for task in group["tasks"]),
        reverse=True,
    )


def _history_status(group: dict) -> str:
    """汇总批次中各配置项和任务状态。"""
    task_statuses = [task.status for task in group["tasks"]]
    if "running" in task_statuses:
        return "running"
    if "pending" in task_statuses:
        return "pending"
    if task_statuses and all(status == "cancelled" for status in task_statuses):
        return "cancelled"
    statuses = [
        row["entry"].get("status")
        for node in group["nodes"].values()
        for row in node["bindings"]
    ]
    if not statuses:
        return task_statuses[0] if task_statuses else "unknown"
    if any(status in ("running", "pending", "waiting_reload") for status in statuses):
        return "running"
    if all(status == "success" for status in statuses):
        return "success"
    if all(status == "failed" for status in statuses):
        return "failed"
    if all(status in ("success", "failed") for status in statuses):
        return "partial"
    return task_statuses[0] if task_statuses else "unknown"


def _history_group_matches(
    group: dict,
    search: str,
    batch: str,
    node_ip: str,
    status: str,
) -> bool:
    """按批次号、节点 IP、关键字和汇总状态筛选历史批次。"""
    batch_number = group["batch_number"]
    if batch and batch.lower() not in batch_number.lower():
        return False
    if node_ip and not any(
        node_ip.lower() in node["ip"].lower()
        for node in group["nodes"].values()
    ):
        return False
    if status and _history_status(group) != status:
        return False
    parts = [batch_number]
    for task in group["tasks"]:
        parts.extend((task.detail, task.target_hostnames, task.target_ips, task.target_configs))
    for node in group["nodes"].values():
        parts.extend((node["hostname"], node["ip"]))
        for row in node["bindings"]:
            entry = row["entry"]
            parts.extend(
                (
                    str(entry.get("config_name") or ""),
                    str(entry.get("remote_path") or ""),
                )
            )
    haystack = " ".join(parts).lower()
    terms = [
        term.strip().lower()
        for term in search.replace("，", ",").split(",")
        if term.strip()
    ]
    return all(term in haystack for term in terms)


def _history_rollback_reason(
    task: Task,
    entry: dict,
    node_id: int,
    binding: Optional[ConfigBinding],
    node: Optional[Node],
    remote_path: str,
    remote_path_hash: str = "",
) -> str:
    """按历史执行状态和当前资产状态判断是否允许回滚。"""
    if task.status not in ("success", "failed"):
        return "任务尚未完成或已取消"
    if entry.get("status") not in ("success", "failed"):
        return "配置项尚未完成"
    if entry.get("action") != "publish":
        return "远程删除操作不可回滚"
    if binding is None or binding.node_id != node_id:
        return "配置绑定已删除"
    if node is None or node.is_deleted:
        return "节点已删除"
    if node.is_locked:
        return "节点已锁定"
    if node.status != "online":
        return "节点 SSH 非在线状态"
    if node.nginx_available is not True:
        return "节点 Nginx 未确认可用"
    if node.credential is None or not node.credential.is_enabled:
        return "节点未关联启用的 SSH 凭证"
    if binding.sync_status == "marked_deleted":
        return "配置绑定已标记删除"
    current_path_hash = hashlib.sha256(binding.remote_path.encode("utf-8")).hexdigest()
    if remote_path_hash:
        if current_path_hash != remote_path_hash:
            return "绑定远程路径已变更"
    elif binding.remote_path != remote_path:
        if len(remote_path) >= 160:
            return "历史路径快照不完整，无法确认绑定路径未变"
        return "绑定远程路径已变更"
    return ""


def _history_binding_item(
    row: dict,
    binding: Optional[ConfigBinding],
    node: Optional[Node],
) -> ReleaseHistoryBindingItem:
    """构造历史配置项和默认上一版回滚状态。"""
    task = row["task"]
    entry = row["entry"]
    node_id = row["node_id"]
    try:
        published_version = int(entry.get("version"))
    except (TypeError, ValueError):
        published_version = None
    binding_id = int(entry.get("binding_id") or 0)
    previous_version = (
        published_version - 1
        if published_version is not None and published_version > 1
        else None
    )
    gate_reason = _history_rollback_reason(
        task,
        entry,
        node_id,
        binding,
        node,
        str(entry.get("remote_path") or ""),
        str(entry.get("remote_path_hash") or ""),
    )
    has_alternative = bool(
        binding is not None
        and published_version is not None
        and (
            published_version > 1
            or binding.current_version > published_version
        )
    )
    reason = gate_reason or ("没有其他可用配置版本" if not has_alternative else "")
    can_rollback = not reason
    history_path = str(entry.get("remote_path") or "")
    if (
        binding is not None
        and entry.get("remote_path_hash")
        == hashlib.sha256(binding.remote_path.encode("utf-8")).hexdigest()
    ):
        history_path = binding.remote_path
    return ReleaseHistoryBindingItem(
        task_id=task.id,
        binding_id=binding_id,
        config_id=int(entry.get("config_id") or 0),
        config_name=str(entry.get("config_name") or ""),
        node_id=node_id,
        hostname=str(row["hostname"] or ""),
        ip=str(row["ip"] or ""),
        remote_path=history_path,
        action=str(entry.get("action") or "publish"),
        version=published_version,
        status=str(entry.get("status") or "unknown"),
        message=str(entry.get("message") or ""),
        is_latest=bool(row.get("is_latest")),
        can_rollback=can_rollback,
        can_batch_rollback=bool(
            can_rollback and previous_version is not None and row.get("is_latest")
        ),
        rollback_reason=reason,
        previous_version=previous_version,
    )


def _history_summary(group: dict) -> str:
    """生成批次成功、失败和总配置数摘要。"""
    statuses = [
        row["entry"].get("status")
        for node in group["nodes"].values()
        for row in node["bindings"]
    ]
    succeeded = sum(1 for status in statuses if status == "success")
    failed = sum(1 for status in statuses if status == "failed")
    return "成功 {}，失败 {}，共 {}".format(succeeded, failed, len(statuses))


def _history_batch_response(group: dict, session: Session) -> ReleaseHistoryBatchItem:
    """批量读取当前页资产和版本后构造一个历史批次响应。"""
    nodes_data = group["nodes"]
    node_ids = set(nodes_data)
    rows = [row for node in nodes_data.values() for row in node["bindings"]]
    binding_ids = {
        int(row["entry"].get("binding_id") or 0)
        for row in rows
        if row["entry"].get("binding_id")
    }
    user_ids = {task.trigger_user_id for task in group["tasks"] if task.trigger_user_id}
    nodes = session.scalars(
        select(Node)
        .options(joinedload(Node.credential))
        .where(Node.id.in_(node_ids))
    ).unique().all() if node_ids else []
    bindings = session.scalars(
        select(ConfigBinding)
        .options(
            joinedload(ConfigBinding.node).joinedload(Node.credential),
        )
        .where(ConfigBinding.id.in_(binding_ids))
    ).unique().all() if binding_ids else []
    users = session.scalars(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
    node_by_id = {node.id: node for node in nodes}
    binding_by_id = {binding.id: binding for binding in bindings}
    user_by_id = {user.id: user for user in users}
    task = group["tasks"][0]
    response_nodes = []
    for node_id, node_data in sorted(
        nodes_data.items(), key=lambda pair: pair[1]["hostname"].lower()
    ):
        live_node = node_by_id.get(node_id)
        response_nodes.append(
            ReleaseHistoryNodeItem(
                id=node_id,
                hostname=node_data["hostname"],
                ip=node_data["ip"],
                is_deleted=live_node is None or live_node.is_deleted,
                status=node_data["status"],
                bindings=[
                    _history_binding_item(
                        row,
                        binding_by_id.get(int(row["entry"].get("binding_id") or 0)),
                        live_node,
                    )
                    for row in node_data["bindings"]
                ],
            )
        )
    return ReleaseHistoryBatchItem(
        task_id=task.id,
        batch_number=group["batch_number"],
        operation_type=task.operation_type,
        status=_history_status(group),
        summary=_history_summary(group),
        created_at=task.created_at,
        operator=(user_by_id[task.trigger_user_id].username
                 if task.trigger_user_id in user_by_id else "-"),
        detail=task.detail,
        nodes=response_nodes,
    )


def _find_history_binding(task: Task, binding_id: int) -> Optional[dict]:
    """从已持久化结果树中查找指定绑定的历史节点快照。"""
    for node_data in _history_tree(task).get("nodes", []):
        if not isinstance(node_data, dict):
            continue
        try:
            node_id = int(node_data.get("node_id"))
        except (TypeError, ValueError):
            continue
        for entry in node_data.get("bindings", []):
            if not isinstance(entry, dict):
                continue
            try:
                entry_binding_id = int(entry.get("binding_id"))
            except (TypeError, ValueError):
                continue
            if entry_binding_id == binding_id:
                return {
                    "node_id": node_id,
                    "hostname": str(node_data.get("hostname") or ""),
                    "ip": str(node_data.get("ip") or ""),
                    "entry": entry,
                }
    return None


def _latest_history_bindings(session: Session, binding_ids: set) -> Dict[int, dict]:
    """查找各绑定最近一次发布或回滚任务中的结果项。"""
    if not binding_ids:
        return {}
    tasks = session.scalars(
        select(Task)
        .where(Task.operation_type.in_(("release_publish", "release_rollback")))
        .order_by(Task.created_at.desc(), Task.id.desc())
    ).all()
    latest = {}
    for task in tasks:
        for node_data in _history_tree(task).get("nodes", []):
            if not isinstance(node_data, dict):
                continue
            try:
                node_id = int(node_data.get("node_id"))
            except (TypeError, ValueError):
                continue
            for entry in node_data.get("bindings", []):
                if not isinstance(entry, dict):
                    continue
                try:
                    binding_id = int(entry.get("binding_id"))
                except (TypeError, ValueError):
                    continue
                if binding_id in binding_ids and binding_id not in latest:
                    latest[binding_id] = {
                        "task": task,
                        "history": {
                            "node_id": node_id,
                            "hostname": str(node_data.get("hostname") or ""),
                            "ip": str(node_data.get("ip") or ""),
                            "entry": entry,
                        },
                    }
    return latest


def _next_release_batch_number(session: Session) -> str:
    """生成当前日期下未使用的发布或回滚批次号。"""
    prefix = "release-{}-".format(datetime.now().strftime("%y%m%d"))
    latest_batch = session.scalar(
        select(Task.source_batch)
        .where(Task.source_batch.like(prefix + "%"))
        .order_by(Task.source_batch.desc())
        .limit(1)
    )
    try:
        sequence = int(latest_batch.rsplit("-", 1)[1]) + 1 if latest_batch else 1
    except (IndexError, ValueError):
        sequence = 1
    if sequence > 9999:
        raise HTTPException(status_code=409, detail="今日发布批次号已用尽")
    return "{}{:04d}".format(prefix, sequence)


@api_router.get(
    "/history",
    response_model=ReleaseHistoryResponse,
    summary="分页查询发布历史",
    description="需要 releases.read 权限；按发布或回滚批次返回节点和配置结果树。",
    responses=api_error_responses((401, 403, 422, 500)),
)
def list_release_history(
    search: str = Query("", max_length=200),
    batch: str = Query("", max_length=64),
    node_ip: str = Query("", max_length=100),
    status: Optional[
        Literal["pending", "running", "success", "partial", "failed", "cancelled"]
    ] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    _user: User = Depends(require_permission("releases", "read")),
    session: Session = Depends(get_session),
) -> ReleaseHistoryResponse:
    """按批次筛选发布与回滚历史并加载当前页结果树。"""
    tasks = session.scalars(
        select(Task)
        .where(Task.operation_type.in_(("release_publish", "release_rollback")))
        .order_by(Task.created_at.desc(), Task.id.desc())
    ).all()
    groups = [
        group
        for group in _history_groups(tasks)
        if _history_group_matches(group, search, batch, node_ip, status or "")
    ]
    total = len(groups)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    page_groups = groups[(page - 1) * page_size : page * page_size]
    return ReleaseHistoryResponse(
        items=[_history_batch_response(group, session) for group in page_groups],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@api_router.get(
    "/history/{task_id}/bindings/{binding_id}/versions",
    response_model=ReleaseHistoryVersionsResponse,
    summary="分页查询历史配置的可回滚版本",
    description="需要 releases.read 权限；正文由单独的预览接口读取。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def list_history_binding_versions(
    task_id: int = Path(..., ge=1),
    binding_id: int = Path(..., ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(15, ge=1, le=100),
    _user: User = Depends(require_permission("releases", "read")),
    session: Session = Depends(get_session),
) -> ReleaseHistoryVersionsResponse:
    """分页返回指定历史配置项除本次版本外的版本元数据。"""
    task = session.get(Task, task_id)
    if task is None or task.operation_type not in ("release_publish", "release_rollback"):
        raise HTTPException(status_code=404, detail="发布历史不存在")
    history_row = _find_history_binding(task, binding_id)
    binding = session.get(ConfigBinding, binding_id)
    if (
        history_row is None
        or binding is None
        or binding.node_id != history_row["node_id"]
    ):
        raise HTTPException(status_code=404, detail="历史配置绑定不存在")
    try:
        published_version = int(history_row["entry"].get("version"))
    except (TypeError, ValueError):
        published_version = None
    version_query = select(BindingVersion).where(
        BindingVersion.binding_id == binding_id
    )
    if published_version is not None:
        version_query = version_query.where(BindingVersion.version != published_version)
    total = int(
        session.scalar(
            select(func.count()).select_from(version_query.subquery())
        )
        or 0
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    versions = session.scalars(
        version_query.options(joinedload(BindingVersion.creator))
        .order_by(BindingVersion.version.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ReleaseHistoryVersionsResponse(
        versions=[
            ReleaseHistoryVersionItem(
                id=version.id,
                version=version.version,
                remark=version.remark[:200],
                created_at=version.created_at,
                created_by=(version.creator.username if version.creator else ""),
            )
            for version in versions
        ],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@api_router.get(
    "/history/{task_id}/bindings/{binding_id}/versions/{version}",
    response_model=ReleaseVersionContentResponse,
    summary="预览发布历史关联版本",
    description="需要 releases.read 或 releases.publish 权限；仅返回指定版本正文。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def get_history_version_content(
    task_id: int = Path(..., ge=1),
    binding_id: int = Path(..., ge=1),
    version: int = Path(..., ge=1),
    user: User = Depends(require_release_access),
    session: Session = Depends(get_session),
) -> ReleaseVersionContentResponse:
    """读取指定历史任务关联绑定的配置版本正文。"""
    task = session.get(Task, task_id)
    if task is None or task.operation_type not in ("release_publish", "release_rollback"):
        raise HTTPException(status_code=404, detail="发布历史不存在")
    history_row = _find_history_binding(task, binding_id)
    binding = session.get(ConfigBinding, binding_id)
    if (
        history_row is None
        or binding is None
        or binding.node_id != history_row["node_id"]
    ):
        raise HTTPException(status_code=404, detail="历史配置绑定不存在")
    version_row = session.scalar(
        select(BindingVersion).where(
            BindingVersion.binding_id == binding_id,
            BindingVersion.version == version,
        )
    )
    if version_row is None:
        raise HTTPException(status_code=404, detail="配置版本不存在")
    return ReleaseVersionContentResponse(
        version=version_row.version,
        content=version_row.content,
    )


@api_router.post(
    "/rollback",
    response_model=RollbackCreatedResponse,
    status_code=202,
    summary="从发布历史创建异步回滚",
    description=(
        "需要 releases.publish 权限。批量请求默认回滚到各配置所发布版本的上一版；"
        "单条请求可指定其他版本。任务执行前重新检查节点、绑定、凭证和 Nginx 状态。"
    ),
    responses=api_error_responses((400, 401, 403, 404, 409, 422, 500, 503)),
)
def rollback_release_items(
    payload: RollbackRequest,
    request: Request,
    user: User = Depends(require_permission("releases", "publish")),
    session: Session = Depends(get_session),
) -> RollbackCreatedResponse:
    """验证历史来源和节点门禁后创建单条或批量回滚任务。"""
    task_ids = {item.task_id for item in payload.items}
    history_tasks = session.scalars(
        select(Task).where(
            Task.id.in_(task_ids),
            Task.operation_type.in_(("release_publish", "release_rollback")),
        )
    ).all()
    task_by_id = {task.id: task for task in history_tasks}
    if len(task_by_id) != len(task_ids):
        raise HTTPException(status_code=404, detail="部分发布历史不存在")

    latest_by_binding: Dict[int, dict] = {}
    for choice in payload.items:
        task = task_by_id[choice.task_id]
        history_row = _find_history_binding(task, choice.binding_id)
        if history_row is None:
            raise HTTPException(status_code=400, detail="历史任务中不存在所选配置")
        candidate = {"task": task, "history": history_row, "choice": choice}
        current = latest_by_binding.get(choice.binding_id)
        if current is None or (task.created_at, task.id) > (
            current["task"].created_at,
            current["task"].id,
        ):
            latest_by_binding[choice.binding_id] = candidate
    if len(latest_by_binding) > 1 and any(
        row["choice"].version is not None for row in latest_by_binding.values()
    ):
        raise HTTPException(
            status_code=400,
            detail="批量回滚使用各历史项的上一版；指定版本仅支持单条回滚",
        )

    binding_ids = set(latest_by_binding)
    latest_history = _latest_history_bindings(session, binding_ids)
    bindings = session.scalars(
        select(ConfigBinding)
        .options(
            joinedload(ConfigBinding.config),
            joinedload(ConfigBinding.node).joinedload(Node.credential),
            selectinload(ConfigBinding.versions),
        )
        .where(ConfigBinding.id.in_(binding_ids))
    ).unique().all()
    binding_by_id = {binding.id: binding for binding in bindings}
    selected = []
    skipped = []
    for binding_id, candidate in latest_by_binding.items():
        task = candidate["task"]
        history_row = candidate["history"]
        entry = history_row["entry"]
        binding = binding_by_id.get(binding_id)
        node = binding.node if binding is not None else None
        reason = _history_rollback_reason(
            task,
            entry,
            history_row["node_id"],
            binding,
            node,
            str(entry.get("remote_path") or ""),
            str(entry.get("remote_path_hash") or ""),
        )
        newest = latest_history.get(binding_id)
        if (
            not reason
            and candidate["choice"].version is None
            and newest is not None
            and newest["task"].id != task.id
        ):
            reason = "该绑定已有更新的发布历史"
        if not reason:
            try:
                published_version = int(entry.get("version"))
            except (TypeError, ValueError):
                published_version = None
            target_version = None
            if candidate["choice"].version is not None:
                target_version = next(
                    (
                        version
                        for version in binding.versions
                        if version.version == candidate["choice"].version
                        and version.version != published_version
                    ),
                    None,
                )
                if target_version is None:
                    reason = "所选回滚版本不存在或与已发布版本相同"
            elif published_version is not None:
                target_version = max(
                    (
                        version
                        for version in binding.versions
                        if version.version < published_version
                    ),
                    key=lambda version: version.version,
                    default=None,
                )
                if target_version is None:
                    reason = "该配置没有可用的上一版"
        if reason:
            skipped.append(
                SkippedPublishItem(
                    binding_id=binding_id,
                    node_hostname=(
                        node.hostname if node is not None else history_row["hostname"]
                    ),
                    reason=reason,
                )
            )
            continue
        selected.append(
            {
                "binding_id": binding.id,
                "config_id": binding.config_id,
                "config_name": binding.config.name,
                "node_id": node.id,
                "hostname": node.hostname,
                "ip": node.ip,
                "port": node.port,
                "nginx_path": node.nginx_path,
                "version": target_version.version,
                "remote_path": binding.remote_path,
                "content": target_version.content,
                "action": "publish",
            }
        )
    if not selected:
        detail = skipped[0].reason if skipped else "没有可回滚配置"
        raise HTTPException(status_code=400, detail="没有可回滚配置：{}".format(detail))
    node_ids = {item["node_id"] for item in selected}
    batch_limit = read_setting(session, "node.batch_max_count", MAX_BATCH_COUNT)
    if len(node_ids) > batch_limit:
        raise HTTPException(
            status_code=400,
            detail="最多只能回滚 {} 个节点".format(batch_limit),
        )
    executor = getattr(request.app.state, "task_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="任务执行器尚未启动")
    session_factory = request.app.state.database.session_factory
    backup_dir = read_setting(
        session, "release.backup_dir", request.app.state.settings.release_backup_dir
    )
    with _SUBMISSION_LOCK:
        active = session.scalar(
            select(func.count(Task.id)).where(
                Task.operation_type.in_(("release_publish", "release_rollback")),
                Task.status.in_(("pending", "running")),
            )
        )
        if active:
            raise HTTPException(
                status_code=409,
                detail="已有发布或回滚批次正在执行，请等待其完成后再操作",
            )
        batch_number = _next_release_batch_number(session)
        session.rollback()
        task_id = create_publish_task(
            session_factory,
            executor,
            request.app.state.credential_encryption_key,
            selected,
            batch_number=batch_number,
            backup_dir=backup_dir,
            trigger_user_id=user.id,
            trigger_ip=request_client_ip(request),
            operation_type="release_rollback",
            max_workers=batch_limit,
        )
    return RollbackCreatedResponse(
        task_id=task_id,
        task_url="/api/tasks/{}".format(task_id),
        batch_number=batch_number,
        item_count=len(selected),
        node_count=len(node_ids),
        skipped=skipped,
        message="回滚批次 {} 已创建，包含 {} 个配置".format(
            batch_number, len(selected)
        ),
    )


@api_router.get(
    "/nodes",
    response_model=ReleaseNodeListResponse,
    summary="分页查询发布中心节点",
    description="需要 releases.read 或 releases.publish 权限；默认仅显示 Nginx 已确认可用的活动节点。",
    responses=api_error_responses((401, 403, 422, 500)),
)
def list_release_nodes(
    search: str = Query("", max_length=200),
    group_id: Optional[int] = Query(None, ge=1),
    environment: str = Query("", max_length=20),
    status: str = Query("", max_length=20),
    sync_status: str = Query("", max_length=20),
    nginx_available: Literal["true", "false", "all"] = "true",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    user: User = Depends(require_release_access),
    session: Session = Depends(get_session),
) -> ReleaseNodeListResponse:
    """返回发布中心节点、分组和绑定状态筛选数据。"""
    if sync_status not in (
        "",
        "pending",
        "synced",
        "failed",
        "orphaned",
        "marked_deleted",
    ):
        raise HTTPException(status_code=422, detail="配置绑定状态无效")
    query = _release_node_query(
        search, group_id, environment, status, sync_status, nginx_available
    )
    total = int(session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    nodes = session.scalars(
        query.options(
            joinedload(Node.credential),
            selectinload(Node.groups),
        )
        .order_by(Node.hostname.asc(), Node.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique().all()
    binding_stats = _node_binding_stats(session, nodes)
    groups = session.scalars(select(NodeGroup).order_by(NodeGroup.name.asc())).all()
    return ReleaseNodeListResponse(
        items=[_node_item(node, binding_stats.get(node.id, (0, 0, 0))) for node in nodes],
        groups=[ReleaseGroupItem(id=group.id, name=group.name) for group in groups],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        status_counts=_release_status_counts(session),
        max_node_count=read_setting(session, "node.batch_max_count", MAX_BATCH_COUNT),
        max_binding_count=MAX_SELECTED_BINDINGS,
    )


@api_router.get(
    "/nodes/{node_id}/bindings",
    response_model=ReleaseBindingsResponse,
    summary="读取节点可发布绑定",
    description="分页返回节点绑定及版本号（包括标记删除项），不包含配置正文；page_size 最大 100。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def list_node_release_bindings(
    node_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    user: User = Depends(require_release_access),
    session: Session = Depends(get_session),
) -> ReleaseBindingsResponse:
    """分页返回节点可发布绑定和版本选择项。"""
    node = session.scalar(
        select(Node)
        .options(joinedload(Node.credential))
        .where(Node.id == node_id, Node.is_deleted.is_(False))
    )
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在或已删除")
    can_publish_node = bool(
        not node.is_locked
        and node.status == "online"
        and node.nginx_available is True
        and node.credential is not None
        and node.credential.is_enabled
    )
    binding_query = (
        select(ConfigBinding)
        .join(ConfigBinding.config)
        .where(ConfigBinding.node_id == node_id)
    )
    total = int(
        session.scalar(
            select(func.count()).select_from(binding_query.subquery())
        )
        or 0
    )
    has_versions = select(BindingVersion.id).where(
        BindingVersion.binding_id == ConfigBinding.id
    ).exists()
    selectable_total = int(
        session.scalar(
            select(func.count(ConfigBinding.id)).where(
                ConfigBinding.node_id == node_id,
                or_(
                    ConfigBinding.sync_status == "marked_deleted",
                    has_versions,
                ),
            )
        )
        or 0
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    bindings = session.scalars(
        binding_query
        .options(
            contains_eager(ConfigBinding.config),
            selectinload(ConfigBinding.versions),
        )
        .order_by(Config.name.asc(), ConfigBinding.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ReleaseBindingsResponse(
        node_id=node.id,
        can_publish=can_publish_node,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        selectable_total=selectable_total,
        bindings=[
            ReleaseBindingItem(
                id=binding.id,
                config_id=binding.config_id,
                config_name=binding.config.name,
                remote_path=binding.remote_path,
                current_version=binding.current_version,
                sync_status=binding.sync_status,
                synced_version=binding.synced_version,
                versions=[
                    ReleaseVersionItem(
                        id=version.id,
                        version=version.version,
                        created_at=version.created_at,
                    )
                    for version in sorted(
                        binding.versions,
                        key=lambda item: item.version,
                        reverse=True,
                    )
                ],
            )
            for binding in bindings
        ],
    )


@api_router.get(
    "/versions/{version_id}",
    response_model=ReleaseVersionContentResponse,
    summary="读取配置版本预览正文",
    description="需要 releases.read 或 releases.publish 权限；仅供发布中心预览。",
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def get_release_version_content(
    version_id: int,
    user: User = Depends(require_release_access),
    session: Session = Depends(get_session),
) -> ReleaseVersionContentResponse:
    """读取指定绑定版本的配置正文用于预览。"""
    version = session.get(BindingVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="配置版本不存在")
    return ReleaseVersionContentResponse(
        version=version.version,
        content=version.content,
    )


@api_router.post(
    "/publish",
    response_model=PublishCreatedResponse,
    status_code=202,
    summary="创建异步发布批次",
    description=(
        "需要 releases.publish 权限。节点数量受 node.batch_max_count 系统设置限制；"
        "同节点配置串行执行，"
        "一次 SSH 会话内完成备份、上传、校验并统一 reload。"
    ),
    responses=api_error_responses((400, 401, 403, 404, 409, 422, 500, 503)),
)
def publish_bindings(
    payload: PublishRequest,
    request: Request,
    user: User = Depends(require_permission("releases", "publish")),
    session: Session = Depends(get_session),
) -> PublishCreatedResponse:
    """校验所选版本和节点门禁后创建统一持久化发布任务。"""
    if not payload.bindings:
        raise HTTPException(status_code=400, detail="请至少选择一个配置绑定")
    binding_ids = [item.binding_id for item in payload.bindings]
    if len(binding_ids) != len(set(binding_ids)):
        raise HTTPException(status_code=400, detail="同一绑定不能重复选择")
    bindings = session.scalars(
        select(ConfigBinding)
        .options(
            joinedload(ConfigBinding.config),
            joinedload(ConfigBinding.node).joinedload(Node.credential),
            selectinload(ConfigBinding.versions),
        )
        .where(ConfigBinding.id.in_(set(binding_ids)))
        .order_by(ConfigBinding.node_id.asc(), ConfigBinding.id.asc())
    ).unique().all()
    if len(bindings) != len(binding_ids):
        raise HTTPException(status_code=404, detail="部分配置绑定不存在")

    selected_by_id = {item.binding_id: item for item in payload.bindings}
    selected = []
    skipped = []
    for binding in bindings:
        node = binding.node
        if node.is_deleted or node.is_locked:
            skipped.append(
                SkippedPublishItem(
                    binding_id=binding.id,
                    node_hostname=node.hostname,
                    reason="节点已删除或锁定",
                )
            )
            continue
        if node.status != "online" or node.nginx_available is not True:
            skipped.append(
                SkippedPublishItem(
                    binding_id=binding.id,
                    node_hostname=node.hostname,
                    reason=(
                        "节点非在线或 Nginx 未确认可用"
                    ),
                )
            )
            continue
        choice = selected_by_id[binding.id]
        if binding.sync_status == "marked_deleted":
            selected.append(
                {
                    "binding_id": binding.id,
                    "config_id": binding.config_id,
                    "config_name": binding.config.name,
                    "node_id": node.id,
                    "hostname": node.hostname,
                    "ip": node.ip,
                    "port": node.port,
                    "nginx_path": node.nginx_path,
                    "version": binding.current_version,
                    "remote_path": binding.remote_path,
                    "action": "delete",
                }
            )
            continue
        version_number = choice.version or binding.current_version
        version = next(
            (
                row
                for row in binding.versions
                if row.version == version_number
            ),
            None,
        )
        if version is None and version_number != binding.current_version:
            raise HTTPException(
                status_code=400,
                detail="绑定 {} 不存在 V{}".format(binding.id, version_number),
            )
        selected.append(
            {
                "binding_id": binding.id,
                "config_id": binding.config_id,
                "config_name": binding.config.name,
                "node_id": node.id,
                "hostname": node.hostname,
                "ip": node.ip,
                "port": node.port,
                "nginx_path": node.nginx_path,
                "version": version_number,
                "remote_path": binding.remote_path,
                "content": version.content if version else binding.content,
                "action": "publish",
            }
        )
    node_ids = {item["node_id"] for item in selected}
    if not selected:
        raise HTTPException(
            status_code=400,
            detail="所选绑定均不可发布（节点离线、锁定、已删除或 Nginx 不可用）",
        )
    batch_limit = read_setting(session, "node.batch_max_count", MAX_BATCH_COUNT)
    if len(node_ids) > batch_limit:
        raise HTTPException(
            status_code=400,
            detail="最多只能选择 {} 个节点".format(batch_limit),
        )
    missing_credentials = [
        item["hostname"]
        for item in bindings
        if item.node_id in node_ids
        and (
            item.node.credential is None
            or not item.node.credential.is_enabled
        )
    ]
    if missing_credentials:
        raise HTTPException(
            status_code=400,
            detail="节点 {} 未关联启用的 SSH 凭证".format(
                ", ".join(sorted(set(missing_credentials)))
            ),
        )

    executor = getattr(request.app.state, "task_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="任务执行器尚未启动")
    session_factory = request.app.state.database.session_factory
    backup_dir = read_setting(
        session, "release.backup_dir", request.app.state.settings.release_backup_dir
    )
    with _SUBMISSION_LOCK:
        active = session.scalar(
            select(func.count(Task.id)).where(
                Task.operation_type.in_(("release_publish", "release_rollback")),
                Task.status.in_(("pending", "running")),
            )
        )
        if active:
            raise HTTPException(
                status_code=409,
                detail="已有发布或回滚批次正在执行，请等待其完成后再发布",
            )
        batch_number = _next_release_batch_number(session)
        session.rollback()
        task_id = create_publish_task(
            session_factory,
            executor,
            request.app.state.credential_encryption_key,
            selected,
            batch_number=batch_number,
            backup_dir=backup_dir,
            trigger_user_id=user.id,
            trigger_ip=request_client_ip(request),
            max_workers=batch_limit,
        )
    return PublishCreatedResponse(
        task_id=task_id,
        task_url="/api/tasks/{}".format(task_id),
        batch_number=batch_number,
        item_count=len(selected),
        node_count=len(node_ids),
        skipped=skipped,
        message="发布批次 {} 已创建，包含 {} 个配置".format(
            batch_number, len(selected)
        ),
    )
