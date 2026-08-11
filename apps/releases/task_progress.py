"""进程内任务进度与活结果树（供发布/同步/启停等复用）。"""
from collections import OrderedDict

from django.utils import timezone

from .models import TaskCenterTask

# 进程内精炼进度：task_center_id -> {hostname: 当前步骤文案}
_RELEASE_CURRENT_STEPS = {}
# 进程内增量结果树：task_center_id -> OrderedDict[node_key -> [{name, status, version, reason}]]
_RELEASE_LIVE_TREE = {}


def _append_task_center_log(task_center_id, line, lock=None):
    """线程安全地向 TaskCenterTask.log_output 追加一行"""

    def _do():
        tc = TaskCenterTask.objects.filter(pk=task_center_id).only("log_output").first()
        if not tc:
            return
        prev = tc.log_output or ""
        new_val = f"{prev}\n{line}" if prev else line
        TaskCenterTask.objects.filter(pk=task_center_id).update(
            log_output=new_val,
            updated_at=timezone.now(),
        )

    if lock is not None:
        with lock:
            _do()
    else:
        _do()


def _set_current_step(task_center_id, hostname, step, lock=None):
    """更新某主机当前精炼步骤；step 为 None 时清除该主机"""
    if not task_center_id:
        return

    def _do():
        bucket = _RELEASE_CURRENT_STEPS.setdefault(task_center_id, {})
        if step is None:
            bucket.pop(hostname, None)
            if not bucket:
                _RELEASE_CURRENT_STEPS.pop(task_center_id, None)
        else:
            bucket[hostname] = step

    if lock is not None:
        with lock:
            _do()
    else:
        _do()


def _format_current_steps(task_center_id):
    """将当前步骤 dict 格式化为多行文本供进度 API 返回"""
    bucket = _RELEASE_CURRENT_STEPS.get(task_center_id) or {}
    if not bucket:
        return ""
    return "\n".join(f"{host} · {text}" for host, text in sorted(bucket.items()))


def _truncate_middle(text, max_len=60):
    """路径过长时截断中间，保留头尾"""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    keep = max_len - 3
    head = keep // 2
    tail = keep - head
    return f"{text[:head]}...{text[-tail:]}"


def _release_step_label(phase, config_name=None, version=None, remote_path=None, extra=None):
    """构造进度弹窗精炼步骤：阶段 · 配置 vN → 路径"""
    if not config_name and not extra:
        return phase
    if not config_name:
        return f"{phase} · {extra}"
    ver = ""
    if version is not None and version != "":
        vs = str(version)
        if vs == "latest":
            ver = " latest"
        elif vs.startswith("v"):
            ver = f" {vs}"
        else:
            ver = f" v{vs}"
    mid = f"{config_name}{ver}"
    if remote_path:
        mid = f"{mid} → {_truncate_middle(remote_path)}"
    label = f"{phase} · {mid}"
    if extra:
        label = f"{label}（{extra}）"
    return label


def _clear_release_progress_state(task_center_id):
    """批次结束时清理进程内精炼状态"""
    if not task_center_id:
        return
    _RELEASE_CURRENT_STEPS.pop(task_center_id, None)
    _RELEASE_LIVE_TREE.pop(task_center_id, None)


def _serialize_live_tree(task_center_id):
    """将内存结果树序列化为进度树文本"""
    tree = _RELEASE_LIVE_TREE.get(task_center_id) or OrderedDict()
    lines = []
    for node_key, configs in tree.items():
        lines.append(f"[节点] {node_key}")
        for c in configs:
            name = c.get("name") or ""
            ver = c.get("version")
            ver_s = f" v{ver}" if ver is not None and ver != "" else ""
            status = c.get("status") or "running"
            if status == "running":
                lines.append(f"  [进行中] {name}")
            elif status == "success":
                lines.append(f"  [成功] {name}{ver_s}")
            else:
                reason = c.get("reason") or ""
                suffix = f" - 失败原因: {reason}" if reason else ""
                lines.append(f"  [失败] {name}{ver_s}{suffix}")
    return "\n".join(lines)


def _flush_live_result(task_center_id, lock=None):
    """把内存结果树刷入 TaskCenterTask.result"""
    if not task_center_id:
        return

    def _do():
        text = _serialize_live_tree(task_center_id)
        TaskCenterTask.objects.filter(pk=task_center_id).update(
            result=text,
            updated_at=timezone.now(),
        )

    if lock is not None:
        with lock:
            _do()
    else:
        _do()


def _live_tree_set_running(task_center_id, node_key, config_name, version=None, lock=None):
    """配置开始执行：写入 [进行中]"""
    if not task_center_id:
        return

    def _do():
        tree = _RELEASE_LIVE_TREE.setdefault(task_center_id, OrderedDict())
        configs = tree.setdefault(node_key, [])
        # 同名配置若已有进行中则覆盖，否则追加
        for c in configs:
            if c.get("name") == config_name and c.get("status") == "running":
                c["version"] = version
                break
        else:
            configs.append({
                "name": config_name,
                "status": "running",
                "version": version,
                "reason": "",
            })
        text = _serialize_live_tree(task_center_id)
        TaskCenterTask.objects.filter(pk=task_center_id).update(
            result=text, updated_at=timezone.now(),
        )

    if lock is not None:
        with lock:
            _do()
    else:
        _do()


def _live_tree_set_done(
    task_center_id, node_key, config_name, ok, version=None, reason="", lock=None,
):
    """配置执行结束：将 [进行中] 改为成功/失败"""
    if not task_center_id:
        return

    def _do():
        tree = _RELEASE_LIVE_TREE.setdefault(task_center_id, OrderedDict())
        configs = tree.setdefault(node_key, [])
        status = "success" if ok else "failed"
        updated = False
        for c in configs:
            if c.get("name") == config_name and c.get("status") == "running":
                c["status"] = status
                c["version"] = version if version is not None else c.get("version")
                c["reason"] = reason if not ok else ""
                updated = True
                break
        if not updated:
            configs.append({
                "name": config_name,
                "status": status,
                "version": version,
                "reason": reason if not ok else "",
            })
        text = _serialize_live_tree(task_center_id)
        TaskCenterTask.objects.filter(pk=task_center_id).update(
            result=text, updated_at=timezone.now(),
        )

    if lock is not None:
        with lock:
            _do()
    else:
        _do()
