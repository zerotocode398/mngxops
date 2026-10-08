"""提供节点筛选、恢复、导入校验和 Excel 流程。"""

import ipaddress
import io
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from ngxops.credentials.models import Credential
from ngxops.nodes.models import Node, NodeGroup, NodeSyncSetting


IMPORT_HEADERS = (
    "主机名",
    "IP",
    "SSH端口",
    "所属环境",
    "Nginx路径",
    "Nginx主配置路径",
    "节点组",
    "凭证",
    "备注",
)
_EMPTY_MARKERS = frozenset(("", "-", "—", "－", "无", "n/a", "na", "none"))
_ENVIRONMENTS = {
    "dev": "dev",
    "development": "dev",
    "开发": "dev",
    "开发环境": "dev",
    "test": "test",
    "testing": "test",
    "测试": "test",
    "测试环境": "test",
    "prod": "prod",
    "production": "prod",
    "生产": "prod",
    "生产环境": "prod",
}


def split_terms(value: str) -> List[str]:
    """按中英文逗号拆分搜索词并去重保序。"""
    terms = []
    for part in re.split(r"[,，]", (value or "").strip()):
        term = part.strip()
        if term and term not in terms:
            terms.append(term[:200])
    return terms


def node_query(
    session: Session,
    *,
    search: str = "",
    group_id: Optional[int] = None,
    environment: str = "",
    status: str = "",
    include_deleted: bool = False,
):
    """构造与节点列表和导出共用的筛选查询。"""
    query = select(Node).options(
        joinedload(Node.credential),
        selectinload(Node.groups),
        joinedload(Node.sync_setting),
    )
    if not include_deleted:
        query = query.where(Node.is_deleted.is_(False))
    for term in split_terms(search):
        query = query.where(
            or_(Node.hostname.ilike("%{}%".format(term)), Node.ip.ilike("%{}%".format(term)))
        )
    if group_id:
        query = query.where(Node.groups.any(NodeGroup.id == group_id))
    if environment in ("dev", "test", "prod"):
        query = query.where(Node.environment == environment)
    if status in ("online", "offline", "unknown"):
        query = query.where(Node.status == status)
    return query.order_by(Node.created_at.desc(), Node.id.desc())


def normalize_ip(value: str) -> str:
    """校验并规范化 IPv4 或 IPv6 地址。"""
    return str(ipaddress.ip_address((value or "").strip()))


def create_or_restore_node(
    session: Session,
    user_id: int,
    *,
    hostname: str,
    ip: str,
    port: int,
    credential_id: Optional[int],
    group_ids: Sequence[int],
    environment: str,
    nginx_path: str,
    main_conf_path: str,
    description: str,
) -> Tuple[Node, bool]:
    """创建节点或恢复同 IP 的已删除行并覆盖其资产字段。"""
    normalized_ip = normalize_ip(ip)
    node = session.scalar(select(Node).where(Node.ip == normalized_ip))
    restored = bool(node and node.is_deleted)
    if node is None:
        node = Node(hostname=hostname, ip=normalized_ip, created_by=user_id)
        session.add(node)
        session.flush()
    elif not restored:
        raise ValueError("该 IP 地址已被其他活跃节点使用")

    node.hostname = hostname
    node.ip = normalized_ip
    node.port = port
    node.credential_id = credential_id
    node.environment = environment
    node.nginx_path = nginx_path or "/usr/sbin/nginx"
    node.description = description
    node.status = "unknown"
    node.is_deleted = False
    node.deleted_at = None
    node.deleted_by = None
    node.groups = list(
        session.scalars(select(NodeGroup).where(NodeGroup.id.in_(set(group_ids)))).all()
    ) if group_ids else []
    if node.sync_setting is None:
        node.sync_setting = NodeSyncSetting(main_conf_path=main_conf_path or "/etc/nginx/nginx.conf")
    else:
        node.sync_setting.main_conf_path = main_conf_path or "/etc/nginx/nginx.conf"
    return node, restored


def _cell_text(value: Any) -> str:
    """把 Excel 单元格值转换为去空白字符串。"""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _empty_optional(value: str) -> bool:
    """识别批量导入表格里的空值占位符。"""
    return value.strip().lower() in _EMPTY_MARKERS


def _split_group_names(value: str) -> List[str]:
    """按常见中英文分隔符拆分并去重节点组名称。"""
    names = []
    for part in re.split(r"[,，、;；]", value or ""):
        name = part.strip()
        if not _empty_optional(name) and name not in names:
            names.append(name)
    return names


def _parse_import_port(value: str) -> Optional[int]:
    """解析导入的 SSH 端口并限制在 TCP 端口范围内。"""
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _format_import_error_rows(rows: Sequence[int]) -> str:
    """把排序后的导入行号压缩成连续范围。"""
    parts = []
    start = rows[0]
    end = rows[0]
    for row_number in rows[1:]:
        if row_number == end + 1:
            end = row_number
            continue
        parts.append(
            "第 {}{} 行".format(
                start,
                "-{}".format(end) if end > start else "",
            )
        )
        start = row_number
        end = row_number
    parts.append(
        "第 {}{} 行".format(
            start,
            "-{}".format(end) if end > start else "",
        )
    )
    return "、".join(parts)


def _merge_import_errors(errors: Sequence[dict]) -> List[dict]:
    """合并相同错误原因并保留其对应的 Excel 行号。"""
    grouped_rows = {}
    message_order = []
    for error in errors:
        message = error.get("message", "").strip()
        if not message:
            continue
        if message not in grouped_rows:
            grouped_rows[message] = []
            message_order.append(message)
        row_number = error.get("row", 0)
        if row_number not in grouped_rows[message]:
            grouped_rows[message].append(row_number)

    merged = []
    for message in message_order:
        rows = sorted(grouped_rows[message])
        if len(rows) == 1 and rows[0] > 0:
            merged.append({"row": rows[0], "message": message})
        elif len(rows) > 1 and rows[0] > 0:
            merged.append(
                {
                    "row": 0,
                    "merged": True,
                    "row_range": _format_import_error_rows(rows),
                    "message": message,
                }
            )
        else:
            merged.append({"row": 0, "message": message})
    return merged


def parse_node_workbook(content: bytes) -> Tuple[List[Dict[str, str]], List[dict]]:
    """检查上传工作簿表头并提取节点数据行。"""
    if len(content) > 8 * 1024 * 1024:
        return [], [{"row": 0, "message": "文件不能超过 8 MiB"}]
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        return [], [{"row": 0, "message": "无法读取 Excel 文件"}]
    try:
        sheet = workbook.active
        headers = tuple(_cell_text(cell.value) for cell in sheet[1][: len(IMPORT_HEADERS)])
        if headers != IMPORT_HEADERS:
            return [], [{"row": 1, "message": "表头不匹配，请使用节点导入模板"}]
        rows = []
        for row_number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            items = list(values[: len(IMPORT_HEADERS)])
            items.extend([None] * (len(IMPORT_HEADERS) - len(items)))
            cells = [_cell_text(value) for value in items]
            if not any(cells):
                continue
            rows.append({"row": row_number, **dict(zip(IMPORT_HEADERS, cells))})
        if not rows:
            return [], [{"row": 0, "message": "没有可导入的数据行"}]
        return rows, []
    finally:
        workbook.close()


def validate_node_import_rows(
    session: Session,
    rows: Sequence[Dict[str, str]],
    user_id: int,
    default_port: int = 22,
    default_nginx_path: str = "/usr/sbin/nginx",
    default_main_conf_path: str = "/etc/nginx/nginx.conf",
) -> Tuple[List[dict], List[dict]]:
    """全量校验导入行及其 IP、节点组和启用凭证引用。"""
    cleaned = []
    errors = []
    seen_ips = {}
    normalized_ips = {}
    all_group_names = set()
    all_credential_names = set()
    for row in rows:
        try:
            normalized_ips[row["row"]] = normalize_ip(row.get("IP", ""))
        except ValueError:
            normalized_ips[row["row"]] = ""
        all_group_names.update(_split_group_names(row.get("节点组", "")))
        credential_name = row.get("凭证", "").strip()
        if not _empty_optional(credential_name):
            all_credential_names.add(credential_name)

    existing_nodes = {}
    if normalized_ips:
        existing_nodes = {
            node.ip: node
            for node in session.scalars(
                select(Node).where(Node.ip.in_([ip for ip in normalized_ips.values() if ip]))
            ).all()
        }
    groups_by_name = {
        group.name: group
        for group in session.scalars(
            select(NodeGroup).where(NodeGroup.name.in_(all_group_names))
        ).all()
    } if all_group_names else {}
    credentials_by_name = {}
    if all_credential_names:
        for credential in session.scalars(
            select(Credential)
            .where(Credential.name.in_(all_credential_names), Credential.is_enabled.is_(True))
            .order_by(Credential.id.asc())
        ).all():
            credentials_by_name.setdefault(credential.name, []).append(credential)

    for row in rows:
        row_number = row["row"]
        hostname = row.get("主机名", "").strip()
        raw_port = row.get("SSH端口", "")
        port = default_port if _empty_optional(raw_port) else _parse_import_port(raw_port)
        environment_raw = row.get("所属环境", "").strip()
        environment = _ENVIRONMENTS.get(environment_raw.lower()) or _ENVIRONMENTS.get(environment_raw)
        messages = []
        if not hostname:
            messages.append("主机名不能为空")
        elif len(hostname) > 100:
            messages.append("主机名不能超过 100 个字符")
        ip = normalized_ips[row_number]
        if not ip:
            ip = ""
            messages.append("IP 地址格式不合法")
        if ip:
            if ip in seen_ips:
                messages.append(
                    "IP 地址「{}」与第 {} 行重复".format(ip, seen_ips[ip])
                )
            seen_ips[ip] = row_number
            existing = existing_nodes.get(ip)
            if existing is not None and not existing.is_deleted:
                messages.append("IP 地址「{}」已被活跃节点占用".format(ip))
        if port is None:
            messages.append("SSH端口必须是 1 到 65535 的整数")
        if not environment:
            environment = "test" if _empty_optional(environment_raw) else None
            if environment is None:
                messages.append("所属环境请填写开发、测试、生产或 dev/test/prod")

        group_names = _split_group_names(row.get("节点组", ""))
        if len(group_names) > 3:
            messages.append("节点最多只能关联 3 个节点组")
        for name in group_names:
            if name not in groups_by_name:
                messages.append("节点组「{}」不存在".format(name))

        credential_id = None
        credential_name = row.get("凭证", "").strip()
        if not _empty_optional(credential_name):
            matches = credentials_by_name.get(credential_name, [])
            own = [item for item in matches if item.created_by == user_id]
            chosen = own[0] if own else matches[0] if len(matches) == 1 else None
            if chosen is None:
                messages.append(
                    "凭证「{}」不存在、未启用或名称匹配不唯一".format(credential_name)
                )
            else:
                credential_id = chosen.id

        if messages:
            errors.extend(
                {"row": row_number, "message": message}
                for message in messages
            )
            continue
        cleaned.append(
            {
                "hostname": hostname,
                "ip": ip,
                "port": port,
                "environment": environment,
                "nginx_path": row.get("Nginx路径", "").strip() or default_nginx_path,
                "main_conf_path": row.get("Nginx主配置路径", "").strip() or default_main_conf_path,
                "group_ids": [groups_by_name[name].id for name in group_names],
                "credential_id": credential_id,
                "description": row.get("备注", "")[:4000],
            }
        )
    return cleaned, _merge_import_errors(errors)


def apply_node_import(
    session: Session,
    rows: Sequence[dict],
    user_id: int,
) -> Dict[str, int]:
    """在调用方事务内创建节点并恢复同 IP 历史行。"""
    created = 0
    restored = 0
    for row in rows:
        _node, was_restored = create_or_restore_node(session, user_id, **row)
        if was_restored:
            restored += 1
        else:
            created += 1
    return {"created": created, "restored": restored, "total": created + restored}


def build_node_template_bytes() -> bytes:
    """生成表头与说明页齐全的节点批量导入工作簿。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "节点导入"
    sheet.append(IMPORT_HEADERS)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:I1"
    widths = (22, 18, 12, 14, 32, 34, 24, 24, 36)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="5267C8")
        cell.alignment = Alignment(horizontal="center")
    environment_validation = DataValidation(type="list", formula1='"开发,测试,生产"')
    sheet.add_data_validation(environment_validation)
    environment_validation.add("D2:D1001")
    guide = workbook.create_sheet("填写说明")
    guide.append(["节点批量导入说明"])
    guide.append(["1. 主机名、IP、SSH端口为必填，IP 地址在当前资产中唯一。"])
    guide.append(["2. 所属环境填写开发、测试、生产或 dev/test/prod；空值按测试环境处理。"])
    guide.append(["3. 节点组可填多个名称，用逗号分隔；每个节点最多关联 3 个组。"])
    guide.append(["4. 凭证填写已启用凭证的名称；空值或 - 表示不设置。"])
    guide.append(["5. 同 IP 的逻辑删除节点会恢复原主键，保留既有历史关联。"])
    guide.append(["6. 任一数据行校验失败时，整份文件不会导入。"])
    guide.column_dimensions["A"].width = 100
    for cell in guide[1]:
        cell.font = Font(bold=True, size=14, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="5267C8")
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def build_node_export_bytes(nodes: Sequence[Node]) -> bytes:
    """按导入模板表头生成不含凭证明文的节点导出文件。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "节点导出"
    sheet.append(IMPORT_HEADERS)
    for node in nodes:
        sheet.append(
            [
                node.hostname,
                node.ip,
                node.port,
                {"dev": "开发", "test": "测试", "prod": "生产"}.get(node.environment, node.environment),
                node.nginx_path or "",
                node.sync_setting.main_conf_path if node.sync_setting else "/etc/nginx/nginx.conf",
                ", ".join(group.name for group in sorted(node.groups, key=lambda item: item.name)),
                node.credential.name if node.credential else "",
                node.description,
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, width in enumerate((22, 18, 12, 14, 32, 34, 24, 24, 36), start=1):
        sheet.column_dimensions[chr(64 + index)].width = width
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="5267C8")
        cell.alignment = Alignment(horizontal="center")
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
