"""提供受保护的配置发现与异步同步 JSON 接口。"""

import posixpath
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ngxops.accounts.models import User
from ngxops.api.contracts import api_error_responses
from ngxops.audit.service import request_client_ip
from ngxops.database.session import get_session
from ngxops.configs.models import ConfigSyncSetting
from ngxops.nodes.models import Node
from ngxops.security.dependencies import require_permission
from ngxops.settings.service import read_setting
from ngxops.configs.tasks import (
    DEFAULT_MAIN_CONF_PATH,
    create_batch_sync_task,
    create_discovery_task,
    create_sync_task,
)


api_router = APIRouter(prefix="/api/configs", tags=["configs"])
MAX_BATCH_COUNT = 3
MAX_SELECTED_PATHS = 500


class ConfigDiscoveryRequest(BaseModel):
    """描述单节点配置路径发现请求。"""

    node_id: int = Field(gt=0)
    main_conf_path: Optional[str] = Field(default=None, max_length=500)


class ConfigSyncRequest(BaseModel):
    """描述单节点全量或部分配置同步请求。"""

    node_id: int = Field(gt=0)
    main_conf_path: Optional[str] = Field(default=None, max_length=500)
    mode: Literal["full", "partial"] = "full"
    selected_paths: List[str] = Field(
        default_factory=list,
        max_length=MAX_SELECTED_PATHS,
    )


class ConfigBatchSyncRequest(BaseModel):
    """描述受系统批量上限约束的节点全量同步请求。"""

    node_ids: List[int] = Field(min_length=1, max_length=100)


class ConfigTaskCreatedResponse(BaseModel):
    """描述配置发现或同步任务创建结果。"""

    success: bool = True
    task_id: int
    task_url: str
    operation_type: Literal["config_discover", "config_batch_sync"]


def _validate_main_conf_path(value: Optional[str]) -> Optional[str]:
    """校验主配置路径为非空、绝对且长度受限的 POSIX 路径。"""
    if value is None:
        return None
    path = value.strip()
    if not path:
        raise HTTPException(status_code=400, detail="请输入 Nginx 主配置文件路径")
    if "\x00" in path or not path.startswith("/"):
        raise HTTPException(status_code=400, detail="主配置路径必须是绝对路径")
    normalized = posixpath.normpath(path)
    if len(normalized) > 500:
        raise HTTPException(status_code=400, detail="主配置路径不能超过 500 个字符")
    return normalized


def _load_eligible_node(session: Session, node_id: int) -> Node:
    """读取单个未删除、未锁定且 Nginx 可用的在线节点。"""
    node = session.scalar(
        select(Node)
        .options(joinedload(Node.credential))
        .where(Node.id == node_id, Node.is_deleted.is_(False))
    )
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在或已删除")
    if node.is_locked:
        raise HTTPException(status_code=400, detail="节点已锁定")
    if node.status != "online":
        raise HTTPException(
            status_code=400,
            detail="节点 {} 非在线状态".format(node.hostname),
        )
    if node.nginx_available is not True:
        raise HTTPException(
            status_code=400,
            detail="节点 {} 未确认 Nginx 可用".format(node.hostname),
        )
    if node.credential is None:
        raise HTTPException(status_code=400, detail="节点未配置 SSH 凭证")
    if not node.credential.is_enabled:
        raise HTTPException(status_code=400, detail="关联凭证已禁用")
    return node


def _resolve_main_conf_path(
    session: Session,
    node: Node,
    requested_path: Optional[str],
    updated_by: int,
    *,
    save: bool,
) -> str:
    """读取节点主配置路径并按请求选择是否保存新值。"""
    path = _validate_main_conf_path(requested_path)
    setting = session.scalar(
        select(ConfigSyncSetting).where(ConfigSyncSetting.node_id == node.id)
    )
    if path is None:
        path = (
            setting.main_conf_path
            if setting is not None and setting.main_conf_path
            else DEFAULT_MAIN_CONF_PATH
        )
    if save:
        if setting is None:
            setting = ConfigSyncSetting(
                node_id=node.id,
                main_conf_path=path,
                updated_by=updated_by,
            )
            session.add(setting)
        elif setting.main_conf_path != path or setting.updated_by != updated_by:
            setting.main_conf_path = path
            setting.updated_by = updated_by
        session.commit()
    return path


def _task_url(task_id: int) -> str:
    """构造任务详情轮询 API 路径。"""
    return "/api/tasks/{}".format(task_id)


def _ensure_executor(request: Request):
    """读取已启动的任务执行器，不可用时返回 503。"""
    executor = getattr(request.app.state, "task_executor", None)
    if executor is None:
        raise HTTPException(status_code=503, detail="任务执行器尚未启动")
    return executor


@api_router.post(
    "/discover",
    response_model=ConfigTaskCreatedResponse,
    status_code=202,
    summary="异步发现节点 Nginx 配置",
    description=(
        "需要 configs.sync 权限。通过 SSH 读取主配置与递归 include，"
        "持久化任务进度和不含正文的远程文件路径清单。"
    ),
    responses=api_error_responses((400, 401, 403, 404, 422, 500, 503)),
)
def discover_configs(
    payload: ConfigDiscoveryRequest,
    request: Request,
    user: User = Depends(require_permission("configs", "sync")),
    session: Session = Depends(get_session),
) -> ConfigTaskCreatedResponse:
    """创建只读远程配置发现任务。"""
    node = _load_eligible_node(session, payload.node_id)
    main_conf_path = _resolve_main_conf_path(
        session,
        node,
        payload.main_conf_path,
        user.id,
        save=True,
    )
    task_id = create_discovery_task(
        request.app.state.database.session_factory,
        _ensure_executor(request),
        request.app.state.credential_encryption_key,
        node_id=node.id,
        hostname=node.hostname,
        ip=node.ip,
        main_conf_path=main_conf_path,
        trigger_user_id=user.id,
        trigger_ip=request_client_ip(request),
    )
    return ConfigTaskCreatedResponse(
        task_id=task_id,
        task_url=_task_url(task_id),
        operation_type="config_discover",
    )


@api_router.post(
    "/sync",
    response_model=ConfigTaskCreatedResponse,
    status_code=202,
    summary="创建单节点配置同步任务",
    description=(
        "需要 configs.sync 权限。全量同步会更新发现到的绑定、标记确认缺失项并清理已标记删除项；"
        "部分同步只写入本次发现且明确选中的路径。"
    ),
    responses=api_error_responses((400, 401, 403, 404, 422, 500, 503)),
)
def sync_configs(
    payload: ConfigSyncRequest,
    request: Request,
    user: User = Depends(require_permission("configs", "sync")),
    session: Session = Depends(get_session),
) -> ConfigTaskCreatedResponse:
    """创建单节点全量或部分配置同步任务。"""
    node = _load_eligible_node(session, payload.node_id)
    main_conf_path = _resolve_main_conf_path(
        session,
        node,
        payload.main_conf_path,
        user.id,
        save=True,
    )
    selected_paths = [path.strip() for path in payload.selected_paths if path.strip()]
    if len(selected_paths) != len(set(selected_paths)):
        raise HTTPException(status_code=400, detail="所选配置路径不能重复")
    if payload.mode == "partial" and not selected_paths:
        raise HTTPException(status_code=400, detail="部分同步至少选择一个配置文件")
    if payload.mode == "full" and selected_paths:
        raise HTTPException(status_code=400, detail="全量同步不能提交部分路径")
    if any("\x00" in path or not path.startswith("/") for path in selected_paths):
        raise HTTPException(status_code=400, detail="配置路径必须是绝对路径")
    if any(len(path) > 500 for path in selected_paths):
        raise HTTPException(status_code=400, detail="配置路径不能超过 500 个字符")
    task_id = create_sync_task(
        request.app.state.database.session_factory,
        _ensure_executor(request),
        request.app.state.credential_encryption_key,
        node_id=node.id,
        hostname=node.hostname,
        ip=node.ip,
        main_conf_path=main_conf_path,
        trigger_user_id=user.id,
        trigger_ip=request_client_ip(request),
        mode=payload.mode,
        selected_paths=selected_paths,
    )
    return ConfigTaskCreatedResponse(
        task_id=task_id,
        task_url=_task_url(task_id),
        operation_type="config_batch_sync",
    )


@api_router.post(
    "/sync/batch",
    response_model=ConfigTaskCreatedResponse,
    status_code=202,
    summary="创建批量配置同步任务",
    description=(
        "需要 configs.sync 权限。节点数量受 node.batch_max_count 系统设置限制；"
        "仅接受活动、未锁定、SSH 在线、"
        "Nginx 可用且凭证启用的节点，使用各节点已保存的主配置路径。"
    ),
    responses=api_error_responses((400, 401, 403, 404, 422, 500, 503)),
)
def sync_configs_batch(
    payload: ConfigBatchSyncRequest,
    request: Request,
    user: User = Depends(require_permission("configs", "sync")),
    session: Session = Depends(get_session),
) -> ConfigTaskCreatedResponse:
    """按系统批量上限验证节点并创建并行配置同步任务。"""
    node_ids = payload.node_ids
    batch_limit = read_setting(session, "node.batch_max_count", MAX_BATCH_COUNT)
    if len(node_ids) > batch_limit:
        raise HTTPException(status_code=400, detail="单次最多选择 {} 台节点".format(batch_limit))
    if len(node_ids) != len(set(node_ids)):
        raise HTTPException(status_code=400, detail="节点不能重复选择")
    nodes = session.scalars(
        select(Node)
        .options(joinedload(Node.credential))
        .where(Node.id.in_(set(node_ids)), Node.is_deleted.is_(False))
        .order_by(Node.id.asc())
    ).unique().all()
    if len(nodes) != len(node_ids):
        raise HTTPException(status_code=404, detail="部分节点不存在或已删除")
    for node in nodes:
        if node.is_locked:
            raise HTTPException(
                status_code=400,
                detail="节点 {} 已锁定".format(node.hostname),
            )
        if node.status != "online":
            raise HTTPException(
                status_code=400,
                detail="节点 {} 非在线状态".format(node.hostname),
            )
        if node.nginx_available is not True:
            raise HTTPException(
                status_code=400,
                detail="节点 {} 未确认 Nginx 可用".format(node.hostname),
            )
        if node.credential is None or not node.credential.is_enabled:
            raise HTTPException(
                status_code=400,
                detail="节点 {} 未关联启用的 SSH 凭证".format(node.hostname),
            )
    task_id = create_batch_sync_task(
        request.app.state.database.session_factory,
        _ensure_executor(request),
        request.app.state.credential_encryption_key,
        targets=[
            {"id": node.id, "hostname": node.hostname, "ip": node.ip}
            for node in nodes
        ],
        trigger_user_id=user.id,
        trigger_ip=request_client_ip(request),
        max_workers=batch_limit,
    )
    return ConfigTaskCreatedResponse(
        task_id=task_id,
        task_url=_task_url(task_id),
        operation_type="config_batch_sync",
    )
