"""任务中心 result / 摘要格式化工具（对齐发布回滚树协议）"""
import re


def strip_nginx_version(value):
    """去掉 nginx/ 或 nginx- 前缀，返回纯版本号"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return re.sub(r"(?i)^nginx[/\\-]", "", text)


def upgrade_detail_short(current_version, target_version):
    """生成 Nginx 升级短摘要：旧 → 新"""
    cur = strip_nginx_version(current_version) or "未知"
    tgt = strip_nginx_version(target_version) or "未知"
    return f"{cur} → {tgt}"


def node_header(ip, hostname):
    """生成结果树节点行"""
    ip = (ip or "").strip()
    hostname = (hostname or "").strip()
    if ip and hostname:
        return f"[节点] {ip} ({hostname})"
    if hostname:
        return f"[节点] {hostname}"
    return f"[节点] {ip or 'unknown'}"


def item_success(label):
    """生成成功明细行"""
    return f"  [成功] {label}"


def item_failed(label, reason=""):
    """生成失败明细行"""
    label = (label or "").strip() or "操作"
    reason = (reason or "").strip()
    if reason:
        return f"  [失败] {label} - 失败原因: {reason}"
    return f"  [失败] {label}"


def build_tree_result(success_count, fail_count, total, node_blocks):
    """组装标准结果树文本

    Args:
        success_count: 成功数
        fail_count: 失败数
        total: 总数
        node_blocks: 各节点行列表（已含 [节点] 与子项行）
    """
    lines = [f"执行完成：成功 {success_count}，失败 {fail_count}，共 {total}"]
    lines.extend(node_blocks)
    return "\n".join(lines)


def targets_from_release_tasks(task_ids):
    """从发布/回滚任务 ID 列表提取 target_hostnames/ips/configs"""
    from apps.releases.models import ReleaseTask

    qs = (
        ReleaseTask.objects.filter(id__in=task_ids)
        .select_related("node", "config")
        .order_by("id")
    )
    hostnames = []
    ips = []
    configs = []
    seen_h = set()
    seen_c = set()
    for t in qs:
        if t.node_id:
            hn = t.node.hostname or ""
            if hn and hn not in seen_h:
                seen_h.add(hn)
                hostnames.append(hn)
                ips.append(t.node.ip or "")
        if t.config_id:
            name = t.config.name or ""
            if name and name not in seen_c:
                seen_c.add(name)
                configs.append(name)
    return {
        "target_hostnames": ",".join(hostnames),
        "target_ips": ",".join(ips),
        "target_configs": ",".join(configs),
    }


def short_error_tail(text, max_lines=5):
    """截取错误信息末尾若干行，供结果树展示"""
    if not text:
        return ""
    lines = [ln for ln in str(text).strip().splitlines() if ln.strip()]
    if not lines:
        return str(text).strip()[:200]
    return " | ".join(lines[-max_lines:])


_FAILED_REASON_SEP = " - 失败原因: "


def split_failed_item(raw):
    """将失败明细正文拆成 (label, reason)；无失败原因前缀则 reason 为空"""
    text = (raw or "").strip()
    if not text:
        return "", ""
    if _FAILED_REASON_SEP in text:
        label, reason = text.split(_FAILED_REASON_SEP, 1)
        return label.strip(), reason.strip()
    return text, ""


def split_error_reason_lines(reason):
    """将折叠的失败原因还原为多行（支持 ' | ' 与真实换行）"""
    if not reason:
        return []
    lines = []
    for chunk in str(reason).split(" | "):
        for ln in chunk.splitlines():
            s = ln.strip()
            if s:
                lines.append(s)
    return lines


_RE_SUCCESS_FAIL = re.compile(
    r"成功\s*(\d+).*?失败\s*(\d+)",
    re.DOTALL,
)
_RE_CRED_NAME = re.compile(r"凭证\s+(.+?)(?:：|:)")
_RE_OLD_NGINX_DETAIL = re.compile(
    r"Nginx\s*升级\s*\[[^\]]*\]:\s*\S+\s*→\s*(.+)$",
    re.IGNORECASE,
)
_RE_VERSION_ARROW = re.compile(
    r"^(.+?)\s*→\s*(.+)$",
)


def _extract_success_fail(text):
    """从文案中提取成功/失败计数，返回 '成功 S / 失败 F' 或空串"""
    if not text:
        return ""
    m = _RE_SUCCESS_FAIL.search(str(text))
    if not m:
        return ""
    return f"成功 {m.group(1)} / 失败 {m.group(2)}"


def _shorten_nginx_secondary(detail):
    """将 Nginx 摘要副行规范为「旧 → 新」或短步骤"""
    text = (detail or "").strip()
    if not text:
        return ""
    # 历史长格式：Nginx 升级 [UG-…]: host → nginx-X
    m_old = _RE_OLD_NGINX_DETAIL.search(text)
    if m_old:
        tgt = strip_nginx_version(m_old.group(1)) or "未知"
        return f"未知 → {tgt}"
    # 已是版本箭头
    m_arrow = _RE_VERSION_ARROW.match(text)
    if m_arrow and "Nginx" not in text:
        cur = strip_nginx_version(m_arrow.group(1)) or m_arrow.group(1).strip()
        tgt = strip_nginx_version(m_arrow.group(2)) or m_arrow.group(2).strip()
        return f"{cur} → {tgt}"
    # 运行中步骤：缩短「正在…」前缀
    if text.startswith("正在"):
        return text.replace("正在", "").replace("...", "").strip() or text
    return text


def _shorten_hostnames(hosts_str, limit=3):
    """列表摘要主机名最多展示 limit 台，超出则「前N台 等总数台」"""
    text = (hosts_str or "").strip()
    if not text:
        return ""
    names = [n.strip() for n in text.split(",") if n.strip()]
    if not names:
        return ""
    if len(names) <= limit:
        return ",".join(names)
    shown = ",".join(names[:limit])
    return f"{shown} 等{len(names)}台"


def _credential_primary(task):
    """凭证任务主行：凭证名"""
    name = (task.target_configs or "").strip()
    if name:
        return name.split(",")[0].strip()
    detail = task.detail or ""
    m = _RE_CRED_NAME.search(detail)
    if m:
        return m.group(1).strip()
    return ""


def _default_primary(task):
    """通用主行：主机 → 批次 → 配置"""
    hosts = (task.target_hostnames or "").strip()
    if hosts:
        return _shorten_hostnames(hosts)
    batch = (task.source_batch or "").strip()
    if batch:
        return batch
    configs = (task.target_configs or "").strip()
    if configs:
        return configs
    return ""


def _batch_or_hosts_primary(task):
    """有来源批次则主行用批次，否则回退主机名摘要"""
    batch = (task.source_batch or "").strip()
    if batch:
        return batch
    hosts = (task.target_hostnames or "").strip()
    if hosts:
        return _shorten_hostnames(hosts)
    return ""


def format_task_center_summary(task):
    """格式化任务中心列表摘要：主行=目标，副行=结果

    Returns:
        (primary, secondary)
    """
    op = task.operation_type or ""
    detail = (task.detail or "").strip()
    result = (task.result or "").strip()
    status = task.status or ""

    if op == "nginx_upgrade":
        primary = _batch_or_hosts_primary(task)
        secondary = _shorten_nginx_secondary(detail)
        if not secondary and status in ("success", "failed", "cancelled"):
            secondary = _extract_success_fail(result) or detail
        return primary or "-", secondary

    if op in ("nginx_install", "nginx_service_control"):
        primary = _batch_or_hosts_primary(task)
        secondary = _extract_success_fail(detail) or _extract_success_fail(result)
        if not secondary:
            secondary = detail
        return primary or "-", secondary

    if op == "credential_enable_test":
        primary = _credential_primary(task) or "-"
        secondary = _extract_success_fail(detail) or _extract_success_fail(result)
        if not secondary:
            if "无可测试节点" in detail or "无可测试节点" in result:
                secondary = "无可测试节点"
            elif status in ("pending", "running"):
                secondary = detail if detail and "凭证" not in detail[:3] else "测试中"
                # 去掉「凭证 xxx：」前缀
                secondary = re.sub(r"^凭证\s+.+?(?:：|:)\s*", "", secondary).strip() or "测试中"
            else:
                secondary = re.sub(r"^凭证\s+.+?(?:：|:)\s*", "", detail).strip() or detail
        return primary, secondary

    if op in ("release_publish", "release_rollback"):
        batch = (task.source_batch or "").strip()
        hosts = (task.target_hostnames or "").strip()
        primary = batch or (_shorten_hostnames(hosts) if hosts else "")
        secondary = _extract_success_fail(detail) or _extract_success_fail(result)
        if not secondary:
            # 保留短 detail（如「回滚：cfg → host vN」「执行中…」）
            secondary = detail
        return primary or "-", secondary

    # SSH / 同步 / 其它
    primary = _default_primary(task) or "-"
    secondary = _extract_success_fail(detail) or _extract_success_fail(result)
    if not secondary:
        # 单节点短结果：连接成功/失败
        if detail in ("连接成功", "连接失败") or detail.startswith("同步"):
            secondary = detail
        elif detail:
            secondary = detail
        elif result:
            # 取结果首行作摘要
            secondary = result.splitlines()[0].strip()
    return primary, secondary
