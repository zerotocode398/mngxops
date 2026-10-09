"""提供配置标签、节点绑定及版本历史的 Jinja2 页面。"""

import math
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager, joinedload

from ngxops.accounts.models import User
from ngxops.configs.models import (
    BindingVersion,
    Config,
    ConfigBinding,
)
from ngxops.configs.services import (
    build_split_diff_rows,
    remove_binding,
    restore_binding_revision,
    restore_marked_binding,
    save_binding_revision,
)
from ngxops.configs.tasks import DEFAULT_MAIN_CONF_PATH
from ngxops.database.session import get_session
from ngxops.nodes.models import Node
from ngxops.security.dependencies import require_permission
from ngxops.settings.service import read_setting
from ngxops.ui import render_page


router = APIRouter(prefix="/configs", tags=["configs"])
PAGE_SIZES = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 25
CONFIG_PAGE_SIZES = (10, 20, 50, 100)
DEFAULT_CONFIG_PAGE_SIZE = 10
CONFIG_FILTER_STATUSES = (
    "pending",
    "synced",
    "orphaned",
    "failed",
    "marked_deleted",
)
NOTICE_KEY = "ngxops_config_notice"
SOURCE_LABELS = {"manual": "手动创建", "discovered": "远程发现"}
STATUS_LABELS = {
    "not_synced": "未同步",
    "synced": "已同步",
    "modified": "本地已修改",
    "orphaned": "远程已删除",
    "failed": "同步失败",
    "marked_deleted": "已标记删除",
}


def _render(
    request: Request,
    template: str,
    user: User,
    db_session: Session,
    context: Optional[dict] = None,
    status_code: int = 200,
) -> Response:
    """读取一次性提示并使用全局布局渲染配置页面。"""
    page_context = dict(context or {})
    notice = request.session.pop(NOTICE_KEY, None)
    if isinstance(notice, dict):
        page_context["notice"] = notice
    return render_page(
        request,
        template,
        context=page_context,
        user=user,
        db_session=db_session,
        status_code=status_code,
    )


def _notice_redirect(
    request: Request,
    path: str,
    message: str,
    kind: str = "success",
) -> RedirectResponse:
    """保存页面一次性提示并以 303 跳转。"""
    request.session[NOTICE_KEY] = {"message": message, "type": kind}
    return RedirectResponse(path, status_code=303)


def _pagination(
    request: Request,
    total: int,
    requested_page: int,
    requested_size: int,
) -> Tuple[int, int, dict]:
    """规范分页参数并构造公共分页组件使用的数据。"""
    size = requested_size if requested_size in PAGE_SIZES else DEFAULT_PAGE_SIZE
    pages = max(1, int(math.ceil(total / float(size))))
    page = min(max(1, requested_page), pages)
    return page, size, {
        "page": page,
        "pages": pages,
        "total": total,
        "per_page": size,
        "per_page_options": PAGE_SIZES,
    }


def _config_list_filters(params: dict) -> Tuple[str, str, str]:
    """规范配置列表的搜索、绑定状态和 Nginx 筛选值。"""
    search = str(params.get("search", "")).strip()[:200]
    sync_status = str(params.get("sync_status", "")).strip()
    nginx_filter = str(params.get("nginx_available", "true")).strip()
    if nginx_filter not in ("true", "all"):
        nginx_filter = "true"
    if sync_status not in CONFIG_FILTER_STATUSES:
        sync_status = ""
    return search, sync_status, nginx_filter


def _config_node_query(
    search: str,
    sync_status: str,
    nginx_filter: str,
):
    """构造节点列表筛选查询并以 EXISTS 避免重复节点。"""
    query = select(Node).where(
        Node.is_deleted.is_(False),
        Node.is_locked.is_(False),
    )
    terms = [
        term.strip()
        for term in search.replace("，", ",").split(",")
        if term.strip()
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
                matching_binding,
            )
        )
    if sync_status:
        allowed_statuses = (
            ("not_synced", "modified")
            if sync_status == "pending"
            else (sync_status,)
        )
        matching_status = select(ConfigBinding.id).where(
            ConfigBinding.node_id == Node.id,
            ConfigBinding.sync_status.in_(allowed_statuses),
        ).exists()
        query = query.where(matching_status)
    if nginx_filter == "true":
        query = query.where(Node.nginx_available.is_(True))
    return query


def _config_status_counts(db_session: Session) -> dict:
    """统计所有未逻辑删除节点上的绑定状态。"""
    counts = {key: 0 for key in CONFIG_FILTER_STATUSES}
    counts["total"] = 0
    rows = db_session.execute(
        select(ConfigBinding.sync_status, func.count(ConfigBinding.id))
        .join(Node, Node.id == ConfigBinding.node_id)
        .where(Node.is_deleted.is_(False))
        .group_by(ConfigBinding.sync_status)
    ).all()
    for binding_status, count in rows:
        counts["total"] += count
        if binding_status in ("not_synced", "modified"):
            counts["pending"] += count
        elif binding_status in counts:
            counts[binding_status] += count
    return counts


def _config_node_rows(db_session: Session, nodes: List[Node]) -> List[dict]:
    """读取当前页节点的绑定状态汇总。"""
    node_ids = [node.id for node in nodes]
    counts_by_node = {
        node_id: {key: 0 for key in CONFIG_FILTER_STATUSES}
        for node_id in node_ids
    }
    if node_ids:
        for node_id, status, count in db_session.execute(
            select(
                ConfigBinding.node_id,
                ConfigBinding.sync_status,
                func.count(ConfigBinding.id),
            )
            .where(ConfigBinding.node_id.in_(node_ids))
            .group_by(ConfigBinding.node_id, ConfigBinding.sync_status)
        ):
            stats = counts_by_node[node_id]
            if status in ("not_synced", "modified"):
                stats["pending"] += count
            elif status in stats:
                stats[status] += count

    rows = []
    for node in nodes:
        stats = counts_by_node[node.id]
        stats["total"] = sum(stats.values())
        rows.append({"node": node, "stats": stats})
    return rows


def _config_list_context(
    request: Request,
    user: User,
    db_session: Session,
    params: Optional[dict] = None,
) -> dict:
    """按配置列表筛选条件聚合节点、绑定和未绑定标签。"""
    query_params = params or dict(request.query_params)
    search, sync_status, nginx_filter = _config_list_filters(query_params)
    query = _config_node_query(search, sync_status, nginx_filter)
    total = db_session.scalar(select(func.count()).select_from(query.subquery())) or 0
    try:
        requested_page = max(1, int(query_params.get("page", 1)))
    except (TypeError, ValueError):
        requested_page = 1
    try:
        requested_size = int(query_params.get("per_page", DEFAULT_CONFIG_PAGE_SIZE))
    except (TypeError, ValueError):
        requested_size = DEFAULT_CONFIG_PAGE_SIZE
    size = (
        requested_size
        if requested_size in CONFIG_PAGE_SIZES
        else DEFAULT_CONFIG_PAGE_SIZE
    )
    pages = max(1, int(math.ceil(total / float(size))))
    page = min(requested_page, pages)
    pagination = {
        "page": page,
        "pages": pages,
        "total": total,
        "per_page": size,
        "per_page_options": CONFIG_PAGE_SIZES,
    }
    nodes = db_session.scalars(
        query.options(joinedload(Node.groups))
        .order_by(Node.hostname.asc(), Node.id.asc())
        .offset((page - 1) * size)
        .limit(size)
    ).unique().all()
    node_rows = _config_node_rows(db_session, nodes)
    status_counts = _config_status_counts(db_session)
    can_create = _can(user, request, db_session, "create")
    has_status_filter = bool(sync_status)
    show_unbound = not (search or has_status_filter)
    unbound_configs = []
    if show_unbound:
        unbound_configs = db_session.scalars(
            select(Config)
            .options(joinedload(Config.creator))
            .where(
                ~select(ConfigBinding.id)
                .where(ConfigBinding.config_id == Config.id)
                .exists()
            )
            .order_by(Config.created_at.desc(), Config.id.desc())
        ).all()

    count_base = select(Node).where(
        Node.is_deleted.is_(False),
        Node.is_locked.is_(False),
    )
    nginx_available_count = db_session.scalar(
        select(func.count()).select_from(
            count_base.where(Node.nginx_available.is_(True)).subquery()
        )
    ) or 0
    total_nodes_count = db_session.scalar(
        select(func.count()).select_from(count_base.subquery())
    ) or 0
    current_query = urlencode(list(query_params.items()))
    list_url = "/configs/"
    if current_query:
        list_url += "?" + current_query

    return {
        "node_rows": node_rows,
        "search": search,
        "sync_status": sync_status,
        "nginx_filter": nginx_filter,
        "has_any_filter": bool(
            search or sync_status or nginx_filter == "all"
        ),
        "status_counts": status_counts,
        "nginx_available_count": nginx_available_count,
        "total_nodes_count": total_nodes_count,
        "unbound_configs": unbound_configs,
        "show_unbound": show_unbound,
        "pagination": pagination,
        "current_list_url": list_url,
        "can_create": can_create,
        "can_update": _can(user, request, db_session, "update"),
        "can_delete": _can(user, request, db_session, "delete"),
        "can_sync": _can(user, request, db_session, "sync"),
        "source_labels": SOURCE_LABELS,
        "status_labels": STATUS_LABELS,
    }


def _safe_configs_return(value: str) -> Optional[str]:
    """仅接受站内配置管理路径作为表单完成后的返回地址。"""
    candidate = (value or "").strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/configs/"):
        return None
    if parsed.path.startswith("//"):
        return None
    return parsed.path + (("?" + parsed.query) if parsed.query else "")


def _can(user: User, request: Request, db_session: Session, action: str) -> bool:
    """按共享 RBAC 解析器判断当前用户是否有指定配置权限。"""
    if user.is_superuser:
        return True
    checker = getattr(request.app.state, "permission_checker", None)
    return bool(checker and checker(db_session, user, "configs", action))


def _node_gate_message(node: Node) -> Optional[str]:
    """返回节点不能创建或修改配置绑定的原因。"""
    if node.is_deleted:
        return "节点已从运维清单移除"
    if node.is_locked:
        return "节点已锁定，无法操作配置绑定"
    if node.status != "online":
        return "节点 {} 非在线状态".format(node.hostname)
    if node.nginx_available is False:
        return "节点 {} 未检测到 Nginx，请先安装".format(node.hostname)
    if node.nginx_available is not True:
        return "节点 {} 尚未探测 Nginx，请先测试连接或采集版本".format(node.hostname)
    return None


def _load_binding(db_session: Session, binding_id: int) -> ConfigBinding:
    """读取绑定及标签、节点关联，不存在时返回 404。"""
    binding = db_session.scalar(
        select(ConfigBinding)
        .options(
            joinedload(ConfigBinding.config),
            joinedload(ConfigBinding.node),
        )
        .where(ConfigBinding.id == binding_id)
    )
    if binding is None:
        raise HTTPException(status_code=404, detail="配置绑定不存在")
    return binding


def _eligible_nodes(db_session: Session) -> List[Node]:
    """读取未锁定且在线并确认安装 Nginx 的活跃节点。"""
    return db_session.scalars(
        select(Node)
        .options(joinedload(Node.groups))
        .where(
            Node.is_deleted.is_(False),
            Node.is_locked.is_(False),
            Node.status == "online",
            Node.nginx_available.is_(True),
        )
        .order_by(Node.hostname.asc())
    ).unique().all()


def _config_form_context(
    values: dict,
    errors: Optional[dict] = None,
    config: Optional[Config] = None,
    node: Optional[Node] = None,
    eligible_nodes: Optional[List[Node]] = None,
    selected_node_ids: Optional[Set[int]] = None,
    return_to: str = "/configs/",
) -> dict:
    """构造配置标签新增或编辑表单的回显上下文。"""
    return {
        "values": values,
        "errors": errors or {},
        "config": config,
        "node": node,
        "eligible_nodes": eligible_nodes or [],
        "selected_node_ids": selected_node_ids or set(),
        "return_to": return_to,
    }


def _binding_form_context(
    db_session: Session,
    values: dict,
    errors: Optional[dict] = None,
    binding: Optional[ConfigBinding] = None,
    selected_node_ids: Optional[Set[int]] = None,
) -> dict:
    """构造绑定新增或编辑表单及可选节点信息。"""
    return {
        "values": values,
        "errors": errors or {},
        "binding": binding,
        "configs": db_session.scalars(select(Config).order_by(Config.name.asc())).all(),
        "nodes": _eligible_nodes(db_session),
        "selected_node_ids": selected_node_ids or set(),
    }


@router.get("/", response_class=Response, summary="配置标签列表")
def list_configs(
    request: Request,
    user: User = Depends(require_permission("configs", "read")),
    db_session: Session = Depends(get_session),
) -> Response:
    """按筛选条件分页显示节点及其配置绑定状态。"""
    return _render(
        request,
        "configs/list.html",
        user,
        db_session,
        _config_list_context(request, user, db_session),
    )


@router.get("/nodes/{node_id}/", response_class=Response, summary="节点配置明细")
def node_configs(
    request: Request,
    node_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PAGE_SIZE),
    user: User = Depends(require_permission("configs", "read")),
    db_session: Session = Depends(get_session),
) -> Response:
    """分页显示指定节点的配置绑定明细。"""
    node = db_session.scalar(
        select(Node)
        .options(joinedload(Node.groups))
        .where(Node.id == node_id, Node.is_deleted.is_(False))
    )
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")

    search = request.query_params.get("search", "").strip()[:200]
    sync_status = request.query_params.get("sync_status", "").strip()
    if sync_status not in CONFIG_FILTER_STATUSES:
        sync_status = ""
    query = select(ConfigBinding).join(Config).where(ConfigBinding.node_id == node.id)
    terms = [
        term.strip()
        for term in search.replace("，", ",").split(",")
        if term.strip()
    ]
    for term in terms:
        query = query.where(
            or_(
                Config.name.contains(term, autoescape=True),
                ConfigBinding.remote_path.contains(term, autoescape=True),
            )
        )
    if sync_status:
        statuses = (
            ("not_synced", "modified")
            if sync_status == "pending"
            else (sync_status,)
        )
        query = query.where(ConfigBinding.sync_status.in_(statuses))

    total = db_session.scalar(select(func.count()).select_from(query.subquery())) or 0
    page, size, pagination = _pagination(request, total, page, per_page)
    bindings = db_session.scalars(
        query.options(contains_eager(ConfigBinding.config))
        .order_by(Config.name.asc(), ConfigBinding.id.asc())
        .offset((page - 1) * size)
        .limit(size)
    ).unique().all()
    status_counts = {key: 0 for key in CONFIG_FILTER_STATUSES}
    for status, count in db_session.execute(
        select(ConfigBinding.sync_status, func.count(ConfigBinding.id))
        .where(ConfigBinding.node_id == node.id)
        .group_by(ConfigBinding.sync_status)
    ):
        if status in ("not_synced", "modified"):
            status_counts["pending"] += count
        elif status in status_counts:
            status_counts[status] += count
    status_counts["total"] = sum(status_counts.values())

    list_query = (
        _safe_configs_return(request.query_params.get("list_query", ""))
        or "/configs/"
    )
    current_query = urlencode(list(request.query_params.items()))
    current_url = request.url.path + ("?" + current_query if current_query else "")
    return _render(
        request,
        "configs/node_bindings.html",
        user,
        db_session,
        {
            "node": node,
            "bindings": bindings,
            "search": search,
            "sync_status": sync_status,
            "status_counts": status_counts,
            "status_labels": STATUS_LABELS,
            "pagination": pagination,
            "list_url": list_query,
            "current_url": current_url,
            "can_create": _can(user, request, db_session, "create"),
            "can_update": _can(user, request, db_session, "update"),
            "can_delete": _can(user, request, db_session, "delete"),
            "can_sync": _can(user, request, db_session, "sync"),
        },
    )


@router.get("/sync/", response_class=Response, summary="远程配置发现与同步向导")
def config_sync_wizard(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PAGE_SIZE),
    user: User = Depends(require_permission("configs", "read")),
    db_session: Session = Depends(get_session),
) -> Response:
    """筛选节点并显示远程配置发现和同步入口。"""
    search = request.query_params.get("search", "").strip()[:200]
    query = select(Node).where(
        Node.is_deleted.is_(False),
        Node.is_locked.is_(False),
        Node.nginx_available.is_(True),
    )
    if search:
        for term in (
            item.strip()
            for item in search.replace("，", ",").split(",")
            if item.strip()
        ):
            query = query.where(
                or_(
                    Node.hostname.contains(term, autoescape=True),
                    Node.ip.contains(term, autoescape=True),
                )
            )
    total = db_session.scalar(select(func.count()).select_from(query.subquery())) or 0
    page, size, pagination = _pagination(request, total, page, per_page)
    nodes = db_session.scalars(
        query.options(joinedload(Node.credential), joinedload(Node.sync_setting))
        .order_by(Node.hostname.asc(), Node.id.asc())
        .offset((page - 1) * size)
        .limit(size)
    ).unique().all()
    node_ids = [node.id for node in nodes]
    binding_stats = {}
    last_sync = {}
    if node_ids:
        for node_id, sync_status, count in db_session.execute(
            select(
                ConfigBinding.node_id,
                ConfigBinding.sync_status,
                func.count(ConfigBinding.id),
            )
            .where(ConfigBinding.node_id.in_(node_ids))
            .group_by(ConfigBinding.node_id, ConfigBinding.sync_status)
        ):
            binding_stats.setdefault(node_id, {})[sync_status] = count
        last_sync = dict(
            db_session.execute(
                select(
                    ConfigBinding.node_id,
                    func.max(ConfigBinding.last_sync_time),
                )
                .where(ConfigBinding.node_id.in_(node_ids))
                .group_by(ConfigBinding.node_id)
            ).all()
        )
    node_rows = []
    for node in nodes:
        counts = binding_stats.get(node.id, {})
        node_rows.append(
            {
                "node": node,
                "main_conf_path": (
                    node.sync_setting.main_conf_path
                    if node.sync_setting is not None and node.sync_setting.main_conf_path
                    else read_setting(
                        db_session,
                        "config.default_nginx_path",
                        DEFAULT_MAIN_CONF_PATH,
                    )
                ),
                "binding_count": sum(counts.values()),
                "synced_count": counts.get("synced", 0),
                "pending_count": counts.get("not_synced", 0)
                + counts.get("modified", 0),
                "failed_count": counts.get("failed", 0),
                "orphaned_count": counts.get("orphaned", 0),
                "last_sync": last_sync.get(node.id),
                "eligible": (
                    node.status == "online"
                    and node.nginx_available is True
                    and node.credential is not None
                    and node.credential.is_enabled
                ),
            }
        )
    preselect = request.query_params.get("node_id", "").strip()
    preselect_node_id = int(preselect) if preselect.isdigit() else None
    return _render(
        request,
        "configs/sync_wizard.html",
        user,
        db_session,
        {
            "nodes": node_rows,
            "search": search,
            "preselect_node_id": preselect_node_id,
            "can_sync": _can(user, request, db_session, "sync"),
            "batch_max_count": read_setting(db_session, "node.batch_max_count", 3),
            "pagination": pagination,
        },
    )


@router.get("/create/", response_class=Response, summary="新增配置标签")
def create_config_page(
    request: Request,
    user: User = Depends(require_permission("configs", "create")),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示配置标签新增表单。"""
    node = None
    errors = {}
    selected_node_ids = set()
    eligible_nodes = _eligible_nodes(db_session)
    eligible_node_ids = {item.id for item in eligible_nodes}
    raw_node_id = request.query_params.get("node_id", "").strip()
    if raw_node_id:
        if not raw_node_id.isdigit():
            raise HTTPException(status_code=404, detail="节点不存在")
        node = db_session.get(Node, int(raw_node_id))
        if node is None or node.is_deleted:
            raise HTTPException(status_code=404, detail="节点不存在")
        if node.id in eligible_node_ids:
            selected_node_ids.add(node.id)
        else:
            errors["nodes"] = _node_gate_message(node) or "该节点当前不满足绑定条件"
            node = None
    return_to = _safe_configs_return(request.query_params.get("return_to", ""))
    return _render(
        request,
        "configs/form.html",
        user,
        db_session,
        _config_form_context(
            {
                "name": "",
                "default_remote_path": "",
                "template_content": "",
                "description": "",
            },
            errors=errors,
            node=node,
            eligible_nodes=eligible_nodes,
            selected_node_ids=selected_node_ids,
            return_to=return_to or "/configs/",
        ),
    )


@router.post("/create/", response_class=Response, summary="保存配置标签")
def create_config(
    request: Request,
    name: str = Form(""),
    default_remote_path: str = Form(""),
    template_content: str = Form(""),
    description: str = Form(""),
    node_ids: Optional[List[int]] = Form(None),
    return_to: str = Form(""),
    user: User = Depends(require_permission("configs", "create")),
    db_session: Session = Depends(get_session),
) -> Response:
    """创建配置标签并为所选节点初始化绑定和版本。"""
    values = {
        "name": name.strip(),
        "default_remote_path": default_remote_path.strip(),
        "template_content": template_content,
        "description": description,
    }
    errors = {}
    if not values["name"]:
        errors["name"] = "请输入配置名称"
    elif len(values["name"]) > 255:
        errors["name"] = "配置名称不能超过 255 个字符"
    if len(values["default_remote_path"]) > 500:
        errors["default_remote_path"] = "默认远程路径不能超过 500 个字符"

    eligible_nodes = _eligible_nodes(db_session)
    eligible_by_id = {item.id: item for item in eligible_nodes}
    selected_node_ids = set(node_ids or [])
    raw_node_id = request.query_params.get("node_id", "").strip()
    if raw_node_id:
        if not raw_node_id.isdigit():
            raise HTTPException(status_code=404, detail="节点不存在")
        preselected_node = db_session.get(Node, int(raw_node_id))
        if preselected_node is None or preselected_node.is_deleted:
            raise HTTPException(status_code=404, detail="节点不存在")
        if preselected_node.id in eligible_by_id:
            selected_node_ids.add(preselected_node.id)
        else:
            errors["nodes"] = _node_gate_message(preselected_node) or "节点当前不可绑定配置"
    if selected_node_ids - set(eligible_by_id):
        errors["nodes"] = "所选节点已不满足绑定条件，请刷新后重新选择"
    if selected_node_ids and not values["default_remote_path"]:
        errors["default_remote_path"] = "选择目标节点时请输入远程文件路径"
    if errors:
        return _render(
            request,
            "configs/form.html",
            user,
            db_session,
            _config_form_context(
                values,
                errors,
                eligible_nodes=eligible_nodes,
                selected_node_ids=selected_node_ids,
                return_to=_safe_configs_return(return_to) or "/configs/",
            ),
            status_code=400,
        )

    config = Config(
        name=values["name"],
        default_remote_path=values["default_remote_path"],
        template_content=values["template_content"],
        description=values["description"],
        source="manual",
        created_by=user.id,
    )
    db_session.add(config)
    db_session.flush()
    for node_id in sorted(selected_node_ids):
        node = eligible_by_id[node_id]
        binding = ConfigBinding(
            config_id=config.id,
            node_id=node.id,
            remote_path=config.default_remote_path,
            content=config.template_content,
            current_version=1,
            sync_status="not_synced",
            source="manual",
            created_by=user.id,
        )
        db_session.add(binding)
        db_session.add(
            BindingVersion(
                binding=binding,
                version=1,
                content=binding.content,
                remark="手动创建配置并绑定",
                created_by=user.id,
            )
        )
    db_session.commit()
    target = _safe_configs_return(return_to) or "/configs/"
    message = "配置标签 {} 已创建".format(config.name)
    if selected_node_ids:
        message += "并绑定到 {} 个节点".format(len(selected_node_ids))
    return _notice_redirect(request, target, message)


@router.get("/{config_id}/edit/", response_class=Response, summary="编辑配置标签")
def edit_config_page(
    request: Request,
    config_id: int,
    user: User = Depends(require_permission("configs", "update")),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示配置标签编辑表单。"""
    config = db_session.get(Config, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="配置标签不存在")
    return _render(
        request,
        "configs/form.html",
        user,
        db_session,
        _config_form_context(
            {
                "name": config.name,
                "default_remote_path": config.default_remote_path,
                "template_content": config.template_content,
                "description": config.description,
            },
            config=config,
        ),
    )


@router.post("/{config_id}/edit/", response_class=Response, summary="保存配置标签")
def edit_config(
    request: Request,
    config_id: int,
    name: str = Form(""),
    default_remote_path: str = Form(""),
    template_content: str = Form(""),
    description: str = Form(""),
    user: User = Depends(require_permission("configs", "update")),
    db_session: Session = Depends(get_session),
) -> Response:
    """更新配置标签元数据，不改动已有绑定内容或版本。"""
    config = db_session.get(Config, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="配置标签不存在")
    values = {
        "name": name.strip(),
        "default_remote_path": default_remote_path.strip(),
        "template_content": template_content,
        "description": description,
    }
    errors = {}
    if not values["name"]:
        errors["name"] = "请输入配置名称"
    elif len(values["name"]) > 255:
        errors["name"] = "配置名称不能超过 255 个字符"
    if len(values["default_remote_path"]) > 500:
        errors["default_remote_path"] = "默认远程路径不能超过 500 个字符"
    if errors:
        return _render(
            request,
            "configs/form.html",
            user,
            db_session,
            _config_form_context(values, errors, config=config),
            status_code=400,
        )
    config.name = values["name"]
    config.default_remote_path = values["default_remote_path"]
    config.template_content = values["template_content"]
    config.description = values["description"]
    db_session.commit()
    return _notice_redirect(request, "/configs/", "配置标签 {} 更新成功".format(config.name))


@router.get("/{config_id}/delete/", response_class=Response, summary="删除配置标签")
def delete_config_page(
    request: Request,
    config_id: int,
    user: User = Depends(require_permission("configs", "delete")),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示删除配置标签及其绑定的确认页。"""
    config = db_session.get(Config, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="配置标签不存在")
    bindings = db_session.scalars(
        select(ConfigBinding)
        .options(joinedload(ConfigBinding.node))
        .where(ConfigBinding.config_id == config_id)
        .order_by(ConfigBinding.id.asc())
    ).all()
    return _render(
        request,
        "configs/delete.html",
        user,
        db_session,
        {"config": config, "bindings": bindings},
    )


@router.post("/{config_id}/delete/", summary="确认删除配置标签")
def delete_config(
    request: Request,
    config_id: int,
    return_to: str = Form(""),
    user: User = Depends(require_permission("configs", "delete")),
    db_session: Session = Depends(get_session),
) -> Response:
    """物理删除配置标签并级联清理其绑定和版本。"""
    config = db_session.get(Config, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="配置标签不存在")
    name = config.name
    db_session.delete(config)
    db_session.commit()
    target = _safe_configs_return(return_to) or "/configs/"
    return _notice_redirect(
        request, target, "配置标签 {} 及其绑定已删除".format(name)
    )


@router.get("/{config_id}/", response_class=Response, summary="配置标签详情")
def config_detail(
    request: Request,
    config_id: int,
    user: User = Depends(require_permission("configs", "read")),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示配置标签元数据和活跃节点上的绑定。"""
    config = db_session.get(Config, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="配置标签不存在")
    bindings = db_session.scalars(
        select(ConfigBinding)
        .join(Node, ConfigBinding.node_id == Node.id)
        .options(joinedload(ConfigBinding.node))
        .where(ConfigBinding.config_id == config_id, Node.is_deleted.is_(False))
        .order_by(Node.hostname.asc())
    ).all()
    return _render(
        request,
        "configs/detail.html",
        user,
        db_session,
        {
            "config": config,
            "bindings": bindings,
            "source_labels": SOURCE_LABELS,
            "status_labels": STATUS_LABELS,
            "can_create": _can(user, request, db_session, "create"),
            "can_update": _can(user, request, db_session, "update"),
            "can_delete": _can(user, request, db_session, "delete"),
        },
    )


@router.get("/bindings/create/", response_class=Response, summary="创建节点绑定")
def create_binding_page(
    request: Request,
    config_id: Optional[int] = Query(None, ge=1),
    user: User = Depends(require_permission("configs", "create")),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示绑定表单并预填标签默认路径和内容模板。"""
    values = {"config_id": "", "remote_path": "", "content": ""}
    if config_id is not None:
        config = db_session.get(Config, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="配置标签不存在")
        values["config_id"] = str(config.id)
        values["remote_path"] = config.default_remote_path
        content = config.template_content
        if not content:
            last_binding = db_session.scalar(
                select(ConfigBinding)
                .where(ConfigBinding.config_id == config.id)
                .order_by(ConfigBinding.updated_at.desc())
                .limit(1)
            )
            content = last_binding.content if last_binding else ""
        values["content"] = content
    return _render(
        request,
        "configs/binding_form.html",
        user,
        db_session,
        _binding_form_context(db_session, values),
    )


@router.post("/bindings/create/", response_class=Response, summary="保存节点绑定")
def create_bindings(
    request: Request,
    config_id: str = Form(""),
    node_ids: Optional[List[str]] = Form(None),
    remote_path: str = Form(""),
    content: str = Form(""),
    return_to: str = Form(""),
    user: User = Depends(require_permission("configs", "create")),
    db_session: Session = Depends(get_session),
) -> Response:
    """全量校验所选节点后一次性创建绑定及各自的 v1 快照。"""
    values = {
        "config_id": config_id.strip(),
        "remote_path": remote_path.strip(),
        "content": content,
    }
    errors = {}
    config = None
    if not values["config_id"].isdigit() or int(values["config_id"]) < 1:
        errors["config_id"] = "请选择配置标签"
    else:
        config = db_session.get(Config, int(values["config_id"]))
        if config is None:
            errors["config_id"] = "配置标签不存在"
    if not values["remote_path"]:
        errors["remote_path"] = "请输入远程文件路径"
    elif len(values["remote_path"]) > 500:
        errors["remote_path"] = "远程文件路径不能超过 500 个字符"
    parsed_ids = []
    try:
        parsed_ids = list(
            dict.fromkeys(int(value) for value in (node_ids or []) if value)
        )
        if any(value < 1 for value in parsed_ids):
            raise ValueError
    except (TypeError, ValueError):
        errors["node_ids"] = "节点选择无效，请重新选择"
    selected_ids = set(parsed_ids)
    nodes = []
    if not errors.get("node_ids") and parsed_ids:
        nodes = db_session.scalars(
            select(Node)
            .where(Node.id.in_(parsed_ids))
            .order_by(Node.hostname.asc())
        ).all()
        if len(nodes) != len(parsed_ids):
            errors["node_ids"] = "部分节点不存在，请重新选择"
        else:
            for node in nodes:
                gate_message = _node_gate_message(node)
                if gate_message:
                    errors["node_ids"] = gate_message
                    break
    elif not errors.get("node_ids"):
        errors["node_ids"] = "请至少选择一个目标节点"

    if config is not None and nodes and not errors.get("node_ids"):
        duplicate = db_session.scalar(
            select(ConfigBinding.id).where(
                ConfigBinding.config_id == config.id,
                ConfigBinding.node_id.in_([node.id for node in nodes]),
            ).limit(1)
        )
        if duplicate is not None:
            errors["node_ids"] = "所选节点中已有该配置绑定，请从标签详情中管理现有绑定"
    if errors:
        return _render(
            request,
            "configs/binding_form.html",
            user,
            db_session,
            _binding_form_context(
                db_session,
                values,
                errors,
                selected_node_ids=selected_ids,
            ),
            status_code=400,
        )

    bindings = []
    for node in nodes:
        binding = ConfigBinding(
            config_id=config.id,
            node_id=node.id,
            remote_path=values["remote_path"],
            content=content,
            current_version=1,
            sync_status="not_synced",
            source="manual",
            created_by=user.id,
        )
        db_session.add(binding)
        bindings.append(binding)
    try:
        db_session.flush()
        db_session.add_all(
            [
                BindingVersion(
                    binding_id=binding.id,
                    version=1,
                    content=binding.content,
                    remark="手动创建绑定",
                    created_by=user.id,
                )
                for binding in bindings
            ]
        )
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
        return _render(
            request,
            "configs/binding_form.html",
            user,
            db_session,
            _binding_form_context(
                db_session,
                values,
                {"node_ids": "绑定状态已变化，请刷新页面后重试"},
                selected_node_ids=selected_ids,
            ),
            status_code=409,
        )
    target = _safe_configs_return(return_to) or "/configs/{}/".format(config.id)
    return _notice_redirect(
        request,
        target,
        "已为配置 {} 创建 {} 个节点绑定".format(config.name, len(bindings)),
    )


@router.get("/bindings/{binding_id}/", response_class=Response, summary="节点绑定详情")
def binding_detail(
    request: Request,
    binding_id: int,
    user: User = Depends(require_permission("configs", "read")),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示绑定路径、当前内容、节点状态和可用操作。"""
    binding = _load_binding(db_session, binding_id)
    return _render(
        request,
        "configs/binding_detail.html",
        user,
        db_session,
        {
            "binding": binding,
            "status_labels": STATUS_LABELS,
            "source_labels": SOURCE_LABELS,
            "can_update": _can(user, request, db_session, "update"),
            "can_delete": _can(user, request, db_session, "delete"),
        },
    )


@router.get("/bindings/{binding_id}/edit/", response_class=Response, summary="编辑节点绑定")
def edit_binding_page(
    request: Request,
    binding_id: int,
    user: User = Depends(require_permission("configs", "update")),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示绑定路径和内容编辑表单。"""
    binding = _load_binding(db_session, binding_id)
    if binding.sync_status == "marked_deleted":
        return _notice_redirect(
            request,
            "/configs/{}/".format(binding.config_id),
            "已标记删除的绑定无法编辑",
            "error",
        )
    gate_message = _node_gate_message(binding.node)
    if gate_message:
        return _notice_redirect(
            request,
            "/configs/{}/".format(binding.config_id),
            gate_message,
            "error",
        )
    values = {
        "remote_path": binding.remote_path,
        "content": binding.content,
        "remark": "",
    }
    context = _binding_form_context(db_session, values, binding=binding)
    context["next_version"] = binding.current_version + 1
    return _render(request, "configs/binding_form.html", user, db_session, context)


@router.post(
    "/bindings/{binding_id}/edit/",
    response_class=Response,
    summary="审阅节点绑定修改",
)
def edit_binding(
    request: Request,
    binding_id: int,
    remote_path: str = Form(""),
    content: str = Form(""),
    remark: str = Form(""),
    expected_version: int = Form(1),
    confirm_save: str = Form(""),
    user: User = Depends(require_permission("configs", "update")),
    db_session: Session = Depends(get_session),
) -> Response:
    """先显示路径和内容差异，再通过确认请求保存新版本。"""
    binding = _load_binding(db_session, binding_id)
    if binding.sync_status == "marked_deleted":
        return _notice_redirect(
            request,
            "/configs/{}/".format(binding.config_id),
            "已标记删除的绑定无法编辑",
            "error",
        )
    gate_message = _node_gate_message(binding.node)
    if gate_message:
        return _notice_redirect(
            request,
            "/configs/{}/".format(binding.config_id),
            gate_message,
            "error",
        )
    if expected_version != binding.current_version:
        return _notice_redirect(
            request,
            "/configs/bindings/{}/edit/".format(binding.id),
            "绑定版本已被其他操作更新，请检查后重试",
            "error",
        )
    clean_path = remote_path.strip()
    errors = {}
    if not clean_path:
        errors["remote_path"] = "请输入远程文件路径"
    elif len(clean_path) > 500:
        errors["remote_path"] = "远程文件路径不能超过 500 个字符"
    if len(remark) > 4000:
        errors["remark"] = "备注不能超过 4000 个字符"
    if errors:
        context = _binding_form_context(
            db_session,
            {"remote_path": remote_path, "content": content, "remark": remark},
            errors,
            binding=binding,
        )
        context["next_version"] = binding.current_version + 1
        return _render(
            request,
            "configs/binding_form.html",
            user,
            db_session,
            context,
            400,
        )
    if confirm_save != "yes":
        return _render(
            request,
            "configs/binding_review.html",
            user,
            db_session,
            {
                "binding": binding,
                "remote_path": clean_path,
                "content": content,
                "remark": remark,
                "expected_version": expected_version,
                "next_version": binding.current_version + 1,
                "diff_rows": build_split_diff_rows(binding.content, content),
            },
        )
    new_version = save_binding_revision(
        db_session,
        binding.id,
        expected_version,
        clean_path,
        content,
        remark,
        user.id,
    )
    if new_version is None:
        return _notice_redirect(
            request,
            "/configs/bindings/{}/edit/".format(binding.id),
            "绑定版本已发生变化，请重新审阅差异",
            "error",
        )
    return _notice_redirect(
        request,
        "/configs/bindings/{}/".format(binding.id),
        "绑定保存成功，已生成 V{}，状态为本地已修改".format(new_version),
    )


@router.get("/bindings/{binding_id}/delete/", response_class=Response, summary="解除节点绑定")
def delete_binding_page(
    request: Request,
    binding_id: int,
    user: User = Depends(require_permission("configs", "delete")),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示绑定解除策略和确认信息。"""
    binding = _load_binding(db_session, binding_id)
    return _render(
        request,
        "configs/binding_delete.html",
        user,
        db_session,
        {"binding": binding, "status_labels": STATUS_LABELS},
    )


@router.post("/bindings/{binding_id}/delete/", summary="确认解除节点绑定")
def delete_binding(
    request: Request,
    binding_id: int,
    return_to: str = Form(""),
    user: User = Depends(require_permission("configs", "delete")),
    db_session: Session = Depends(get_session),
) -> Response:
    """按参考状态机物理删除绑定或标记为待远程清理。"""
    binding = _load_binding(db_session, binding_id)
    label = "{} @ {}".format(binding.config.name, binding.node.hostname)
    config_id = binding.config_id
    nginx_available = binding.node.nginx_available
    result = remove_binding(db_session, binding)
    if result == "marked_deleted":
        message = "绑定 {} 已标记删除，下次同步时将清理远程文件".format(label)
    elif nginx_available is not True:
        message = "绑定 {} 已删除（节点无可用 Nginx，未清理远程）".format(label)
    else:
        message = "绑定 {} 已删除".format(label)
    target = _safe_configs_return(return_to) or "/configs/{}/".format(config_id)
    return _notice_redirect(request, target, message)


@router.post("/bindings/{binding_id}/restore/", summary="恢复已标记删除的绑定")
def restore_marked_binding_page(
    request: Request,
    binding_id: int,
    return_to: str = Form(""),
    user: User = Depends(require_permission("configs", "update")),
    db_session: Session = Depends(get_session),
) -> Response:
    """在节点门禁允许时撤销绑定的待删除状态。"""
    binding = _load_binding(db_session, binding_id)
    target = _safe_configs_return(return_to) or "/configs/{}/".format(binding.config_id)
    if binding.sync_status != "marked_deleted":
        return _notice_redirect(request, target, "该绑定未处于标记删除状态", "error")
    gate_message = _node_gate_message(binding.node)
    if gate_message:
        return _notice_redirect(request, target, gate_message, "error")
    if not restore_marked_binding(db_session, binding):
        return _notice_redirect(request, target, "绑定状态已发生变化，请刷新后重试", "error")
    return _notice_redirect(request, target, "绑定已恢复为未同步状态")


@router.get(
    "/bindings/{binding_id}/versions/",
    response_class=Response,
    summary="绑定版本历史",
)
def binding_versions(
    request: Request,
    binding_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PAGE_SIZE),
    user: User = Depends(require_permission("configs", "read")),
    db_session: Session = Depends(get_session),
) -> Response:
    """分页显示绑定版本、修改人、备注和内容大小。"""
    binding = _load_binding(db_session, binding_id)
    total = db_session.scalar(
        select(func.count(BindingVersion.id)).where(
            BindingVersion.binding_id == binding.id
        )
    ) or 0
    page, size, pagination = _pagination(request, total, page, per_page)
    versions = db_session.scalars(
        select(BindingVersion)
        .options(joinedload(BindingVersion.creator))
        .where(BindingVersion.binding_id == binding.id)
        .order_by(BindingVersion.version.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return _render(
        request,
        "configs/versions.html",
        user,
        db_session,
        {
            "binding": binding,
            "versions": versions,
            "pagination": pagination,
            "next_version": binding.current_version + 1,
            "can_update": _can(user, request, db_session, "update"),
            "can_restore": (
                binding.sync_status != "marked_deleted"
                and _node_gate_message(binding.node) is None
            ),
        },
    )


@router.get(
    "/bindings/{binding_id}/versions/{version_id}/",
    response_class=Response,
    summary="绑定版本详情",
)
def binding_version_detail(
    request: Request,
    binding_id: int,
    version_id: int,
    user: User = Depends(require_permission("configs", "read")),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示绑定某一历史版本的完整配置正文。"""
    binding = _load_binding(db_session, binding_id)
    version = db_session.scalar(
        select(BindingVersion)
        .options(joinedload(BindingVersion.creator))
        .where(BindingVersion.id == version_id, BindingVersion.binding_id == binding.id)
    )
    if version is None:
        raise HTTPException(status_code=404, detail="绑定版本不存在")
    return _render(
        request,
        "configs/version_detail.html",
        user,
        db_session,
        {
            "binding": binding,
            "version": version,
            "is_current": version.version == binding.current_version,
            "can_restore": (
                _can(user, request, db_session, "update")
                and binding.sync_status != "marked_deleted"
                and _node_gate_message(binding.node) is None
                and version.version != binding.current_version
            ),
            "next_version": binding.current_version + 1,
        },
    )


@router.post("/bindings/{binding_id}/versions/{version_id}/restore/", summary="恢复历史版本")
def restore_binding_version(
    request: Request,
    binding_id: int,
    version_id: int,
    user: User = Depends(require_permission("configs", "update")),
    db_session: Session = Depends(get_session),
) -> Response:
    """从指定历史快照生成新版本并设置为待发布的本地修改。"""
    binding = _load_binding(db_session, binding_id)
    target = "/configs/bindings/{}/versions/".format(binding.id)
    if binding.sync_status == "marked_deleted":
        return _notice_redirect(request, target, "已标记删除的绑定无法恢复版本", "error")
    gate_message = _node_gate_message(binding.node)
    if gate_message:
        return _notice_redirect(request, target, gate_message, "error")
    version = db_session.scalar(
        select(BindingVersion).where(
            BindingVersion.id == version_id,
            BindingVersion.binding_id == binding.id,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="绑定版本不存在")
    old_version = version.version
    new_version = restore_binding_revision(db_session, binding, version, user.id)
    if new_version is None:
        return _notice_redirect(request, target, "绑定版本已发生变化，请刷新后重试", "error")
    return _notice_redirect(
        request,
        target,
        "已恢复到 V{} 并生成新版本 V{}；需再次发布才与远程一致".format(old_version, new_version),
    )


@router.get(
    "/bindings/{binding_id}/compare/",
    response_class=Response,
    summary="对比绑定版本",
)
def compare_binding_versions(
    request: Request,
    binding_id: int,
    base_version: Optional[int] = Query(None, ge=1),
    target_version: Optional[int] = Query(None, ge=1),
    user: User = Depends(require_permission("configs", "read")),
    db_session: Session = Depends(get_session),
) -> Response:
    """显示所选两个历史版本的逐行左右差异。"""
    binding = _load_binding(db_session, binding_id)
    versions = db_session.scalars(
        select(BindingVersion)
        .where(BindingVersion.binding_id == binding.id)
        .order_by(BindingVersion.version.desc())
    ).all()
    version_map = {version.id: version for version in versions}
    base_obj = version_map.get(base_version) if base_version is not None else None
    target_obj = version_map.get(target_version) if target_version is not None else None
    if base_version is not None and base_obj is None:
        raise HTTPException(status_code=404, detail="基准版本不存在")
    if target_version is not None and target_obj is None:
        raise HTTPException(status_code=404, detail="目标版本不存在")
    diff_rows = []
    has_diff = False
    if base_obj is not None and target_obj is not None and base_obj.id != target_obj.id:
        diff_rows = build_split_diff_rows(base_obj.content, target_obj.content)
        has_diff = base_obj.content != target_obj.content
    return _render(
        request,
        "configs/compare.html",
        user,
        db_session,
        {
            "binding": binding,
            "versions": versions,
            "base_obj": base_obj,
            "target_obj": target_obj,
            "selected_base": base_version,
            "selected_target": target_version,
            "diff_rows": diff_rows,
            "has_diff": has_diff,
        },
    )
