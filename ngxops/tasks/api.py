"""提供统一任务列表、进度轮询、结果日志和协作式取消 API。"""

import json
from datetime import datetime
from typing import Any, List, Literal, Optional, Set, Tuple

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ngxops.accounts.models import User
from ngxops.audit.service import request_client_ip
from ngxops.api.contracts import api_error_response, api_error_responses
from ngxops.database.session import get_session
from ngxops.security.dependencies import require_authenticated_user
from ngxops.security.errors import PermissionDenied
from ngxops.tasks.executor import cancel_task
from ngxops.tasks.models import OPERATION_TYPES, Task, TaskLog
from ngxops.tasks.presentation import task_search_filters


TaskStatus = Literal["pending", "running", "success", "failed", "cancelled"]
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_LIMITED_OPERATION_PERMISSIONS = {
    ("releases", "publish"): ("release_publish", "release_rollback"),
    ("nodes", "ssh_test"): (
        "node_ssh_test",
        "node_batch_test",
        "node_system_info",
        "node_nginx_version",
    ),
    ("nodes", "unlock"): ("node_batch_test",),
    ("credentials", "enable"): ("credential_enable_test",),
    ("configs", "sync"): ("config_batch_sync", "config_discover"),
    ("nginx_service", "operate"): ("nginx_service_control",),
    ("upgrade", "execute"): ("nginx_upgrade", "nginx_rollback", "nginx_install"),
    ("nginx_uninstall", "read"): ("nginx_uninstall",),
    ("nginx_uninstall", "execute"): ("nginx_uninstall",),
}
_UPGRADE_POLL_OPERATIONS = frozenset(
    ("nginx_upgrade", "nginx_rollback", "nginx_install")
)


class TaskListItem(BaseModel):
    """描述列表和轮询共用的任务摘要。"""

    id: int
    operation_type: str
    status: TaskStatus
    detail: str
    progress: int
    source_batch: str
    target_hostnames: str
    target_ips: str
    target_configs: str
    trigger_user_id: Optional[int]
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class TaskLogResponse(BaseModel):
    """描述单条持久化任务日志。"""

    id: int
    level: str
    message: str
    created_at: datetime


class TaskListResponse(BaseModel):
    """描述任务列表与分页总数。"""

    success: bool = True
    items: List[TaskListItem]
    page: int
    page_size: int
    total: int


class TaskDetailResponse(TaskListItem):
    """描述任务状态、结构化结果树与增量日志。"""

    result_tree: Any = None
    logs: List[TaskLogResponse]
    next_log_id: int
    has_more_logs: bool


class TaskCancelResponse(BaseModel):
    """描述协作式取消请求结果。"""

    success: bool = True
    message: str
    task_id: int
    status: Literal["cancelled"]


def _has_permission(
    request: Request,
    session: Session,
    user: User,
    resource: str,
    action: str,
) -> bool:
    """通过应用注册的 RBAC 解析器检查单项权限。"""
    checker = getattr(request.app.state, "permission_checker", None)
    return bool(checker and checker(session, user, resource, action))


def _task_permissions(
    request: Request,
    session: Session,
    user: User,
    include_poll_permissions: bool = False,
) -> Tuple[bool, Set[str], bool]:
    """计算任务中心全量权限、本人可见任务类型及入口权限。"""
    if user.is_superuser or _has_permission(
        request, session, user, "releases", "read"
    ):
        return True, set(OPERATION_TYPES), True

    allowed_operations: Set[str] = set()
    has_center_permission = False
    for permission, operation_types in _LIMITED_OPERATION_PERMISSIONS.items():
        if _has_permission(request, session, user, permission[0], permission[1]):
            allowed_operations.update(operation_types)
            has_center_permission = True

    if include_poll_permissions and _has_permission(
        request, session, user, "upgrade", "read"
    ):
        allowed_operations.update(_UPGRADE_POLL_OPERATIONS)
        has_center_permission = True
    return False, allowed_operations, has_center_permission


def _task_list_item(task: Task) -> TaskListItem:
    """将 ORM 任务转换为不含敏感字段的列表响应。"""
    return TaskListItem(
        id=task.id,
        operation_type=task.operation_type,
        status=task.status,
        detail=task.detail,
        progress=task.progress,
        source_batch=task.source_batch,
        target_hostnames=task.target_hostnames,
        target_ips=task.target_ips,
        target_configs=task.target_configs,
        trigger_user_id=task.trigger_user_id,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _read_result_tree(serialized: Optional[str]) -> Any:
    """解码持久化的 JSON 结果树并容忍历史坏数据。"""
    if not serialized:
        return None
    try:
        return json.loads(serialized)
    except (TypeError, ValueError):
        return None


def _visible_task_query(
    session: Session,
    task_id: int,
    user: User,
    can_read_all: bool,
    allowed_operations: Set[str],
) -> Optional[Task]:
    """按发布读权限或本人受限任务范围读取任务。"""
    query = select(Task).where(Task.id == task_id)
    if not can_read_all:
        if not allowed_operations:
            return None
        query = query.where(
            Task.operation_type.in_(allowed_operations),
            Task.trigger_user_id == user.id,
        )
    return session.scalars(query).one_or_none()


@router.get(
    "",
    response_model=TaskListResponse,
    summary="分页查询可见任务",
    description=(
        "releases.read 可查询全部任务；其他账号仅查询本人有对应业务权限的任务。"
        "search 使用逗号分隔关键词，词间 AND，每个关键词在批次号、主机名和 IP 中 OR。"
        "该接口用于轮询列表摘要，不返回结果树和日志正文。"
    ),
    responses=api_error_responses((401, 403, 422, 500)),
)
def list_tasks(
    request: Request,
    status: Optional[TaskStatus] = None,
    operation_type: Optional[str] = Query(None, max_length=40),
    source_batch: Optional[str] = Query(None, max_length=64),
    search: Optional[str] = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> TaskListResponse:
    """返回经过当前用户任务范围过滤的分页结果。"""
    if operation_type and operation_type not in OPERATION_TYPES:
        raise HTTPException(status_code=422, detail="任务类型无效")
    can_read_all, allowed_operations, has_access = _task_permissions(
        request, session, user, include_poll_permissions=True
    )
    if not has_access:
        raise PermissionDenied("无访问权限", "当前账号没有查看任务的权限。")

    conditions = []
    if not can_read_all:
        if not allowed_operations:
            return TaskListResponse(items=[], page=page, page_size=page_size, total=0)
        conditions.append(
            (Task.operation_type.in_(allowed_operations))
            & (Task.trigger_user_id == user.id)
        )
    if status:
        conditions.append(Task.status == status)
    if operation_type:
        conditions.append(Task.operation_type == operation_type)
    if source_batch:
        conditions.append(Task.source_batch == source_batch)
    conditions.extend(task_search_filters(search or ""))

    query = select(Task)
    count_query = select(func.count()).select_from(Task)
    for condition in conditions:
        query = query.where(condition)
        count_query = count_query.where(condition)
    total = int(session.scalar(count_query) or 0)
    tasks = session.scalars(
        query.order_by(Task.created_at.desc(), Task.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return TaskListResponse(
        items=[_task_list_item(task) for task in tasks],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{task_id}",
    response_model=TaskDetailResponse,
    summary="读取任务进度、结果和日志",
    description=(
        "返回任务当前状态及结构化结果树。日志按自增 ID 游标读取；"
        "后续轮询将 next_log_id 作为 after_log_id。"
    ),
    responses=api_error_responses((401, 403, 404, 422, 500)),
)
def get_task(
    request: Request,
    task_id: int = Path(..., ge=1),
    after_log_id: int = Query(0, ge=0),
    log_limit: int = Query(200, ge=1, le=500),
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> Any:
    """读取当前用户有权访问的任务并增量返回日志。"""
    can_read_all, allowed_operations, has_access = _task_permissions(
        request, session, user, include_poll_permissions=True
    )
    if not has_access:
        raise PermissionDenied("无访问权限", "当前账号没有查看任务的权限。")
    task = _visible_task_query(
        session, task_id, user, can_read_all, allowed_operations
    )
    if task is None:
        return _api_error(404, "任务不存在或无权查看")

    log_rows = session.scalars(
        select(TaskLog)
        .where(TaskLog.task_id == task_id, TaskLog.id > after_log_id)
        .order_by(TaskLog.id.asc())
        .limit(log_limit + 1)
    ).all()
    has_more = len(log_rows) > log_limit
    selected_logs = log_rows[:log_limit]
    next_log_id = selected_logs[-1].id if selected_logs else after_log_id
    response = _task_list_item(task).model_dump()
    response.update(
        {
            "result_tree": _read_result_tree(task.result_tree_json),
            "logs": [
                TaskLogResponse(
                    id=row.id,
                    level=row.level,
                    message=row.message,
                    created_at=row.created_at,
                )
                for row in selected_logs
            ],
            "next_log_id": next_log_id,
            "has_more_logs": has_more,
        }
    )
    return TaskDetailResponse(**response)


@router.post(
    "/{task_id}/cancel",
    response_model=TaskCancelResponse,
    summary="协作式取消活跃任务",
    description=(
        "将 pending/running 任务立即标记为 cancelled；运行中的业务函数应定期调用"
        " TaskContext.check_cancelled()。取消不会强制终止远端命令。"
        "请求必须携带 csrftoken Cookie 与 X-CSRFToken 请求头。"
    ),
    responses=api_error_responses((400, 401, 403, 404, 409, 422, 500)),
)
def cancel_task_endpoint(
    request: Request,
    task_id: int = Path(..., ge=1),
    user: User = Depends(require_authenticated_user),
    session: Session = Depends(get_session),
) -> Any:
    """按任务可见范围取消活跃任务且避免终态被覆盖。"""
    can_read_all, allowed_operations, has_access = _task_permissions(
        request, session, user
    )
    if not has_access:
        raise PermissionDenied("无访问权限", "当前账号没有取消任务的权限。")
    task = _visible_task_query(
        session, task_id, user, can_read_all, allowed_operations
    )
    if task is None:
        return _api_error(404, "任务不存在或无权操作")
    if task.status not in ("pending", "running"):
        return _api_error(400, "当前任务状态不可取消")
    if task.operation_type in ("nginx_upgrade", "nginx_rollback"):
        from ngxops.upgrade.models import NginxUpgradeRun

        run = session.scalars(
            select(NginxUpgradeRun).where(NginxUpgradeRun.task_id == task_id)
        ).one_or_none()
        if run is None or run.upgrade_mode == "rollback" or run.phase not in (
            "pending",
            "fetching_config",
            "uploading_package",
        ):
            return _api_error(400, "当前任务状态不可取消")
        if not user.is_superuser and not _has_permission(
            request, session, user, "upgrade", "execute"
        ):
            raise PermissionDenied("无访问权限", "当前账号没有取消升级任务的权限。")
    if task.operation_type == "nginx_install":
        from ngxops.nginx_install.models import NginxInstallRun

        run = session.scalars(
            select(NginxInstallRun).where(NginxInstallRun.task_id == task_id)
        ).one_or_none()
        if run is None or run.phase not in (
            "pending",
            "checking_tools",
            "uploading_package",
            "extracting_package",
            "preparing_modules",
        ):
            return _api_error(400, "当前安装阶段不可取消")
        if not user.is_superuser and not _has_permission(
            request, session, user, "upgrade", "execute"
        ):
            raise PermissionDenied("无访问权限", "当前账号没有取消安装任务的权限。")
    if task.operation_type == "nginx_uninstall" and not _has_permission(
        request, session, user, "nginx_uninstall", "execute"
    ):
        raise PermissionDenied("无访问权限", "当前账号没有取消卸载任务的权限。")
    session.rollback()
    if not cancel_task(
        request.app.state.database.session_factory,
        task_id,
        actor_id=user.id,
        actor_username=user.username,
        actor_ip=request_client_ip(request),
    ):
        return _api_error(409, "取消失败，任务状态可能已变更")
    executor = getattr(request.app.state, "task_executor", None)
    if executor is not None:
        executor.cancel(task_id)
    return TaskCancelResponse(
        message="任务已取消；运行中的步骤会在下一个检查点停止。",
        task_id=task_id,
        status="cancelled",
    )


def _api_error(status_code: int, message: str) -> Any:
    """构造符合任务 API 错误协议的 JSON 响应。"""
    return api_error_response(status_code, message)
