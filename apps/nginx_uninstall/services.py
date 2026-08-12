"""Nginx 卸载：路径解析、安全校验与后台执行流水线。"""
import json
import logging
import re
import shlex
import threading

from django.utils import timezone

from apps.nodes.models import Node
from apps.releases.models import TaskCenterTask
from apps.releases.task_cancel import finish_if_active, is_cancelled, update_if_active
from apps.releases.task_progress import _clear_release_progress_state, _set_current_step
from apps.releases.task_result import (
    build_tree_result,
    item_failed,
    item_success,
    node_header,
)
from utils.nginx_ops import (
    can_manage_systemd_unit,
    detect_nginx_manage_mode,
    is_nginx_running,
    remove_nginx_systemd_unit,
    stop_nginx,
)
from utils.setting_service import get_setting
from utils.ssh import SSHClient, _safe_backup_hostname

logger = logging.getLogger(__name__)

# 禁止作为卸载目标的过短/系统根路径
_FORBIDDEN_PATHS = frozenset({
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
})


def batch_max_count():
    """读取批量操作最大节点数"""
    try:
        return max(1, int(get_setting("node.batch_max_count", "3") or 3))
    except (TypeError, ValueError):
        return 3


def _default_work_dir():
    """读取默认编译工作目录"""
    return (
        get_setting("upgrade.default_work_dir", "/tmp/nginx-upgrade")
        or "/tmp/nginx-upgrade"
    )


def _default_backup_dir():
    """读取远程发布备份根目录"""
    return (
        get_setting("release.backup_dir", "/opt/app/mascloud/ansible/mngxops")
        or "/opt/app/mascloud/ansible/mngxops"
    )


def normalize_remote_path(path):
    """规范化远程绝对路径（去尾部斜杠，保留根 /）"""
    raw = (path or "").strip()
    if not raw:
        return ""
    if not raw.startswith("/"):
        return ""
    cleaned = re.sub(r"/+", "/", raw)
    if cleaned != "/" and cleaned.endswith("/"):
        cleaned = cleaned.rstrip("/")
    return cleaned


def is_dangerous_path(path):
    """判断路径是否禁止删除（过短系统根等）"""
    p = normalize_remote_path(path)
    if not p:
        return True
    if p in _FORBIDDEN_PATHS:
        return True
    # 至少两级路径，如 /opt/app
    parts = [x for x in p.split("/") if x]
    if len(parts) < 2:
        return True
    return False


def derive_prefix_from_nginx_path(nginx_path):
    """由二进制路径推导 prefix（…/sbin/nginx → …）"""
    p = normalize_remote_path(nginx_path)
    if not p:
        return ""
    if p.endswith("/sbin/nginx"):
        return p[: -len("/sbin/nginx")] or ""
    if p.endswith("/nginx"):
        parent = p.rsplit("/", 1)[0]
        if parent.endswith("/sbin"):
            return parent[: -len("/sbin")] or ""
    return ""


def resolve_prefix_for_node(node):
    """按优先级解析节点 --prefix：-V → nginx_path → 最近成功安装。

    Returns:
        (prefix, source_label, error_message)
    """
    # 1) nginx -V
    try:
        from apps.upgrade.services import fetch_nginx_v_from_node

        ok, parsed = fetch_nginx_v_from_node(node)
        if ok and isinstance(parsed, dict):
            prefix = normalize_remote_path(parsed.get("prefix") or "")
            if prefix and not is_dangerous_path(prefix):
                return prefix, "nginx -V", ""
    except Exception:
        logger.exception("解析节点 %s nginx -V 失败", getattr(node, "id", None))

    # 2) Node.nginx_path 推导
    derived = derive_prefix_from_nginx_path(node.nginx_path or "")
    if derived and not is_dangerous_path(derived):
        return derived, "nginx_path", ""

    # 3) 最近成功安装任务
    try:
        from apps.nginx_install.models import NginxInstallTask

        last = (
            NginxInstallTask.objects.filter(node_id=node.id, status="success")
            .order_by("-finished_at", "-id")
            .first()
        )
        if last:
            prefix = normalize_remote_path(last.target_prefix or "")
            if prefix and not is_dangerous_path(prefix):
                return prefix, "install_task", ""
    except Exception:
        logger.exception("读取节点 %s 安装历史失败", getattr(node, "id", None))

    return "", "", "无法解析安装路径，请手工填写合法 --prefix"


def backup_subdir_for_node(node):
    """组装该节点远程发布备份子目录路径"""
    root = normalize_remote_path(_default_backup_dir())
    host = _safe_backup_hostname(node.hostname)
    if not root or is_dangerous_path(root):
        # 根目录本身过短时仍允许拼子目录，但需整体通过校验
        pass
    path = f"{root.rstrip('/')}/{host}"
    return normalize_remote_path(path)


def is_file_like_path(path):
    """粗判路径更像文件（含扩展名或 sbin/bin 下的 nginx 二进制）"""
    p = normalize_remote_path(path)
    if not p:
        return False
    name = p.rsplit("/", 1)[-1]
    if "." in name:
        return True
    if name == "nginx":
        # 仅 …/sbin/nginx、…/bin/nginx 视为文件；名为 nginx 的目录段视为目录
        parts = [x for x in p.split("/") if x]
        if len(parts) >= 2 and parts[-2] in ("sbin", "bin"):
            return True
        return False
    if name in ("nginx.pid", "nginx.lock"):
        return True
    return False


def resolve_nginx_tree_path(path):
    """将路径收敛到最右侧名为 nginx 的目录段；sbin/bin 下二进制除外。

    例：/etc/nginx/nginx.conf → /etc/nginx；/usr/sbin/nginx 保持原样。
    找不到 nginx 目录段时原样返回。若收敛结果危险则回退原路径。
    """
    p = normalize_remote_path(path)
    if not p:
        return ""
    parts = [x for x in p.split("/") if x]
    if not parts:
        return p
    # …/sbin/nginx 或 …/bin/nginx：只删二进制，不升到父目录
    if parts[-1] == "nginx" and len(parts) >= 2 and parts[-2] in ("sbin", "bin"):
        return p
    # 最右侧段名为 nginx 的位置
    nginx_idx = None
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "nginx":
            nginx_idx = i
            break
    if nginx_idx is None:
        return p
    resolved = "/" + "/".join(parts[: nginx_idx + 1])
    if is_dangerous_path(resolved):
        return p
    return resolved


def coalesce_delete_targets(targets):
    """父子去重：只保留最外层路径，丢弃位于其它目标之下的项。

    Args:
        targets: list[(path, kind)] 或 list[str]

    Returns:
        与输入同形的列表（tuple 保留 kind，str 仅路径）
    """
    if not targets:
        return []
    as_tuples = isinstance(targets[0], (tuple, list))
    items = []
    for t in targets:
        if as_tuples:
            path, kind = t[0], t[1]
        else:
            path, kind = t, "dir"
        np = normalize_remote_path(path)
        if not np:
            continue
        items.append((np, kind if as_tuples else None))
    # 短路径优先，便于外层先入选
    items.sort(key=lambda x: (len(x[0]), x[0]))
    kept = []
    for path, kind in items:
        under = False
        for kp, _ in kept:
            if path == kp or path.startswith(kp.rstrip("/") + "/"):
                under = True
                break
        if under:
            continue
        kept.append((path, kind))
    if as_tuples:
        return [(p, k if k is not None else "dir") for p, k in kept]
    return [p for p, _ in kept]


def extract_path_entries_from_nginx_v(parsed):
    """从 nginx -V 解析结果提取可勾选路径条目。

    Returns:
        list[dict]: key/label/path/source/required/checked/editable/kind
    """
    if not isinstance(parsed, dict):
        return []
    entries = []
    seen = set()
    params = parsed.get("params") or []
    for token in params:
        if not isinstance(token, str) or "=" not in token:
            continue
        key, raw_val = token.split("=", 1)
        key = key.strip()
        raw_path = normalize_remote_path(raw_val.strip().strip("'\""))
        if not raw_path:
            continue
        if key != "--prefix" and "-path" not in key:
            continue
        is_prefix = key == "--prefix"
        # prefix 保持探测原值；其余收敛到 …/nginx 目录
        path = raw_path if is_prefix else resolve_nginx_tree_path(raw_path)
        if path in seen:
            continue
        seen.add(path)
        entries.append({
            "key": "prefix" if is_prefix else key,
            "label": "--prefix" if is_prefix else key,
            "path": path,
            "source": "nginx -V",
            "required": is_prefix,
            "checked": is_prefix or key in (
                "--sbin-path", "--modules-path", "--conf-path", "--pid-path",
            ),
            "editable": is_prefix,
            "kind": "dir" if is_prefix or not is_file_like_path(path) else "file",
        })
    # 无 --prefix token 时用 parsed.prefix 兜底
    if not any(e["key"] == "prefix" for e in entries):
        prefix = normalize_remote_path(parsed.get("prefix") or "")
        if prefix:
            entries.insert(0, {
                "key": "prefix",
                "label": "--prefix",
                "path": prefix,
                "source": "nginx -V",
                "required": True,
                "checked": True,
                "editable": True,
                "kind": "dir",
            })
    return entries


def _setting_path_entries(node, work_dir):
    """组装系统设置类路径条目"""
    backup_path = backup_subdir_for_node(node)
    modules_dir = f"{work_dir.rstrip('/')}/nginx-modules" if work_dir else ""
    return [
        {
            "key": "release_backup",
            "label": "发布备份目录",
            "path": backup_path,
            "source": "系统设置",
            "required": False,
            "checked": False,
            "editable": False,
            "kind": "dir",
        },
        {
            "key": "work_dir",
            "label": "编译工作目录",
            "path": work_dir,
            "source": "系统设置",
            "required": False,
            "checked": False,
            "editable": False,
            "kind": "dir",
        },
        {
            "key": "nginx_modules",
            "label": "第三方模块源码目录",
            "path": modules_dir,
            "source": "系统设置",
            "required": False,
            "checked": False,
            "editable": False,
            "kind": "dir",
        },
    ]


def _fetch_v_and_prefix(node):
    """探测 nginx -V 并解析 prefix；返回 (prefix, source, err, parsed, paths_from_v)"""
    parsed = None
    try:
        from apps.upgrade.services import fetch_nginx_v_from_node

        ok, result = fetch_nginx_v_from_node(node)
        if ok and isinstance(result, dict):
            parsed = result
    except Exception:
        logger.exception("探测节点 %s nginx -V 失败", getattr(node, "id", None))

    paths_v = extract_path_entries_from_nginx_v(parsed) if parsed else []
    prefix = ""
    source = ""
    err = ""
    for e in paths_v:
        if e["key"] == "prefix":
            prefix = e["path"]
            source = "nginx -V"
            break
    if not prefix:
        prefix, source, err = resolve_prefix_for_node(node)
        if prefix and not any(e["key"] == "prefix" for e in paths_v):
            paths_v.insert(0, {
                "key": "prefix",
                "label": "--prefix",
                "path": prefix,
                "source": source or "nginx_path",
                "required": True,
                "checked": True,
                "editable": True,
                "kind": "dir",
            })
    return prefix, source, err, parsed, paths_v


def uninstall_gate_message(node):
    """返回禁止卸载的原因；允许时返回 None。"""
    if node.is_locked:
        return "节点已锁定"
    if node.status != "online":
        return "节点非在线状态"
    cred = node.credential
    if not cred:
        return "未配置凭证"
    if not cred.is_enabled:
        return "凭证已禁用"
    # 对齐升级：仅 online + Nginx 可用
    if node.nginx_available is True:
        return None
    return "未检测到 Nginx"


def _auth_kwargs(credential):
    """按凭证类型组装认证参数"""
    if credential.auth_type == "password":
        return {"password": credential.get_password()}
    return {"private_key": credential.get_private_key()}


def _pkg_sudo_cmd(cmd, use_sudo=False):
    """按需为包管理命令加 sudo -n。"""
    cmd = (cmd or "").strip()
    if not cmd:
        return cmd
    if use_sudo:
        return f"sudo -n {cmd}"
    return cmd


def detect_nginx_package_origin(ssh, nginx_path=""):
    """探测 nginx 二进制是否由 rpm/dpkg 包拥有。

    Returns:
        dict: origin(package|source), mgr(rpm|deb|""), package, binary
    """
    result = {
        "origin": "source",
        "mgr": "",
        "package": "",
        "binary": "",
    }
    bin_path = normalize_remote_path(nginx_path or "")
    if not bin_path:
        ok, out = ssh.execute_command("command -v nginx 2>/dev/null || true")
        cand = (out or "").strip().splitlines()
        bin_path = normalize_remote_path(cand[-1].strip() if cand else "")
    if not bin_path:
        return result
    result["binary"] = bin_path
    quoted = shlex.quote(bin_path)

    ok, out = ssh.execute_command(
        f"rpm -qf --queryformat '%{{NAME}}' {quoted} 2>/dev/null || true"
    )
    name = (out or "").strip().splitlines()
    name = name[-1].strip() if name else ""
    low = name.lower()
    if ok and name and "not owned" not in low and "error" not in low and "\n" not in name:
        result["origin"] = "package"
        result["mgr"] = "rpm"
        result["package"] = name
        return result

    ok, out = ssh.execute_command(f"dpkg -S {quoted} 2>/dev/null || true")
    text = (out or "").strip()
    if ok and text and "no path found" not in text.lower():
        first = text.splitlines()[0]
        left = first.split(":", 1)[0].strip()
        pkg_name = left.split(",")[0].strip()
        if pkg_name:
            result["origin"] = "package"
            result["mgr"] = "deb"
            result["package"] = pkg_name
            return result

    return result


def remove_nginx_package(ssh, package, mgr="", use_sudo=False, log_fn=None):
    """通过 dnf/yum/apt-get 卸载包。

    Returns:
        tuple: (ok, msg)
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    package = (package or "").strip()
    if not package:
        return False, "未解析到软件包名"
    quoted_pkg = shlex.quote(package)
    mgr = (mgr or "").strip()

    def _has(cmd):
        ok, out = ssh.execute_command(
            f"command -v {cmd} >/dev/null 2>&1 && echo OK"
        )
        return ok and "OK" in (out or "")

    if mgr == "deb" or (mgr != "rpm" and _has("apt-get") and not _has("rpm")):
        cmd = (
            f"DEBIAN_FRONTEND=noninteractive apt-get remove -y {quoted_pkg} 2>&1"
        )
    elif _has("dnf"):
        cmd = f"dnf remove -y {quoted_pkg} 2>&1"
    elif _has("yum"):
        cmd = f"yum remove -y {quoted_pkg} 2>&1"
    elif _has("apt-get"):
        cmd = (
            f"DEBIAN_FRONTEND=noninteractive apt-get remove -y {quoted_pkg} 2>&1"
        )
    else:
        return False, "未找到 dnf/yum/apt-get，无法包管理卸载"

    full = _pkg_sudo_cmd(cmd, use_sudo=use_sudo)
    _log(f"执行: {full}")
    ok, out = ssh.execute_command(full)
    if not ok:
        return False, (out or "").strip() or "包管理器卸载失败"
    return True, (out or "").strip() or f"已卸载软件包 {package}"


def _parse_options(raw):
    """解析删除选项 JSON（含 extra_paths）"""
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            data = {}
    extra = data.get("extra_paths") or []
    if not isinstance(extra, list):
        extra = []
    normalized_extra = []
    for item in extra:
        if not isinstance(item, dict):
            continue
        path = normalize_remote_path(item.get("path") or "")
        if not path:
            continue
        kind = item.get("kind") or ("file" if is_file_like_path(path) else "dir")
        normalized_extra.append({
            "key": str(item.get("key") or path),
            "path": path,
            "kind": "file" if kind == "file" else "dir",
        })
    return {
        "remove_backup": bool(data.get("remove_backup", False)),
        "remove_workdir": bool(data.get("remove_workdir", False)),
        "remove_modules": bool(data.get("remove_modules", False)),
        "stop_if_running": bool(data.get("stop_if_running", True)),
        "extra_paths": normalized_extra,
        "work_dir": normalize_remote_path(data.get("work_dir") or ""),
    }


def preview_nodes(node_ids):
    """预览选中节点的卸载路径与运行状态。

    Returns:
        dict: success / nodes / message
    """
    if not node_ids:
        return {"success": False, "message": "未选择任何节点"}
    try:
        node_ids = [int(x) for x in node_ids]
    except (TypeError, ValueError):
        return {"success": False, "message": "节点 ID 无效"}

    max_batch = batch_max_count()
    if len(node_ids) > max_batch:
        return {"success": False, "message": f"最多只能操作 {max_batch} 个节点"}

    nodes = list(
        Node.objects.filter(id__in=node_ids, is_deleted=False)
        .select_related("credential")
        .order_by("id")
    )
    if not nodes:
        return {"success": False, "message": "节点不存在"}

    work_dir = normalize_remote_path(_default_work_dir())
    items = []
    for node in nodes:
        gate = uninstall_gate_message(node)
        prefix, source, err = ("", "", "")
        paths = []
        running = False
        running_error = ""
        manage_mode = "unknown"
        manage_unit = ""
        can_manage = False
        cred_username = ""
        install_origin = "unknown"
        package_mgr = ""
        package_name = ""
        if node.credential_id and node.credential:
            cred_username = node.credential.username or ""
        if not gate:
            prefix, source, err, _parsed, paths_v = _fetch_v_and_prefix(node)
            paths = list(paths_v) + _setting_path_entries(node, work_dir)
            cred = node.credential
            try:
                with SSHClient(
                    node.ip, node.port, cred.username, **_auth_kwargs(cred)
                ) as ssh:
                    conn = getattr(ssh, "_connect_result", None)
                    if isinstance(conn, tuple) and not conn[0]:
                        running_error = conn[1] or "SSH 连接失败"
                    else:
                        pkg_info = detect_nginx_package_origin(
                            ssh, nginx_path=node.nginx_path or ""
                        )
                        install_origin = pkg_info.get("origin") or "source"
                        package_mgr = pkg_info.get("mgr") or ""
                        package_name = pkg_info.get("package") or ""
                        if install_origin == "package":
                            # 包安装：-V 路径锁定勾选展示，实际由包管理器卸载（不 rm）
                            for p in paths:
                                if p.get("source") == "nginx -V":
                                    p["checked"] = True
                                    p["required"] = True
                                    p["editable"] = False
                                    p["package_owned"] = True
                        mode, detail = detect_nginx_manage_mode(
                            node.ip,
                            node.port,
                            cred.username,
                            client=ssh.client,
                            **_auth_kwargs(cred),
                        )
                        manage_mode = mode or "binary"
                        manage_unit = (detail or {}).get("unit") or ""
                        ok_cap, _use_sudo, _reason = can_manage_systemd_unit(ssh)
                        can_manage = bool(ok_cap)
                        running = is_nginx_running(
                            node.ip,
                            node.port,
                            cred.username,
                            nginx_path=node.nginx_path or None,
                            client=ssh.client,
                            **_auth_kwargs(cred),
                        )
            except Exception as exc:
                running_error = str(exc)
        else:
            paths = _setting_path_entries(node, work_dir)

        backup_path = backup_subdir_for_node(node)
        if install_origin == "package" and package_name:
            eligible = gate is None
        else:
            eligible = (
                gate is None
                and bool(prefix)
                and not is_dangerous_path(prefix)
            )
        items.append({
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "nginx_path": node.nginx_path or "",
            "nginx_available": node.nginx_available,
            "eligible": eligible,
            "gate_message": gate or err or "",
            "prefix": prefix,
            "prefix_source": source,
            "backup_path": backup_path,
            "work_dir": work_dir,
            "modules_dir": f"{work_dir.rstrip('/')}/nginx-modules" if work_dir else "",
            "paths": paths,
            "running": running,
            "running_error": running_error,
            "credential_username": cred_username,
            "manage_mode": manage_mode,
            "manage_unit": manage_unit,
            "can_manage_systemd": can_manage,
            "install_origin": install_origin,
            "package_mgr": package_mgr,
            "package_name": package_name,
        })


    return {
        "success": True,
        "nodes": items,
        "defaults": {
            "remove_backup": False,
            "remove_workdir": False,
            "remove_modules": False,
            "work_dir": work_dir,
        },
    }


def _options_from_selected_paths(item, node, stop_if_running, batch_work_dir):
    """从 selected_paths（或旧字段）解析节点删除选项。

    Returns:
        (prefix, backup_path, work_dir, options_dict) 或 (None, None, None, error_message)
    """
    work_dir = normalize_remote_path(
        (item.get("work_dir") or "").strip() or batch_work_dir
    )
    backup_path = backup_subdir_for_node(node)
    modules_dir = f"{work_dir.rstrip('/')}/nginx-modules" if work_dir else ""
    selected = item.get("selected_paths")

    if isinstance(selected, list) and selected:
        prefix = ""
        remove_backup = False
        remove_workdir = False
        remove_modules = False
        extra_paths = []
        for sp in selected:
            if not isinstance(sp, dict):
                continue
            key = str(sp.get("key") or "").strip()
            path = normalize_remote_path(sp.get("path") or "")
            if key in ("prefix", "--prefix"):
                prefix = path
                continue
            if key == "release_backup":
                remove_backup = True
                if path:
                    backup_path = path
                continue
            if key == "work_dir":
                remove_workdir = True
                if path:
                    work_dir = path
                    modules_dir = f"{work_dir.rstrip('/')}/nginx-modules"
                continue
            if key == "nginx_modules":
                remove_modules = True
                if path:
                    modules_dir = path
                continue
            if path:
                resolved = resolve_nginx_tree_path(path)
                extra_paths.append({
                    "key": key or resolved,
                    "path": resolved,
                    "kind": "file" if is_file_like_path(resolved) else "dir",
                })
        if not prefix:
            prefix = normalize_remote_path(
                (item.get("prefix") or "").strip()
            ) or resolve_prefix_for_node(node)[0]
        opts = {
            "remove_backup": remove_backup,
            "remove_workdir": remove_workdir,
            "remove_modules": remove_modules,
            "stop_if_running": stop_if_running,
            "extra_paths": extra_paths,
            "work_dir": work_dir,
            "modules_dir": modules_dir,
        }
        return prefix, backup_path, work_dir, opts

    # 兼容旧 payload
    prefix = normalize_remote_path(
        (item.get("prefix") or "").strip()
    ) or resolve_prefix_for_node(node)[0]
    opts = {
        "remove_backup": bool(item.get("remove_backup", False)),
        "remove_workdir": bool(item.get("remove_workdir", False)),
        "remove_modules": bool(item.get("remove_modules", False)),
        "stop_if_running": stop_if_running,
        "extra_paths": [],
        "work_dir": work_dir,
        "modules_dir": modules_dir,
    }
    return prefix, backup_path, work_dir, opts


def _under_prefix(path, prefix):
    """判断 path 是否等于或位于 prefix 目录树内"""
    p = normalize_remote_path(path)
    pref = normalize_remote_path(prefix)
    if not p or not pref:
        return False
    if p == pref:
        return True
    return p.startswith(pref.rstrip("/") + "/")


def create_uninstall_batch_from_data(user, data):
    """校验并创建卸载批次，启动后台线程。

    Returns:
        dict: API 响应字段
    """
    from apps.audit.utils import log_task_center_created

    from .models import NginxUninstallTask, generate_uninstall_batch_number

    items = data.get("nodes") or []
    if not isinstance(items, list) or not items:
        return {"success": False, "message": "请选择至少一个节点"}

    stop_if_running = bool(data.get("stop_if_running", True))
    batch_work_dir = normalize_remote_path(
        (data.get("work_dir") or "").strip() or _default_work_dir()
    )

    max_batch = batch_max_count()
    if len(items) > max_batch:
        return {"success": False, "message": f"最多只能操作 {max_batch} 个节点"}

    prepared = []
    rejected = []
    for item in items:
        if not isinstance(item, dict):
            rejected.append("无效节点条目")
            continue
        try:
            nid = int(item.get("id") or item.get("node_id"))
        except (TypeError, ValueError, AttributeError):
            rejected.append("无效节点 ID")
            continue
        try:
            node = Node.objects.select_related("credential").get(id=nid, is_deleted=False)
        except Node.DoesNotExist:
            rejected.append(f"节点 {nid} 不存在")
            continue
        gate = uninstall_gate_message(node)
        if gate:
            rejected.append(f"{node.hostname}（{gate}）")
            continue

        prefix, backup_path, work_dir, node_opts = _options_from_selected_paths(
            item, node, stop_if_running, batch_work_dir
        )

        # 复用预览包归属（create 不做同步 SSH；执行线程再权威探测）
        origin = str(item.get("install_origin") or "").strip().lower()
        pkg_name = str(item.get("package_name") or "").strip()
        pkg_mgr = str(item.get("package_mgr") or "").strip()
        is_pkg = origin == "package" and bool(pkg_name)
        node_opts["install_origin"] = "package" if is_pkg else "source"
        node_opts["package_mgr"] = pkg_mgr if is_pkg else ""
        node_opts["package_name"] = pkg_name if is_pkg else ""

        if is_pkg:
            # 包路径不 rm 安装树；忽略 -V 额外路径勾选
            node_opts["extra_paths"] = []
            if not prefix:
                prefix = normalize_remote_path(node.nginx_path or "") or (
                    f"package:{node_opts['package_name']}"
                )
        else:
            if not prefix:
                rejected.append(f"{node.hostname}（无法解析安装路径）")
                continue
            if is_dangerous_path(prefix):
                rejected.append(f"{node.hostname}（禁止删除路径 {prefix}）")
                continue

        if node_opts["remove_backup"] and is_dangerous_path(backup_path):
            rejected.append(f"{node.hostname}（发布备份路径不安全：{backup_path}）")
            continue
        if node_opts["remove_workdir"] and is_dangerous_path(work_dir):
            rejected.append(f"{node.hostname}（工作目录路径不安全：{work_dir}）")
            continue
        modules_dir = node_opts.get("modules_dir") or (
            f"{work_dir.rstrip('/')}/nginx-modules" if work_dir else ""
        )
        if node_opts["remove_modules"] and is_dangerous_path(modules_dir):
            rejected.append(f"{node.hostname}（模块目录路径不安全：{modules_dir}）")
            continue

        bad_extra = False
        filtered_extra = []
        for ep in node_opts.get("extra_paths") or []:
            ep_path = normalize_remote_path(ep.get("path") or "")
            if not ep_path or _under_prefix(ep_path, prefix):
                continue
            if is_dangerous_path(ep_path):
                rejected.append(
                    f"{node.hostname}（额外路径不安全：{ep_path}）"
                )
                bad_extra = True
                break
            filtered_extra.append({**ep, "path": ep_path})
        if bad_extra:
            continue
        node_opts["extra_paths"] = filtered_extra
        node_opts["modules_dir"] = modules_dir
        prepared.append((node, prefix, backup_path, work_dir, node_opts))

    if not prepared:
        msg = "没有可执行的节点"
        if rejected:
            msg += "：" + "；".join(rejected[:5])
        return {"success": False, "message": msg, "skipped": rejected}

    batch_number = generate_uninstall_batch_number()
    hostnames = ",".join(n.hostname for n, _, _, _, _ in prepared)
    ips = ",".join(n.ip for n, _, _, _, _ in prepared)
    tc = TaskCenterTask.objects.create(
        operation_type="nginx_uninstall",
        status="pending",
        detail="任务已创建，等待执行 Nginx 卸载",
        target_hostnames=hostnames,
        target_ips=ips,
        target_configs="uninstall",
        source_batch=batch_number,
        trigger_user=user,
    )
    log_task_center_created(tc, user=user)

    uninstall_ids = []
    for node, prefix, backup_path, work_dir, node_opts in prepared:
        options_payload = dict(node_opts)
        options_payload["work_dir"] = work_dir
        options_json = json.dumps(options_payload, ensure_ascii=False)
        ut = NginxUninstallTask.objects.create(
            batch_number=batch_number,
            node=node,
            resolved_prefix=prefix,
            backup_path=backup_path if node_opts["remove_backup"] else "",
            work_dir=work_dir,
            options_json=options_json,
            task_center=tc,
            operator=user,
        )
        uninstall_ids.append(ut.id)

    thread = threading.Thread(
        target=_run_uninstall_batch,
        args=(tc.id, uninstall_ids),
        daemon=True,
    )
    thread.start()

    message = f"已创建 Nginx 卸载任务（{len(prepared)} 台）"
    if rejected:
        message += f"；已跳过 {len(rejected)} 台"
    return {
        "success": True,
        "async": True,
        "message": message,
        "task_center_id": tc.id,
        "source_batch": batch_number,
        "skipped": rejected,
    }


def _append_log(task, line):
    """追加卸载任务日志并保存"""
    stamp = timezone.now().strftime("%H:%M:%S")
    task.log_output = (task.log_output or "") + f"[{stamp}] {line}\n"
    task.save(update_fields=["log_output", "updated_at"])


def _set_task_status(task, status, progress=None, step="", error=""):
    """更新单节点卸载任务状态"""
    fields = ["status", "current_step", "updated_at"]
    task.status = status
    task.current_step = step or ""
    if progress is not None:
        task.progress = progress
        fields.append("progress")
    if error:
        task.error_message = error
        fields.append("error_message")
    task.save(update_fields=fields)


def _rm_remote(ssh, path, log_fn, kind="dir"):
    """远程删除路径；不存在则跳过成功。kind=file 用 rm -f，否则 rm -rf。"""
    path = normalize_remote_path(path)
    if is_dangerous_path(path):
        return False, f"拒绝删除危险路径: {path}"
    quoted = shlex.quote(path)
    ok, out = ssh.execute_command(f"test -e {quoted} && echo EXISTS || echo MISSING")
    if not ok:
        return False, out or "探测路径失败"
    if "MISSING" in (out or ""):
        log_fn(f"路径不存在，跳过: {path}")
        return True, "skipped"
    cmd = f"rm -f {quoted}" if kind == "file" else f"rm -rf {quoted}"
    log_fn(f"删除({'文件' if kind == 'file' else '目录'}): {path}")
    ok, out = ssh.execute_command(cmd)
    if not ok:
        return False, out or "删除失败"
    return True, "removed"


def _rm_rf_remote(ssh, path, log_fn):
    """远程删除目录（兼容旧调用）"""
    return _rm_remote(ssh, path, log_fn, kind="dir")


def _bookkeep_node_after_uninstall(node, operator=None):
    """卸载成功后回写节点与配置同步路径。"""
    from apps.configs.models import ConfigSyncSetting
    from apps.configs.services import save_sync_path
    from apps.nodes.services import apply_nginx_probe_result

    node.nginx_path = ""
    node.nginx_version = ""
    apply_nginx_probe_result(node, False)
    node.save(
        update_fields=[
            "nginx_path",
            "nginx_version",
            "nginx_available",
            "last_nginx_probe_at",
            "updated_at",
        ]
    )
    # 清空主配置路径，避免指向已删 conf
    try:
        setting = ConfigSyncSetting.objects.filter(node=node).first()
        if setting and setting.main_conf_path:
            save_sync_path(node, "", user=operator)
    except Exception:
        logger.exception("清空节点 %s main_conf_path 失败", node.id)


def _collect_extra_delete_targets(task, options):
    """汇总 prefix 之外待删路径，去重且排除已在 prefix 下的项。"""
    prefix = normalize_remote_path(task.resolved_prefix)
    targets = []
    seen = set()

    def add(path, kind=None):
        """加入待删路径（收敛后）；kind 忽略，按收敛结果判定。"""
        np = resolve_nginx_tree_path(path)
        if not np or np in seen:
            return
        if np == prefix or _under_prefix(np, prefix):
            return
        # 收敛后按路径形态判定（…/nginx 为目录；sbin/nginx 等仍为文件）
        resolved_kind = "file" if is_file_like_path(np) else "dir"
        seen.add(np)
        targets.append((np, resolved_kind))

    if options.get("remove_backup") and task.backup_path:
        add(task.backup_path)
    work_dir = normalize_remote_path(task.work_dir or options.get("work_dir") or "")
    if options.get("remove_workdir") and work_dir:
        add(work_dir)
    modules_dir = normalize_remote_path(options.get("modules_dir") or "")
    if not modules_dir and options.get("remove_modules") and work_dir:
        modules_dir = f"{work_dir.rstrip('/')}/nginx-modules"
    if options.get("remove_modules") and modules_dir:
        add(modules_dir)
    for ep in options.get("extra_paths") or []:
        add(ep.get("path"))
    return coalesce_delete_targets(targets)


def _run_one_uninstall(task, stop_if_running):
    """执行单节点卸载，成功返回 None，失败返回错误信息。"""
    node = task.node
    cred = node.credential
    if not cred or not cred.is_enabled:
        return "凭证不可用"
    options = _parse_options(task.options_json)
    auth = _auth_kwargs(cred)

    def log(msg):
        _append_log(task, msg)

    with SSHClient(node.ip, node.port, cred.username, **auth) as ssh:
        conn = getattr(ssh, "_connect_result", None)
        if isinstance(conn, tuple) and not conn[0]:
            return f"SSH 连接失败: {conn[1] or '未知错误'}"
        try:
            running = is_nginx_running(
                node.ip,
                node.port,
                cred.username,
                nginx_path=node.nginx_path or None,
                client=ssh.client,
                **auth,
            )
        except Exception as exc:
            return f"检测运行状态失败: {exc}"

        if running:
            if not stop_if_running:
                return "Nginx 仍在运行，请先停止服务或确认「停止并继续卸载」"
            _set_task_status(task, "stopping", progress=20, step="停止 Nginx")
            log("Nginx 运行中，执行停止…")
            ok, msg = stop_nginx(
                node.ip,
                node.port,
                cred.username,
                nginx_path=node.nginx_path or None,
                client=ssh.client,
                **auth,
            )
            if not ok:
                return f"停止 Nginx 失败: {msg or '未知错误'}"
            log("已停止 Nginx")

        pkg_info = detect_nginx_package_origin(
            ssh, nginx_path=node.nginx_path or ""
        )
        # 创建时写入的包信息作回退
        if pkg_info.get("origin") != "package":
            if options.get("install_origin") == "package" and options.get("package_name"):
                pkg_info = {
                    "origin": "package",
                    "mgr": options.get("package_mgr") or "",
                    "package": options.get("package_name") or "",
                    "binary": node.nginx_path or "",
                }

        is_pkg = (
            pkg_info.get("origin") == "package" and bool(pkg_info.get("package"))
        )

        if is_pkg:
            _set_task_status(
                task, "removing_package", progress=45, step="包管理器卸载"
            )
            log(
                f"检测到包安装（{pkg_info.get('mgr') or '?'}:{pkg_info.get('package')}），"
                "通过包管理器卸载，不直接删除安装目录…"
            )
            ok_cap, use_sudo, reason = can_manage_systemd_unit(ssh, log_fn=log)
            if not ok_cap:
                return f"包管理器卸载需要系统权限: {reason}"
            ok, msg = remove_nginx_package(
                ssh,
                package=pkg_info.get("package"),
                mgr=pkg_info.get("mgr") or "",
                use_sudo=use_sudo,
                log_fn=log,
            )
            if not ok:
                # 原样返回远程输出，由批次写入完整执行日志
                return (msg or "").strip() or "包管理器卸载失败"
            log(msg or "包管理器卸载完成")
        else:
            _set_task_status(task, "removing_prefix", progress=45, step="删除安装目录")
            if is_dangerous_path(task.resolved_prefix):
                return f"禁止删除路径: {task.resolved_prefix}"
            ok, msg = _rm_remote(ssh, task.resolved_prefix, log, kind="dir")
            if not ok:
                return msg

        extras = _collect_extra_delete_targets(task, options)
        backup_targets = [
            t for t in extras
            if normalize_remote_path(task.backup_path) == t[0]
        ]
        other_targets = [
            t for t in extras
            if normalize_remote_path(task.backup_path) != t[0]
        ]

        if backup_targets:
            _set_task_status(task, "removing_backup", progress=65, step="清理发布备份")
            for path, kind in backup_targets:
                ok, msg = _rm_remote(ssh, path, log, kind=kind)
                if not ok:
                    return f"清理发布备份失败: {msg}"

        if other_targets:
            _set_task_status(task, "removing_extra", progress=80, step="清理额外路径")
            for path, kind in other_targets:
                ok, msg = _rm_remote(ssh, path, log, kind=kind)
                if not ok:
                    return f"清理额外路径失败: {msg}"

        # systemd：源码安装按托管清理；包安装仅清理平台自写 /etc unit（若仍在）
        if is_pkg:
            from utils.nginx_ops import UNIT_FILE_PATH

            ok, cout = ssh.execute_command(
                f"test -e {shlex.quote(UNIT_FILE_PATH)} && echo EXISTS || echo MISSING"
            )
            if "EXISTS" in (cout or ""):
                _set_task_status(task, "removing_unit", progress=85, step="清理 systemd")
                log("残留平台 nginx.service，尝试清理…")
                ok_cap, use_sudo, reason = can_manage_systemd_unit(ssh, log_fn=log)
                if not ok_cap:
                    return f"存在平台 unit 但无权限清理: {reason}"
                ok, msg = remove_nginx_systemd_unit(
                    ssh, unit_name="nginx", use_sudo=use_sudo, log_fn=log,
                )
                if not ok:
                    return f"清理 systemd unit 失败: {msg}"
                log(msg or "systemd unit 已清理")
            else:
                log("包卸载完成，无平台自写 unit，跳过 systemd 清理")
        else:
            mode, detail = detect_nginx_manage_mode(
                node.ip,
                node.port,
                cred.username,
                client=ssh.client,
                **auth,
            )
            if mode == "systemctl":
                _set_task_status(task, "removing_unit", progress=85, step="清理 systemd")
                log("检测到 systemctl 托管，清理 unit…")
                ok_cap, use_sudo, reason = can_manage_systemd_unit(ssh, log_fn=log)
                if not ok_cap:
                    return f"当前为 systemctl 托管，但无权限清理 unit: {reason}"
                unit_name = (detail or {}).get("unit") or "nginx"
                ok, msg = remove_nginx_systemd_unit(
                    ssh,
                    unit_name=unit_name,
                    use_sudo=use_sudo,
                    log_fn=log,
                )
                if not ok:
                    return f"清理 systemd unit 失败: {msg}"
                log(msg or "systemd unit 已清理")
            else:
                log("二进制托管，跳过 systemd 清理")

    _set_task_status(task, "updating_node", progress=90, step="更新节点状态")
    log("回写节点 Nginx 状态…")
    try:
        _bookkeep_node_after_uninstall(node, operator=task.operator)
        log("节点状态已更新（nginx_available=False，绑定 orphaned）")
    except Exception as exc:
        logger.exception("卸载后回写节点失败 node=%s", node.id)
        return f"文件已删除但回写节点失败: {exc}"

    return None


def _run_uninstall_batch(task_center_id, uninstall_task_ids):
    """后台串行执行批次内各节点卸载。"""
    from .models import NginxUninstallTask

    TaskCenterTask.objects.filter(pk=task_center_id).update(
        status="running",
        progress=5,
        detail="正在执行 Nginx 卸载…",
        started_at=timezone.now(),
    )

    tasks = list(
        NginxUninstallTask.objects.filter(id__in=uninstall_task_ids)
        .select_related("node", "node__credential", "operator")
        .order_by("id")
    )
    total = len(tasks)
    success_count = 0
    fail_count = 0
    done = 0
    node_blocks = []

    try:
        for task in tasks:
            if is_cancelled(task_center_id):
                NginxUninstallTask.objects.filter(
                    id__in=uninstall_task_ids,
                    status__in=[
                        "pending", "stopping", "removing_package",
                        "removing_prefix",
                        "removing_backup", "removing_extra", "removing_unit",
                        "updating_node",
                    ],
                ).update(
                    status="cancelled",
                    error_message="用户手动取消",
                    finished_at=timezone.now(),
                )
                return

            node = task.node
            hostname = node.hostname or node.ip
            options = _parse_options(task.options_json)
            stop_if_running = options.get("stop_if_running", True)
            _set_current_step(task_center_id, hostname, "卸载 Nginx")
            node_blocks.append(node_header(node.ip, node.hostname))
            _set_task_status(task, "pending", progress=5, step="连接远程")

            try:
                err = _run_one_uninstall(task, stop_if_running=stop_if_running)
                if err:
                    fail_count += 1
                    # 失败原文写入完整执行日志（返回什么记什么）
                    _append_log(task, err)
                    _set_task_status(
                        task, "failed", progress=100, step="", error=err,
                    )
                    task.finished_at = timezone.now()
                    task.save(update_fields=["finished_at", "updated_at"])
                    node_blocks.append(item_failed("Nginx 卸载", err))
                else:
                    success_count += 1
                    task.status = "success"
                    task.progress = 100
                    task.current_step = ""
                    task.finished_at = timezone.now()
                    task.save(
                        update_fields=[
                            "status", "progress", "current_step",
                            "finished_at", "updated_at",
                        ]
                    )
                    node_blocks.append(item_success("Nginx 卸载"))
            except Exception as exc:
                logger.exception("Nginx 卸载失败 node=%s", node.id)
                fail_count += 1
                err_text = str(exc)
                _append_log(task, err_text)
                _set_task_status(
                    task, "failed", progress=100, step="", error=err_text,
                )
                task.finished_at = timezone.now()
                task.save(update_fields=["finished_at", "updated_at"])
                node_blocks.append(item_failed("Nginx 卸载", err_text))

            done += 1
            _set_current_step(task_center_id, hostname, None)
            update_if_active(
                task_center_id,
                progress=int(done * 100 / total) if total else 100,
                detail=(
                    f"执行中：成功 {success_count}，失败 {fail_count}，"
                    f"已完成 {done}/{total}"
                ),
                result="\n".join(node_blocks),
            )

        if is_cancelled(task_center_id):
            return

        status = "success" if fail_count == 0 else "failed"
        finish_if_active(
            task_center_id,
            status=status,
            progress=100,
            finished_at=timezone.now(),
            detail=f"执行完成：成功 {success_count}，失败 {fail_count}，共 {total}",
            result=build_tree_result(success_count, fail_count, total, node_blocks),
        )
    finally:
        _clear_release_progress_state(task_center_id)
