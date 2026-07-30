import json
import re
import threading
import logging
from datetime import datetime

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db import OperationalError
from django.core.paginator import Paginator
from django.db.models import Max, Q
from django.utils import timezone
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse
from django.views.generic import ListView, DetailView, View

from apps.nodes.views import _get_node_credential
from apps.nodes.models import Node
from apps.configs.models import Config, ConfigNodeBinding, BindingVersion
from apps.users.permissions import (
    PermissionRequiredMixin,
    user_has_permission,
    forbidden_response,
)
from utils.ssh import (
    backup_remote_file,
    upload_file_via_sftp,
    restore_backup_file,
    remove_remote_file,
    check_remote_file_size,
    check_remote_file_md5,
    copy_remote_file,
    execute_nginx_test,
    execute_nginx_reload,
    _build_ssh_client,
)

from .models import ReleaseTask, ReleaseHistory, TaskCenterTask, generate_batch_number
from utils.pagination import PerPagePaginationMixin
from utils.setting_service import get_setting


def _release_max_workers():
    """读取发布并行 worker 数"""
    try:
        return max(1, int(get_setting("release.max_parallel_tasks", "3") or 3))
    except (TypeError, ValueError):
        return 3


def _start_release_executor(task_ids, task_center_id):
    """按系统设置并行度启动发布/回滚后台线程"""
    max_workers = _release_max_workers()
    if max_workers > 1 and len(task_ids) > 1:
        thread = threading.Thread(
            target=_run_release_tasks_parallel,
            args=(task_ids, task_center_id, max_workers),
            daemon=True,
        )
    else:
        thread = threading.Thread(
            target=_run_release_tasks,
            args=(task_ids, task_center_id),
            daemon=True,
        )
    thread.start()
    return thread

logger = logging.getLogger(__name__)

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
    from collections import OrderedDict
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
    from collections import OrderedDict

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
    from collections import OrderedDict

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


class ReleaseExecutorMixin:
    """发布执行核心逻辑 - 适配 ConfigNodeBinding"""

    def _make_task_logger(self, task, node, log_lines, task_center_id=None, log_lock=None):
        """构造单任务日志写入器（增量写 ReleaseTask + TaskCenter）"""

        def add_log(msg, milestone=False, step=None):
            """追加步骤日志；milestone 时同步精炼当前步骤"""
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] {msg}"
            log_lines.append(line)
            task.result = "\n".join(log_lines)
            task.save(update_fields=["result"])
            if task_center_id:
                _append_task_center_log(
                    task_center_id,
                    f"[{ts}] [{node.hostname}] {msg}",
                    log_lock,
                )
                if milestone:
                    _set_current_step(
                        task_center_id,
                        node.hostname,
                        step if step is not None else msg,
                        log_lock,
                    )

        return add_log

    def _mark_task_failed(self, task, action, log_lines, msg, task_center_id=None,
                          log_lock=None, node=None):
        """标记任务失败并写历史"""
        task.status = "failed"
        if log_lines:
            task.result = "\n".join(log_lines)
        elif not task.result:
            task.result = msg
        task.finished_at = datetime.now()
        task.save()
        self._record_history(task, action, task.result)
        if task_center_id and node is not None:
            _set_current_step(task_center_id, node.hostname, None, log_lock)
        return False, msg

    def _fail_task_early(self, task, action, msg):
        """前置校验失败：无 SSH、无日志列表"""
        task.status = "failed"
        task.result = msg
        task.finished_at = datetime.now()
        task.save()
        self._record_history(task, action, task.result)
        return False, msg

    def _deploy_release_config(
        self, task, action, step_kwargs, task_center_id=None, log_lock=None,
        seed_log_lines=None, note_ssh_reuse=False,
    ):
        """备份+上传+nginx -t（不 reload）；成功返回 pending 字典"""
        node = task.node
        config = task.config
        content = task.content_to_publish if task.content_to_publish else ""
        remote_path = task.remote_path or (task.binding.remote_path if task.binding else "")
        log_lines = list(seed_log_lines or [])
        config_label = config.name
        add_log = self._make_task_logger(
            task, node, log_lines, task_center_id=task_center_id, log_lock=log_lock,
        )

        def _fail(msg):
            """标记失败并记录历史"""
            return self._mark_task_failed(
                task, action, log_lines, msg,
                task_center_id=task_center_id, log_lock=log_lock, node=node,
            )

        if not remote_path:
            return _fail("未指定远程路径")

        task.status = "running"
        if not task.started_at:
            task.started_at = datetime.now()
        task.save(update_fields=["status", "started_at"])

        if note_ssh_reuse:
            add_log(
                "复用本节点 SSH 会话",
                milestone=True,
                step=_release_step_label("已连接", config_label, task.publish_version, remote_path),
            )
        version_label = f"v{task.publish_version}" if task.publish_version else "latest"
        add_log(
            f"开始发布: {config.name} {version_label} → {node.hostname}",
            milestone=True,
            step=_release_step_label("开始发布", config_label, task.publish_version, remote_path),
        )
        add_log(f"目标路径: {remote_path}")

        if not content or not content.strip():
            add_log(
                "配置内容为空，中止发布",
                milestone=True,
                step=_release_step_label("内容为空", config_label, task.publish_version, remote_path),
            )
            return _fail(f"配置 {config.name} {version_label} 内容为空，无法发布")

        # 备份（远程文件不存在时跳过，支持首次发布；按 hostname 分子目录）
        add_log(
            "正在备份原配置...",
            milestone=True,
            step=_release_step_label("备份中", config_label, task.publish_version, remote_path),
        )
        success, backup_result = backup_remote_file(
            file_path=remote_path,
            hostname=node.hostname,
            backup_dir=get_setting(
                "release.backup_dir",
                "/opt/app/mascloud/ansible/mngxops",
            ),
            **step_kwargs,
        )
        if success:
            if backup_result:
                add_log(
                    f"备份成功: {backup_result}",
                    milestone=True,
                    step=_release_step_label("备份完成", config_label, task.publish_version, remote_path),
                )
                backup_size_ok, backup_size_msg = check_remote_file_size(
                    file_path=backup_result, **step_kwargs,
                )
                add_log(f"备份文件大小: {backup_size_msg}")
                if not backup_size_ok:
                    add_log("警告: 备份文件为空，回滚将无法恢复原配置")
            else:
                add_log(
                    "远程文件不存在，跳过备份（首次发布）",
                    milestone=True,
                    step=_release_step_label("跳过备份", config_label, task.publish_version, remote_path),
                )
        else:
            add_log(
                f"备份失败: {backup_result}",
                milestone=True,
                step=_release_step_label("备份失败", config_label, task.publish_version, remote_path),
            )
            return _fail(f"备份失败: {backup_result}")

        # 上传到临时中转文件（带 task.id 避免同名并发覆盖）
        tmp_path = f"/tmp/{remote_path.split('/')[-1]}.mngxops_tmp.{task.id}"

        def _cleanup_tmp():
            """尽力删除远程中转文件，失败仅记日志不阻断流程"""
            ok, msg = remove_remote_file(file_path=tmp_path, **step_kwargs)
            if ok:
                add_log(f"已清理临时文件: {tmp_path}")
            else:
                add_log(f"清理临时文件失败（可忽略）: {tmp_path} ({msg})")

        add_log(
            "正在上传到临时中转文件...",
            milestone=True,
            step=_release_step_label("上传中", config_label, task.publish_version, remote_path),
        )
        success, upload_result = upload_file_via_sftp(
            remote_path=tmp_path, content=content, **step_kwargs,
        )
        if not success:
            add_log(
                f"上传到临时中转文件失败: {upload_result}",
                milestone=True,
                step=_release_step_label("上传失败", config_label, task.publish_version, remote_path),
            )
            return _fail(f"上传到临时中转文件失败: {upload_result}")

        add_log(f"已上传到临时中转文件 {tmp_path}，检查文件大小...")
        size_ok, size_msg = check_remote_file_size(file_path=tmp_path, **step_kwargs)
        add_log(f"临时文件大小: {size_msg}")
        if not size_ok:
            add_log(
                "临时中转文件为空，中止发布",
                milestone=True,
                step=_release_step_label("上传为空", config_label, task.publish_version, remote_path),
            )
            _cleanup_tmp()
            return _fail(f"临时中转文件为空: {size_msg}")

        # 复制到目标路径
        add_log(f"从临时中转文件复制到目标路径 {remote_path} ...")
        copy_ok, copy_msg = copy_remote_file(
            src_path=tmp_path, dst_path=remote_path, **step_kwargs,
        )
        if not copy_ok:
            add_log(
                f"复制失败: {copy_msg}",
                milestone=True,
                step=_release_step_label("复制失败", config_label, task.publish_version, remote_path),
            )
            add_log("正在回滚备份...")
            self._rollback_backup(backup_result, remote_path, step_kwargs, log_lines, add_log)
            _cleanup_tmp()
            return _fail(f"复制失败: {copy_msg}")

        # 校验
        add_log("验证目标文件大小...")
        target_ok, target_msg = check_remote_file_size(
            file_path=remote_path, **step_kwargs,
        )
        add_log(f"目标文件大小: {target_msg}")
        add_log("校验文件 md5...")
        tmp_md5_ok, tmp_md5 = check_remote_file_md5(file_path=tmp_path, **step_kwargs)
        target_md5_ok, target_md5 = check_remote_file_md5(
            file_path=remote_path, **step_kwargs,
        )
        add_log(f"临时文件 md5: {tmp_md5}")
        add_log(f"目标 md5: {target_md5}")
        if tmp_md5_ok and target_md5_ok and tmp_md5 == target_md5:
            add_log("md5 一致 ✓")
        else:
            add_log("md5 不一致 ✗")

        if not target_ok:
            add_log(
                "目标文件为空，正在回滚备份...",
                milestone=True,
                step=_release_step_label("校验失败", config_label, task.publish_version, remote_path),
            )
            self._rollback_backup(backup_result, remote_path, step_kwargs, log_lines, add_log)
            _cleanup_tmp()
            return _fail(f"目标文件为空: {target_msg}")

        # 中转已落地目标，清理临时文件后再继续 nginx -t
        _cleanup_tmp()

        add_log(
            "上传成功",
            milestone=True,
            step=_release_step_label("上传完成", config_label, task.publish_version, remote_path),
        )

        # nginx -t（本配置校验；reload 延后到节点级）
        add_log(
            "正在执行 nginx -t ...",
            milestone=True,
            step=_release_step_label("nginx -t", config_label, task.publish_version, remote_path),
        )
        nginx_path = node.nginx_path or None
        success, test_output = execute_nginx_test(
            config_path=remote_path, nginx_path=nginx_path, **step_kwargs,
        )
        add_log(test_output)
        if not success:
            add_log(
                "nginx -t 失败，正在回滚备份...",
                milestone=True,
                step=_release_step_label("nginx -t 失败", config_label, task.publish_version, remote_path),
            )
            self._rollback_backup(backup_result, remote_path, step_kwargs, log_lines, add_log)
            return _fail(f"nginx -t 失败: {test_output}")

        add_log(
            "nginx -t 通过，等待本节点统一 reload",
            milestone=True,
            step=_release_step_label("待 reload", config_label, task.publish_version, remote_path),
        )
        pending = {
            "task": task,
            "action": action,
            "backup_result": backup_result,
            "remote_path": remote_path,
            "target_md5": target_md5,
            "log_lines": log_lines,
            "add_log": add_log,
            "version_label": version_label,
            "publish_version": task.publish_version,
            "config_label": config_label,
        }
        return True, pending

    def _rollback_pending_items(self, pending_items, step_kwargs, reason):
        """回滚本节点本批次已上传但未生效的配置"""
        for item in pending_items:
            add_log = item["add_log"]
            add_log(
                f"因{reason}，回滚本配置...",
                milestone=True,
                step=_release_step_label(
                    "回滚",
                    item["config_label"],
                    item.get("publish_version"),
                    item["remote_path"],
                ),
            )
            self._rollback_backup(
                item["backup_result"],
                item["remote_path"],
                step_kwargs,
                item["log_lines"],
                add_log,
            )
            task = item["task"]
            task.status = "failed"
            task.result = "\n".join(item["log_lines"])
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, item["action"], task.result)

    def _finalize_node_reload(
        self, node, pending_items, step_kwargs, task_center_id=None, log_lock=None,
    ):
        """本节点统一 reload；成功则全部标成功，失败则回滚全部 pending"""
        if not pending_items:
            return True

        nginx_path = node.nginx_path or None
        reload_step = _release_step_label(
            "本节点统一 reload", extra=f"{len(pending_items)} 个配置",
        )
        # 以首个 pending 写里程碑，并广播到各任务日志
        lead = pending_items[0]
        lead["add_log"](
            "本节点全部配置已通过 nginx -t，正在统一 reload...",
            milestone=True,
            step=reload_step,
        )
        for item in pending_items[1:]:
            item["add_log"]("本节点全部配置已通过 nginx -t，正在统一 reload...")

        success, reload_output = execute_nginx_reload(
            nginx_path=nginx_path, **step_kwargs,
        )
        for item in pending_items:
            item["add_log"](reload_output)

        if success:
            for item in pending_items:
                task = item["task"]
                item["add_log"](
                    "发布成功!",
                    milestone=True,
                    step=_release_step_label(
                        "完成",
                        item["config_label"],
                        item.get("publish_version"),
                        item["remote_path"],
                    ),
                )
                task.status = "success"
                task.result = "\n".join(item["log_lines"])
                task.finished_at = datetime.now()
                task.save()
                self._on_release_success(task, item["target_md5"])
                self._record_history(task, item["action"], task.result)
            if task_center_id:
                _set_current_step(task_center_id, node.hostname, None, log_lock)
            return True

        lead["add_log"](
            "reload 失败，正在回滚本节点本批次全部配置...",
            milestone=True,
            step=_release_step_label("reload 失败", extra=f"{len(pending_items)} 个配置"),
        )
        for item in pending_items[1:]:
            item["add_log"]("reload 失败，正在回滚本节点本批次全部配置...")
        self._rollback_pending_items(pending_items, step_kwargs, "reload 失败")
        if task_center_id:
            _set_current_step(task_center_id, node.hostname, None, log_lock)
        return False

    def _skip_remaining_tasks(self, tasks, action, reason):
        """节点批次中断后，将未执行任务标为失败"""
        for task in tasks:
            if task.status in ("success", "failed"):
                continue
            self._fail_task_early(task, action, reason)

    def _execute_node_release_batch(
        self, tasks, action, task_center_id=None, log_lock=None, on_task_done=None,
    ):
        """同节点批量发布：共用一条 SSH，全部 -t 通过后统一 reload

        on_task_done(task, ok, reason) 可选，用于刷新进度树。
        返回 [(task, ok), ...]
        """
        results = []
        if not tasks:
            return results

        node = tasks[0].node

        # 前置：锁定 / 凭证
        if node.is_locked:
            msg = f"节点 {node.hostname} 已锁定，无法执行发布"
            for task in tasks:
                self._fail_task_early(task, action, msg)
                if on_task_done:
                    on_task_done(task, False, msg)
                results.append((task, False))
            return results

        credential = _get_node_credential(node)
        if not credential:
            msg = f"节点 {node.hostname} 未配置 SSH 凭证"
            for task in tasks:
                self._fail_task_early(task, action, msg)
                if on_task_done:
                    on_task_done(task, False, msg)
                results.append((task, False))
            return results

        kwargs = {
            "host": node.ip,
            "port": node.port,
            "username": credential.username,
        }
        if credential.auth_type == "password":
            kwargs["password"] = credential.get_password()
        else:
            kwargs["private_key"] = credential.get_private_key()

        ssh = None
        pending_items = []
        step_kwargs = None
        try:
            # 建连日志写到第一个任务
            first = tasks[0]
            first_logs = []
            first_add = self._make_task_logger(
                first, node, first_logs,
                task_center_id=task_center_id, log_lock=log_lock,
            )
            first.status = "running"
            first.started_at = datetime.now()
            first.save(update_fields=["status", "started_at"])
            first_add(
                "正在建立 SSH 连接...",
                milestone=True,
                step=_release_step_label("连接 SSH"),
            )
            try:
                ssh = _build_ssh_client(**kwargs)
            except Exception as e:
                first_add(
                    f"SSH 连接失败: {e}",
                    milestone=True,
                    step=_release_step_label("连接失败"),
                )
                self._mark_task_failed(
                    first, action, first_logs, f"SSH 连接失败: {e}",
                    task_center_id=task_center_id, log_lock=log_lock, node=node,
                )
                if on_task_done:
                    on_task_done(first, False, f"SSH 连接失败: {e}")
                results.append((first, False))
                self._skip_remaining_tasks(
                    tasks[1:], action, f"SSH 连接失败，跳过本节点其余配置: {e}",
                )
                for task in tasks[1:]:
                    if on_task_done:
                        on_task_done(task, False, f"SSH 连接失败: {e}")
                    results.append((task, False))
                return results

            first_add(
                "SSH 连接成功 ✓",
                milestone=True,
                step=_release_step_label("已连接"),
            )
            # 将建连日志并入 first 的 result，供后续 deploy 继续追加
            first.result = "\n".join(first_logs)
            first.save(update_fields=["result"])
            step_kwargs = {**kwargs, "client": ssh}

            abort_reason = None
            for idx, task in enumerate(tasks):
                if abort_reason:
                    self._fail_task_early(task, action, abort_reason)
                    if on_task_done:
                        on_task_done(task, False, abort_reason)
                    results.append((task, False))
                    continue

                ok, payload = self._deploy_release_config(
                    task, action, step_kwargs,
                    task_center_id=task_center_id, log_lock=log_lock,
                    seed_log_lines=first_logs if idx == 0 else None,
                    note_ssh_reuse=(idx > 0),
                )

                if ok:
                    pending_items.append(payload)
                    # 尚未终态，进度树保持进行中，等节点统一 reload 后再 done
                else:
                    abort_reason = (
                        f"本节点配置 {task.config.name} 发布失败，"
                        f"已中止并回滚本批次未生效配置"
                    )
                    # 先回滚此前已上传 pending，再标记当前失败（进度树按上传顺序收尾）
                    if pending_items:
                        self._rollback_pending_items(
                            pending_items, step_kwargs,
                            f"后续配置失败({task.config.name})",
                        )
                        for item in pending_items:
                            if on_task_done:
                                on_task_done(item["task"], False, abort_reason)
                            results.append((item["task"], False))
                        pending_items = []
                    reason = (task.result or "").split("\n")[-1]
                    if on_task_done:
                        on_task_done(task, False, reason)
                    results.append((task, False))
                    continue

            if pending_items:
                reload_ok = self._finalize_node_reload(
                    node, pending_items, step_kwargs,
                    task_center_id=task_center_id, log_lock=log_lock,
                )
                for item in pending_items:
                    task = item["task"]
                    ok = reload_ok and task.status == "success"
                    reason = "" if ok else (task.result or "").split("\n")[-1]
                    if on_task_done:
                        on_task_done(task, ok, reason)
                    results.append((task, ok))

            return results
        finally:
            if ssh is not None:
                try:
                    ssh.close()
                except Exception:
                    pass

    def _execute_release(self, task, action, task_center_id=None, log_lock=None):
        """执行单条发布/回滚（兼容入口：走同节点批次逻辑）"""
        batch_results = self._execute_node_release_batch(
            [task], action,
            task_center_id=task_center_id, log_lock=log_lock,
        )
        if not batch_results:
            return False, "无任务"
        task, ok = batch_results[0]
        version_label = f"v{task.publish_version}" if task.publish_version else "latest"
        if ok:
            return True, f"配置 {task.config.name} {version_label} 发布到 {task.node.hostname} 成功"
        return False, f"配置 {task.config.name} {version_label} 发布到 {task.node.hostname} 失败"

    def _on_release_success(self, task, remote_md5):
        """发布成功后回写绑定状态"""
        binding = task.binding
        if not binding:
            return
        binding.synced_version = task.publish_version or binding.current_version
        binding.remote_content_hash = remote_md5
        binding.sync_status = "synced"
        binding.last_sync_time = timezone.now()
        binding.save(update_fields=[
            "synced_version", "remote_content_hash", "sync_status", "last_sync_time",
        ])

    def _record_history(self, task, action, result):
        """写入发布操作历史"""
        ReleaseHistory.objects.create(
            release_task=task,
            node=task.node,
            config=task.config,
            version=task.publish_version or 0,
            operator=task.operator,
            action=action,
            result=result,
        )

    def _rollback_backup(self, backup_result, config_file_path, kwargs, log_lines, add_log=None):
        """发布失败回滚：有备份则还原，无备份（首次发布）则删除新文件"""
        def _note(msg):
            if add_log:
                add_log(msg)
            else:
                log_lines.append(msg)

        if not backup_result:
            ok, msg = remove_remote_file(file_path=config_file_path, **kwargs)
            if ok:
                _note("无原备份，已清理新上传文件")
            else:
                _note(f"无原备份，清理新文件失败: {msg}")
            return
        backup_size_ok, backup_size_msg = check_remote_file_size(
            file_path=backup_result, **kwargs,
        )
        if not backup_size_ok:
            _note("警告: 备份文件为空，跳过回滚")
            return
        rollback_ok, rollback_msg = restore_backup_file(
            backup_path=backup_result, original_path=config_file_path, **kwargs,
        )
        if rollback_ok:
            _note("回滚完成")
        else:
            _note(f"回滚失败: {rollback_msg}")


class ReleaseCreateAPIView(LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View):
    """发布任务创建 API — 处理 JSON 格式的发布任务创建请求"""
    permission_resource = "releases"
    permission_action = "create"

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "请求数据格式错误"}, status=400)

        bindings_data = data.get("bindings", [])
        auto_execute = data.get("auto_execute", False)

        if not bindings_data:
            return JsonResponse({"success": False, "message": "请至少选择一个配置绑定"}, status=400)

        batch_number = generate_batch_number()
        task_ids = []

        for item in bindings_data:
            binding_id = item.get("binding_id", 0)
            version = item.get("version")

            try:
                binding = ConfigNodeBinding.objects.select_related("node", "config").get(pk=binding_id)
            except ConfigNodeBinding.DoesNotExist:
                continue

            if binding.node.is_locked or binding.node.is_deleted:
                continue

            publish_version = version if version else binding.current_version

            task = ReleaseTask.objects.create(
                batch_number=batch_number,
                binding=binding,
                config=binding.config,
                node=binding.node,
                version=binding.versions.filter(version=publish_version).first(),
                publish_version=publish_version,
                remote_path=binding.remote_path,
                operator=request.user,
                status="pending",
            )
            task_ids.append(task.id)

        if not task_ids:
            return JsonResponse({"success": False, "message": "未找到可发布的配置绑定"}, status=400)

        response_data = {
            "success": True,
            "batch_number": batch_number,
            "task_count": len(task_ids),
            "message": f"发布任务已创建，批次号: {batch_number}，共 {len(task_ids)} 个任务",
        }

        if auto_execute:
            from apps.releases.task_result import targets_from_release_tasks
            targets = targets_from_release_tasks(task_ids)
            task_center = TaskCenterTask.objects.create(
                operation_type="release_publish",
                status="running",
                source_batch=batch_number,
                detail=f"执行中：成功 0，失败 0，共 {len(task_ids)}",
                progress=0,
                started_at=timezone.now(),
                trigger_user=request.user,
                **targets,
            )

            _start_release_executor(task_ids, task_center.id)

            response_data["task_center_id"] = task_center.id
            response_data["async"] = True

        return JsonResponse(response_data)


class ReleaseListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """发布历史 - 按批次内节点分页，批次→节点→配置树形展示"""
    model = ReleaseTask
    template_name = "releases/list.html"
    context_object_name = "tasks"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        """按搜索条件过滤发布任务"""
        queryset = (
            super()
            .get_queryset()
            .select_related("node", "config", "binding", "operator")
            .prefetch_related("node__groups", "binding__versions")
        )
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")
        batch = self.request.GET.get("batch", "")
        node_ip = self.request.GET.get("node_ip", "")
        if search:
            terms = [t.strip() for t in search.split(",") if t.strip()]
            for term in terms:
                queryset = queryset.filter(
                    Q(config__name__icontains=term)
                    | Q(node__hostname__icontains=term)
                    | Q(batch_number__icontains=term)
                    | Q(operator__username__icontains=term)
                )
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if batch:
            queryset = queryset.filter(batch_number__icontains=batch)
        if node_ip:
            queryset = queryset.filter(node__ip__icontains=node_ip)
        return queryset

    def paginate_queryset(self, queryset, page_size):
        """按 batch_number 批次分页，再加载本页全部配置任务"""
        batches = list(
            queryset.values("batch_number")
            .annotate(latest=Max("created_at"))
            .order_by("-latest")
        )
        paginator = Paginator(batches, page_size)
        page_number = self.request.GET.get("page") or 1
        page = paginator.get_page(page_number)
        self._page_batches = [str(b["batch_number"] or "") for b in page.object_list]

        if not self._page_batches:
            return (paginator, page, [], page.has_other_pages())

        tasks = list(
            queryset.filter(batch_number__in=self._page_batches)
            .select_related("node", "config", "binding", "operator")
            .prefetch_related("node__groups")
            .order_by("-created_at")
        )
        return (paginator, page, tasks, page.has_other_pages())

    def get_context_data(self, **kwargs):
        """组装本页批次→节点→任务树，供统一表格渲染"""
        from collections import OrderedDict

        context = super().get_context_data(**kwargs)
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")
        batch = self.request.GET.get("batch", "")
        node_ip = self.request.GET.get("node_ip", "")
        context["search"] = search
        context["status_filter"] = status_filter
        context["batch_filter"] = batch
        context["node_ip_filter"] = node_ip
        context["status_choices"] = ReleaseTask.STATUS_CHOICES

        # 先按本页批次顺序建空组，再填入任务，保证分页顺序
        batch_groups = OrderedDict()
        for batch_key in getattr(self, "_page_batches", []):
            batch_groups[batch_key] = {
                "batch_number": batch_key,
                "created_at": None,
                "operator": "-",
                "total": 0,
                "success": 0,
                "failed": 0,
                "other": 0,
                "nodes": OrderedDict(),
            }

        for task in context["tasks"]:
            batch_key = str(task.batch_number or "")
            if batch_key not in batch_groups:
                continue
            batch_data = batch_groups[batch_key]
            if batch_data["created_at"] is None:
                batch_data["created_at"] = task.created_at
                batch_data["operator"] = (
                    task.operator.username if task.operator else "-"
                )
            node_id = int(task.node_id)
            if node_id not in batch_data["nodes"]:
                batch_data["nodes"][node_id] = {
                    "node": task.node,
                    "tasks": [],
                }
            batch_data["nodes"][node_id]["tasks"].append(task)
            batch_data["total"] += 1
            if task.status == "success":
                batch_data["success"] += 1
            elif task.status == "failed":
                batch_data["failed"] += 1
            else:
                batch_data["other"] += 1

        # 去掉本页无任务的空批次（异常兜底）
        context["batch_groups"] = OrderedDict(
            (k, v) for k, v in batch_groups.items() if v["total"] > 0
        )
        context["expand_all_nodes"] = bool(search or status_filter or batch or node_ip)
        context["has_any_filter"] = bool(
            search or status_filter or context["batch_filter"] or context["node_ip_filter"]
        )
        # 本页各任务可回滚目标版本（publish_version 的上一版），供明细弹窗展示
        for task in context["tasks"]:
            prev_ver = None
            if task.binding_id and task.publish_version is not None:
                for ver in task.binding.versions.all():
                    if ver.version < task.publish_version:
                        if prev_ver is None or ver.version > prev_ver:
                            prev_ver = ver.version
            task.rollback_target_version = prev_ver
        return context


class TaskCenterListView(LoginRequiredMixin, PerPagePaginationMixin, ListView):
    model = TaskCenterTask
    template_name = "releases/task_center.html"
    context_object_name = "tasks"
    paginate_by = 15
    ordering = ["-created_at"]

    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(request.user, "releases", "read")
        self.can_read_node_tasks = user_has_permission(request.user, "nodes", "update")
        if not (self.can_read_release_tasks or self.can_read_node_tasks):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset().select_related("trigger_user")
        # 仅有 nodes.update 时与详情可见范围对齐（本人触发的批量测/配置同步）
        if not self.can_read_release_tasks:
            queryset = queryset.filter(
                operation_type__in=["node_batch_test", "config_batch_sync"],
                trigger_user=self.request.user,
            )
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")
        operation_type = self.request.GET.get("operation_type", "")
        if search:
            tags = [t.strip() for t in search.replace("，", ",").split(",") if t.strip()]
            for tag in tags:
                queryset = queryset.filter(
                    Q(source_batch__icontains=tag)
                    | Q(target_hostnames__icontains=tag)
                    | Q(target_ips__icontains=tag)
                )
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if operation_type:
            queryset = queryset.filter(operation_type=operation_type)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from apps.releases.task_result import format_task_center_summary

        # 为列表行注入格式化摘要（目标 + 结果）
        for task in context.get("tasks") or []:
            primary, secondary = format_task_center_summary(task)
            task.summary_primary = primary
            task.summary_secondary = secondary

        context["search"] = self.request.GET.get("search", "")
        context["status_filter"] = self.request.GET.get("status", "")
        context["operation_type_filter"] = self.request.GET.get("operation_type", "")
        context["status_choices"] = TaskCenterTask.STATUS_CHOICES
        # 筛选下拉仅展示实际会创建的任务类型（不含未启用的 discover/drift/glob）
        context["operation_type_choices"] = [
            c for c in TaskCenterTask.OPERATION_TYPE_CHOICES
            if c[0] not in ("config_discover", "config_drift_check", "config_glob_preview")
        ]
        context["has_any_filter"] = bool(
            context["search"] or context["status_filter"] or context["operation_type_filter"]
        )
        return context


class TaskCenterDetailView(LoginRequiredMixin, DetailView):
    """任务中心详情 - 按节点→配置树形展示执行结果"""
    model = TaskCenterTask
    template_name = "releases/task_detail.html"
    context_object_name = "task"

    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(request.user, "releases", "read")
        self.can_read_node_tasks = user_has_permission(request.user, "nodes", "update")
        if not (self.can_read_release_tasks or self.can_read_node_tasks):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.can_read_release_tasks:
            return queryset
        return queryset.filter(
            operation_type__in=["node_batch_test", "config_batch_sync"],
            trigger_user=self.request.user,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        task = self.object
        result_text = (task.result or "").strip()
        op = task.operation_type
        is_release_type = op in ("release_publish", "release_rollback")

        # 解析目标节点和目标配置/凭证
        target_nodes = []
        target_configs = []
        if task.target_hostnames:
            ips = (task.target_ips or "").split(",")
            hostnames = task.target_hostnames.split(",")
            seen = set()
            for i, hn_raw in enumerate(hostnames):
                hn = hn_raw.strip()
                if not hn or hn in seen:
                    continue
                seen.add(hn)
                ip = ips[i].strip() if i < len(ips) else ""
                target_nodes.append(f"{hn}({ip})" if ip else hn)
        if task.target_configs:
            configs_raw = task.target_configs.split(",")
            target_configs = [c.strip() for c in configs_raw if c.strip()]
            seen_c = set()
            target_configs = [c for c in target_configs if not (c in seen_c or seen_c.add(c))]

        context["target_nodes"] = target_nodes[:50]
        context["target_configs"] = target_configs[:50]
        context["target_configs_count"] = len(target_configs)
        context["is_release_type"] = is_release_type
        # 配置同步详情默认展开结果树，便于查看新建/更新/删除/跳过明细
        context["is_config_sync_type"] = op == "config_batch_sync"
        context["target_configs_label"] = (
            "目标凭证" if op == "credential_enable_test" else "目标配置"
        )

        # 解析结果树（按节点分组 + 成功/失败明细）
        result_tree = []
        success_total = 0
        failed_total = 0

        if result_text:
            current_node = None
            for raw in result_text.splitlines():
                stripped = raw.strip()
                if not stripped:
                    continue
                if stripped.startswith("[节点] "):
                    node_text = stripped[len("[节点] "):]
                    if current_node:
                        result_tree.append(current_node)
                    current_node = {
                        "name": node_text,
                        "configs": [],
                        "success": 0,
                        "failed": 0,
                    }
                elif raw.startswith("  [成功]") and current_node is not None:
                    raw_name = raw[len("  [成功] "):].strip()
                    name = re.sub(r'\s+v(\d+).*', r' (V\1)', raw_name)
                    search_name = re.sub(r'\s+v\d+.*', '', raw_name).strip()
                    current_node["configs"].append({
                        "name": name, "search_name": search_name, "status": "success",
                    })
                    current_node["success"] += 1
                    success_total += 1
                elif raw.startswith("  [失败]") and current_node is not None:
                    raw_name = raw[len("  [失败] "):].strip()
                    name = re.sub(r'\s+v(\d+).*', r' (V\1)', raw_name)
                    search_name = re.sub(r'\s+v\d+.*', '', raw_name).strip()
                    current_node["configs"].append({
                        "name": name, "search_name": search_name, "status": "failed",
                    })
                    current_node["failed"] += 1
                    failed_total += 1

            if current_node:
                result_tree.append(current_node)

        # 失败节点排前面
        result_tree.sort(key=lambda n: (n["failed"] == 0, n["name"]))

        # 为每个结果树节点附加 IP（从目标主机列表匹配）
        host_to_ip = {}
        if task.target_hostnames and task.target_ips:
            hostnames = task.target_hostnames.split(",")
            ips = task.target_ips.split(",")
            for i in range(min(len(hostnames), len(ips))):
                hn = hostnames[i].strip()
                ip = ips[i].strip()
                if hn and ip:
                    host_to_ip[hn] = ip
        for node in result_tree:
            node_name = node["name"]
            # 从 "IP (hostname)" 格式提取纯 IP
            ip_match = re.match(r'^([\d.]+)\s*\(', node_name)
            if ip_match:
                node["node_ip"] = ip_match.group(1)
            elif node_name in host_to_ip:
                node["node_ip"] = host_to_ip[node_name]
            else:
                node["node_ip"] = node_name
            # 提取主机名（括号内文本）
            hn_match = re.search(r'\(([^)]+)\)', node_name)
            if hn_match:
                node["node_hostname"] = hn_match.group(1)
            elif node_name in host_to_ip:
                node["node_hostname"] = node_name
            else:
                node["node_hostname"] = node.get("node_ip", node_name)

        # 计算执行耗时
        duration = ""
        if task.started_at and task.finished_at:
            delta = (task.finished_at - task.started_at).total_seconds()
            if delta >= 60:
                duration = f"{delta / 60:.1f} 分钟"
            else:
                duration = f"{delta:.1f} 秒"

        # Nginx 升级：关联升级任务详情入口
        upgrade_task = None
        if op == "nginx_upgrade":
            try:
                from apps.upgrade.models import NginxUpgradeTask
                upgrade_task = (
                    NginxUpgradeTask.objects.filter(task_center_id=task.id)
                    .select_related("node")
                    .first()
                )
            except Exception:
                upgrade_task = None

        # 系统信息 / 版本检测：特化展示
        system_info_rows = None
        nginx_version_text = None
        if op == "node_system_info" and result_text:
            try:
                import json as _json
                data = _json.loads(result_text)
                if isinstance(data, dict):
                    system_info_rows = [
                        {"key": k, "value": v} for k, v in data.items()
                    ]
            except (ValueError, TypeError):
                system_info_rows = None
        elif op == "node_nginx_version" and result_text:
            nginx_version_text = result_text

        context["result_tree"] = result_tree
        context["result_summary"] = {"success": success_total, "failed": failed_total}
        context["execution_duration"] = duration
        context["upgrade_task"] = upgrade_task
        context["system_info_rows"] = system_info_rows
        context["nginx_version_text"] = nginx_version_text
        return context


class ReleaseDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = ReleaseTask
    template_name = "releases/detail.html"
    context_object_name = "task"
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        return super().get_queryset().select_related(
            "node", "config", "binding", "operator", "version",
        )

    def get_context_data(self, **kwargs):
        """组装操作记录，并为历史版本号解析 BindingVersion.id 供预览"""
        context = super().get_context_data(**kwargs)
        histories = list(
            self.object.history.all().select_related("node", "config", "operator")
        )
        version_id_map = {}
        if self.object.binding_id:
            version_id_map = dict(
                BindingVersion.objects.filter(binding_id=self.object.binding_id)
                .values_list("version", "id")
            )
        for h in histories:
            # 供模板挂 version-preview-link
            h.preview_version_id = version_id_map.get(h.version)
        context["histories"] = histories
        return context


class ReleaseRollbackView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "update"
    # 仅成功或失败的发布允许人工回滚
    ROLLBACK_ALLOWED_STATUSES = ("success", "failed")

    def get(self, request, pk):
        from django.core.paginator import Paginator
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "binding", "operator", "version"), pk=pk,
        )
        if task.status not in self.ROLLBACK_ALLOWED_STATUSES:
            messages.error(request, "仅成功或失败的发布可回滚")
            return redirect("releases:detail", pk=task.pk)
        if task.node.is_deleted:
            messages.error(request, f"节点 {task.node.hostname} 已删除，无法回滚")
            return redirect("releases:detail", pk=task.pk)
        binding = task.binding
        versions = []
        if binding:
            versions = binding.versions.select_related("created_by").order_by("-version")
        paginator = Paginator(versions, 15)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)
        return render(request, "releases/rollback.html", {
            "task": task, "config": task.config, "page_obj": page_obj,
        })

    def post(self, request, pk):
        """创建回滚任务并立即异步执行（与批量回滚一致）"""
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "binding", "operator"), pk=pk,
        )
        if task.status not in self.ROLLBACK_ALLOWED_STATUSES:
            msg = "仅成功或失败的发布可回滚"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("releases:detail", pk=task.pk)
        if task.node.is_locked:
            msg = f"节点 {task.node.hostname} 已锁定，无法回滚"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("releases:rollback", pk=task.pk)
        if task.node.is_deleted:
            msg = f"节点 {task.node.hostname} 已删除，无法回滚"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("releases:detail", pk=task.pk)

        version_id = request.POST.get("version_id")
        if not version_id:
            msg = "请选择要回滚的版本"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("releases:rollback", pk=task.pk)

        version = get_object_or_404(BindingVersion, pk=version_id, binding=task.binding)
        batch_number = generate_batch_number()
        new_task = ReleaseTask.objects.create(
            binding=task.binding,
            node=task.node,
            config=task.config,
            version=version,
            publish_version=version.version,
            remote_path=task.remote_path or (task.binding.remote_path if task.binding else ""),
            operator=request.user,
            status="pending",
            batch_number=batch_number,
        )

        from apps.releases.task_result import targets_from_release_tasks
        targets = targets_from_release_tasks([new_task.id])
        task_center = TaskCenterTask.objects.create(
            operation_type="release_rollback",
            status="running",
            source_batch=batch_number,
            detail=f"回滚：{task.config.name} → {task.node.hostname} v{version.version}",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
            **targets,
        )
        _start_release_executor([new_task.id], task_center.id)

        if is_ajax:
            return JsonResponse({
                "success": True,
                "batch_number": batch_number,
                "task_center_id": task_center.id,
                "message": f"回滚已开始，批次号: {batch_number}",
            })

        messages.success(request, f"回滚已开始，批次号: {batch_number}")
        return redirect("releases:task_center_detail", pk=task_center.id)

class ReleaseCenterView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """发布中心 - 节点为主维度选择 + 配置绑定展开（数据通过 AJAX 加载）"""
    model = ReleaseTask
    template_name = "releases/center.html"
    context_object_name = "tasks"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        # 数据由前端 AJAX 加载，服务端无需查询
        return ReleaseTask.objects.none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["pre_node_id"] = self.request.GET.get("node_id", "")
        context["pre_binding_id"] = self.request.GET.get("binding_id", "")
        context["environment_choices"] = Node.ENV_CHOICES
        return context


def _release_action_from_task_center(task_center_id):
    """根据 TaskCenter 操作类型决定历史动作（publish/rollback）"""
    if not task_center_id:
        return "publish"
    tc = TaskCenterTask.objects.filter(pk=task_center_id).only("operation_type").first()
    if tc and tc.operation_type == "release_rollback":
        return "rollback"
    return "publish"


def _run_release_tasks(task_ids, task_center_id=None):
    """异步执行发布任务（顺序模式）：按节点分组，同节点共用 SSH 并统一 reload"""
    executor = ReleaseExecutorMixin()
    total = len(task_ids)
    success = 0
    failed = 0
    detail_lines = []
    node_results = {}
    log_lock = threading.Lock()
    action = _release_action_from_task_center(task_center_id)

    if task_center_id:
        _clear_release_progress_state(task_center_id)
        TaskCenterTask.objects.filter(pk=task_center_id).update(
            status="running", started_at=timezone.now(), progress=0,
            log_output="", result="",
        )

    node_tasks = {}
    for task_id in task_ids:
        try:
            task = ReleaseTask.objects.select_related(
                "node", "config", "binding", "operator",
            ).get(pk=task_id)
            node_key = f"{task.node.ip} ({task.node.hostname})"
            if node_key not in node_tasks:
                node_tasks[node_key] = []
            node_tasks[node_key].append(task)
        except ReleaseTask.DoesNotExist:
            failed += 1
            detail_lines.append(f"[失败] 任务#{task_id} 不存在")

    for node_key, tasks in node_tasks.items():
        node_success = 0
        node_failed = 0
        detail_lines.append(f"[节点] {node_key}")

        for task in tasks:
            _live_tree_set_running(
                task_center_id, node_key, task.config.name,
                task.publish_version, log_lock,
            )

        def _on_done(task, ok, reason, _nk=node_key):
            """单配置终态回调：更新计数与进度树"""
            nonlocal success, failed, node_success, node_failed
            if ok:
                success += 1
                node_success += 1
                detail_lines.append(f"  [成功] {task.config.name} v{task.publish_version}")
            else:
                failed += 1
                node_failed += 1
                detail_lines.append(
                    f"  [失败] {task.config.name} v{task.publish_version}"
                    f" - 失败原因: {reason}"
                )
            _live_tree_set_done(
                task_center_id, _nk, task.config.name, ok,
                version=task.publish_version, reason=reason, lock=log_lock,
            )
            if task_center_id:
                done = success + failed
                TaskCenterTask.objects.filter(pk=task_center_id).update(
                    progress=int(done * 100 / total) if total else 100,
                    detail=f"执行中：成功 {success}，失败 {failed}，共 {total}",
                    updated_at=timezone.now(),
                )

        executor._execute_node_release_batch(
            tasks, action,
            task_center_id=task_center_id, log_lock=log_lock,
            on_task_done=_on_done,
        )
        if tasks:
            _set_current_step(task_center_id, tasks[0].node.hostname, None, log_lock)

        node_results[node_key] = {"success": node_success, "failed": node_failed}

    if task_center_id:
        status = "success" if failed == 0 else "failed"
        result_lines = [f"执行完成：成功 {success}，失败 {failed}，共 {total}"]
        for nk, nr in node_results.items():
            result_lines.append(f"[节点摘要] {nk}: 成功 {nr['success']}, 失败 {nr['failed']}")
        result_lines.extend(detail_lines)

        TaskCenterTask.objects.filter(pk=task_center_id).update(
            status=status, progress=100, finished_at=timezone.now(),
            result="\n".join(result_lines),
            detail=f"执行完成：成功 {success}，失败 {failed}，共 {total}",
        )
        _clear_release_progress_state(task_center_id)


def _run_release_tasks_parallel(task_ids, task_center_id=None, max_workers=3):
    """并行发布：多节点并行，节点内共用 SSH、全部 -t 通过后统一 reload"""
    from concurrent.futures import ThreadPoolExecutor

    executor = ReleaseExecutorMixin()
    total = len(task_ids)
    detail_lines = []
    node_results = {}
    state = {"success": 0, "failed": 0}
    state_lock = threading.Lock()
    log_lock = threading.Lock()
    action = _release_action_from_task_center(task_center_id)

    if task_center_id:
        _clear_release_progress_state(task_center_id)
        TaskCenterTask.objects.filter(pk=task_center_id).update(
            status="running", started_at=timezone.now(), progress=0,
            log_output="", result="",
        )

    node_tasks = {}
    for task_id in task_ids:
        try:
            task = ReleaseTask.objects.select_related(
                "node", "config", "binding", "operator",
            ).get(pk=task_id)
            node_key = f"{task.node.ip} ({task.node.hostname})"
            if node_key not in node_tasks:
                node_tasks[node_key] = []
            node_tasks[node_key].append(task)
        except ReleaseTask.DoesNotExist:
            with state_lock:
                state["failed"] += 1
            detail_lines.append(f"[失败] 任务#{task_id} 不存在")

    def _execute_node(node_key, tasks):
        """执行单个节点的所有配置发布（节点级一次 reload）"""
        node_success = 0
        node_failed = 0
        node_lines = [f"[节点] {node_key}"]

        for t in tasks:
            _live_tree_set_running(
                task_center_id, node_key, t.config.name,
                t.publish_version, log_lock,
            )

        def _on_done(task, ok, reason):
            """单配置终态回调：更新并行计数与进度树"""
            nonlocal node_success, node_failed
            with state_lock:
                if ok:
                    state["success"] += 1
                    node_success += 1
                    node_lines.append(f"  [成功] {task.config.name} v{task.publish_version}")
                else:
                    state["failed"] += 1
                    node_failed += 1
                    node_lines.append(
                        f"  [失败] {task.config.name} v{task.publish_version}"
                        f" - 失败原因: {reason}"
                    )
                if task_center_id:
                    done = state["success"] + state["failed"]
                    TaskCenterTask.objects.filter(pk=task_center_id).update(
                        progress=int(done * 100 / total) if total else 100,
                        detail=(
                            f"并行执行中：成功 {state['success']}，"
                            f"失败 {state['failed']}，共 {total}"
                        ),
                        updated_at=timezone.now(),
                    )
            _live_tree_set_done(
                task_center_id, node_key, task.config.name, ok,
                version=task.publish_version, reason=reason, lock=log_lock,
            )

        executor._execute_node_release_batch(
            tasks, action,
            task_center_id=task_center_id, log_lock=log_lock,
            on_task_done=_on_done,
        )
        if tasks:
            _set_current_step(task_center_id, tasks[0].node.hostname, None, log_lock)
        return node_key, node_success, node_failed, node_lines

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for node_key, tasks in node_tasks.items():
            futures[pool.submit(_execute_node, node_key, tasks)] = node_key

        for future in futures:
            try:
                node_key, node_success, node_failed, node_lines = future.result()
                detail_lines.extend(node_lines)
                node_results[node_key] = {"success": node_success, "failed": node_failed}
            except Exception as e:
                logger.error(f"并行节点执行异常: {e}")

    success = state["success"]
    failed = state["failed"]

    if task_center_id:
        status = "success" if failed == 0 else "failed"
        result_lines = [f"执行完成（并行模式）：成功 {success}，失败 {failed}，共 {total}"]
        for nk, nr in node_results.items():
            result_lines.append(f"[节点摘要] {nk}: 成功 {nr['success']}, 失败 {nr['failed']}")
        result_lines.extend(detail_lines)

        TaskCenterTask.objects.filter(pk=task_center_id).update(
            status=status, progress=100, finished_at=timezone.now(),
            result="\n".join(result_lines),
            detail=f"执行完成：成功 {success}，失败 {failed}，共 {total}",
        )
        _clear_release_progress_state(task_center_id)


class TaskCenterProgressAPIView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        self.can_read_release_tasks = user_has_permission(request.user, "releases", "read")
        self.can_read_node_tasks = user_has_permission(request.user, "nodes", "update")
        self.can_sync_configs = user_has_permission(request.user, "configs", "update")
        if not (self.can_read_release_tasks or self.can_read_node_tasks or self.can_sync_configs):
            return forbidden_response(request, "当前账号无权限访问该功能")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        ids_raw = request.GET.get("ids", "")
        id_list = [int(i) for i in ids_raw.split(",") if i.strip().isdigit()]
        if not id_list:
            return JsonResponse({"success": True, "tasks": []})
        tasks = TaskCenterTask.objects.filter(id__in=id_list).order_by("-created_at")
        # 无发布读权限时：仅本人触发的节点测试 / 配置同步（对齐任务中心列表）
        if not self.can_read_release_tasks:
            tasks = tasks.filter(
                operation_type__in=["node_batch_test", "config_batch_sync"],
                trigger_user=request.user,
            )
        data = [
            {
                "id": t.id, "status": t.status, "progress": t.progress,
                "detail": t.detail, "result": t.result,
                "log_output": t.log_output or "",
                "current_steps": (
                    _format_current_steps(t.id)
                    if t.status in ("pending", "running")
                    else ""
                ),
                "finished": t.status in ["success", "failed", "cancelled"],
            }
            for t in tasks
        ]
        return JsonResponse({"success": True, "tasks": data})


class ReleaseCenterExecuteView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View
):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        try:
            if ReleaseTask.objects.filter(status="running").exists():
                msg = "当前有批次正在执行中，请等待完成后再执行"
                if is_ajax:
                    return JsonResponse({"success": False, "message": msg})
                messages.error(request, msg)
                return redirect("releases:center")
        except OperationalError:
            pass

        tasks_qs = ReleaseTask.objects.filter(
            batch_number=batch_number, status="pending",
        ).select_related("node", "config", "binding", "operator")
        task_ids = list(tasks_qs.values_list("id", flat=True))

        if not task_ids:
            msg = "没有可执行的发布任务"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg})
            messages.error(request, msg)
            return redirect("releases:center")

        # 创建 TaskCenterTask
        from apps.releases.task_result import targets_from_release_tasks
        targets = targets_from_release_tasks(task_ids)
        task_center = TaskCenterTask.objects.create(
            operation_type="release_publish",
            status="running",
            source_batch=batch_number,
            detail=f"执行中：成功 0，失败 0，共 {len(task_ids)}",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
            **targets,
        )

        _start_release_executor(task_ids, task_center.id)

        redirect_url = reverse("releases:task_center_detail", kwargs={"pk": task_center.id})
        if is_ajax:
            return JsonResponse({
                "success": True,
                "async": True,
                "task_center_id": task_center.id,
                "task_center_detail_url": redirect_url,
            })

        messages.success(request, f"发布任务已开始执行，{len(task_ids)} 个任务，批次号: {batch_number}")
        return redirect(redirect_url)


class ReleaseCenterCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        updated = ReleaseTask.objects.filter(
            batch_number=batch_number, status="pending",
        ).update(status="cancelled", result="用户取消")
        if updated:
            messages.success(request, f"已取消 {updated} 个待执行任务")
        else:
            messages.info(request, "没有待执行的任务")
        return redirect("releases:center")


class ReleaseCenterSingleExecuteView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View
):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, task_id):
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "binding", "operator"),
            pk=task_id,
        )
        if task.status != "pending":
            messages.error(request, "任务不是待发布状态")
            return redirect("releases:center")

        from apps.releases.task_result import targets_from_release_tasks
        targets = targets_from_release_tasks([task.id])
        task_center = TaskCenterTask.objects.create(
            operation_type="release_publish",
            status="running",
            source_batch=task.batch_number,
            detail="执行中...",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
            **targets,
        )

        _start_release_executor([task.id], task_center.id)

        messages.success(request, f"发布任务 #{task_id} 已开始执行")
        return redirect("releases:center")


class ReleaseTaskStatusView(LoginRequiredMixin, View):
    """查询单个任务状态 (Ajax)"""

    def get(self, request, task_id):
        task = get_object_or_404(ReleaseTask, pk=task_id)
        return JsonResponse({
            "id": task.id,
            "status": task.status,
            "result": task.result,
            "finished": task.status in ["success", "failed", "rollback", "cancelled"],
        })


class VersionContentAPIView(LoginRequiredMixin, View):
    """获取版本内容 (Ajax)"""

    def get(self, request, version_id):
        from apps.configs.models import BindingVersion
        version = get_object_or_404(BindingVersion, pk=version_id)
        return JsonResponse({
            "id": version.id,
            "version": version.version,
            "content": version.content,
            "remark": version.remark,
            "created_at": version.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "created_by": version.created_by.username if version.created_by else "",
        })



def _build_release_status_counts():
    """构建发布中心绑定状态全局统计（排除已删节点与标记删除绑定）"""
    from apps.configs.models import ConfigNodeBinding
    # 可发布集合：不含 marked_deleted；conflict/syncing 未启用故不统计
    bindings = ConfigNodeBinding.objects.filter(node__is_deleted=False).exclude(
        sync_status="marked_deleted",
    )
    return {
        "total": bindings.count(),
        "pending": bindings.filter(sync_status__in=["not_synced", "modified"]).count(),
        "synced": bindings.filter(sync_status="synced").count(),
        "failed": bindings.filter(sync_status="failed").count(),
        "orphaned": bindings.filter(sync_status="orphaned").count(),
    }


class ReleaseNodeListAPIView(LoginRequiredMixin, View):
    """获取发布中心可选节点列表（含绑定统计）"""

    def get(self, request):
        search = request.GET.get("search", "").strip()
        environment = request.GET.get("environment", "").strip()
        group_id = request.GET.get("group_id", "").strip()
        node_status = request.GET.get("status", "").strip()
        sync_status = request.GET.get("sync_status", "").strip()
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))

        queryset = Node.objects.all().prefetch_related("groups")

        if search:
            terms = [t.strip() for t in search.replace("，", ",").split(",") if t.strip()]
            for term in terms:
                queryset = queryset.filter(
                    Q(hostname__icontains=term)
                    | Q(ip__icontains=term)
                    | Q(groups__name__icontains=term)
                    | Q(config_bindings__config__name__icontains=term)
                    | Q(config_bindings__remote_path__icontains=term)
                ).distinct()
        if environment:
            queryset = queryset.filter(environment=environment)
        if node_status:
            queryset = queryset.filter(status=node_status)
        if group_id and group_id.isdigit():
            queryset = queryset.filter(groups__id=int(group_id)).distinct()

        # 按绑定同步状态过滤节点
        if sync_status:
            if sync_status == "pending":
                status_values = ["not_synced", "modified"]
            else:
                status_values = [sync_status]
            node_ids_with_status = (
                ConfigNodeBinding.objects
                .filter(sync_status__in=status_values)
                .values_list("node_id", flat=True)
                .distinct()
            )
            queryset = queryset.filter(id__in=node_ids_with_status)

        total = queryset.count()
        nodes_page = queryset[(page - 1) * page_size: page * page_size]

        node_ids = [n.id for n in nodes_page]
        binding_stats = {}
        if node_ids:
            from django.db.models import Count, Q as DQ
            stats_qs = (
                ConfigNodeBinding.objects
                .filter(node_id__in=node_ids)
                .exclude(sync_status="marked_deleted")
                .values("node_id")
                .annotate(
                    total_bindings=Count("id"),
                    modified_bindings=Count("id", filter=DQ(sync_status="modified")),
                )
            )
            for row in stats_qs:
                binding_stats[row["node_id"]] = {
                    "total_bindings": row["total_bindings"],
                    "modified_bindings": row["modified_bindings"],
                }

        node_list = []
        for node in nodes_page:
            stats = binding_stats.get(node.id, {"total_bindings": 0, "modified_bindings": 0})
            node_list.append({
                "id": node.id,
                "hostname": node.hostname,
                "ip": f"{node.ip}:{node.port}",
                "environment": node.environment,
                "status": node.status,
                "is_locked": node.is_locked,
                "has_credential": node.credential_id is not None,
                "total_bindings": stats["total_bindings"],
                "modified_bindings": stats["modified_bindings"],
                "group_names": [g.name for g in node.groups.all()],
            })

        return JsonResponse({
            "success": True,
            "nodes": node_list,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
            "status_counts": _build_release_status_counts(),
        })


class ReleaseNodeBindingsAPIView(LoginRequiredMixin, View):
    """获取指定节点的所有绑定详情（含版本列表）"""

    def get(self, request, node_id):
        # 标记删除的绑定不可发布，不返回给发布中心勾选
        bindings = (
            ConfigNodeBinding.objects
            .filter(node_id=node_id)
            .exclude(sync_status="marked_deleted")
            .select_related("config")
            .order_by("config__name")
        )

        result = []
        for binding in bindings:
            versions = (
                binding.versions
                .order_by("-version")
                .values("id", "version", "created_at")
            )
            result.append({
                "id": binding.id,
                "config_id": binding.config_id,
                "config_name": binding.config.name,
                "remote_path": binding.remote_path,
                "current_version": binding.current_version,
                "sync_status": binding.sync_status,
                "synced_version": binding.synced_version,
                "versions": [
                    {
                        "id": v["id"],
                        "version": v["version"],
                        "created_at": v["created_at"].strftime("%Y-%m-%d %H:%M:%S") if v["created_at"] else "",
                    }
                    for v in versions
                ],
            })

        return JsonResponse({"success": True, "bindings": result})


class ReleaseRetryView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """重试单条失败的发布任务"""
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, pk):
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "binding", "operator"),
            pk=pk,
        )
        if task.status not in ["failed"]:
            return JsonResponse({"success": False, "message": "只能重试失败的任务"}, status=400)

        if task.node.is_locked:
            return JsonResponse({"success": False, "message": f"节点 {task.node.hostname} 已锁定"}, status=400)
        if task.node.is_deleted:
            return JsonResponse({"success": False, "message": f"节点 {task.node.hostname} 已删除，无法重试"}, status=400)

        task.status = "pending"
        task.result = ""
        task.save(update_fields=["status", "result"])

        from apps.releases.task_result import targets_from_release_tasks
        targets = targets_from_release_tasks([task.id])
        task_center = TaskCenterTask.objects.create(
            operation_type="release_publish",
            status="running",
            source_batch=task.batch_number or f"retry-task-{task.id}",
            detail=f"重试: {task.config.name} → {task.node.hostname}",
            progress=0,
            started_at=timezone.now(),
            trigger_user=request.user,
            **targets,
        )

        _start_release_executor([task.id], task_center.id)

        return JsonResponse({
            "success": True,
            "message": f"重试任务已开始: {task.config.name} → {task.node.hostname}",
            "task_center_id": task_center.id,
        })

def _start_rollback_for_release_tasks(tasks, user):
    """
    根据已筛选的发布任务启动批量回滚。
    返回 (True, response_dict) 或 (False, error_message)。
    回滚目标为各任务 publish_version 的上一版（非 synced_version）。
    同一 binding 跨多批次时仅保留最新任务，再回滚到其上一版。
    """
    rollback_batch = generate_batch_number()
    rollback_task_ids = []
    skipped = 0

    # 同 binding 只保留最新任务（created_at、id 更大者优先）
    task_list = list(tasks)
    by_binding = {}
    no_binding = []
    for task in task_list:
        bid = task.binding_id
        if bid is None:
            no_binding.append(task)
            continue
        prev = by_binding.get(bid)
        if prev is None:
            by_binding[bid] = task
            continue
        if (task.created_at, task.id) > (prev.created_at, prev.id):
            by_binding[bid] = task

    # 被同绑定更新的旧任务计入跳过
    kept_ids = {t.id for t in by_binding.values()}
    for task in task_list:
        if task.binding_id is not None and task.id not in kept_ids:
            skipped += 1

    candidates = list(by_binding.values()) + no_binding

    for task in candidates:
        if task.node.is_locked or task.node.is_deleted:
            skipped += 1
            continue
        if not task.binding:
            skipped += 1
            continue
        # 回滚目标 = 该次发布版本的上一版（成功发布后 synced_version 已是刚发布版，不能用）
        binding = task.binding
        publish_ver = task.publish_version
        if publish_ver is None:
            skipped += 1
            continue
        prev = binding.versions.filter(version__lt=publish_ver).order_by("-version").first()
        if not prev:
            skipped += 1
            continue

        new_task = ReleaseTask.objects.create(
            batch_number=rollback_batch,
            binding=binding,
            config=task.config,
            node=task.node,
            version=prev,
            publish_version=prev.version,
            remote_path=task.remote_path or (binding.remote_path if binding else ""),
            operator=user,
            status="pending",
        )
        rollback_task_ids.append(new_task.id)

    if not rollback_task_ids:
        return False, "未生成任何回滚任务（所选任务均无上一版本、同绑定已去重、节点已锁定或已删除）"

    started = len(rollback_task_ids)
    detail = f"批量回滚：{started} 个任务"
    if skipped:
        detail += f"（跳过 {skipped} 个）"

    from apps.releases.task_result import targets_from_release_tasks
    targets = targets_from_release_tasks(rollback_task_ids)
    task_center = TaskCenterTask.objects.create(
        operation_type="release_rollback",
        status="running",
        source_batch=rollback_batch,
        detail=detail,
        progress=0,
        started_at=timezone.now(),
        trigger_user=user,
        **targets,
    )

    _start_release_executor(rollback_task_ids, task_center.id)

    message = f"批量回滚已开始，批次号: {rollback_batch}，已启动 {started} 个"
    if skipped:
        message += f"，跳过 {skipped} 个"

    return True, {
        "success": True,
        "message": message,
        "task_center_id": task_center.id,
        "batch_number": rollback_batch,
    }


class ReleaseBatchRollbackView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """按批次号批量回滚（兼容保留）"""
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        tasks = ReleaseTask.objects.filter(
            batch_number=batch_number,
            status__in=["success", "failed"],
        ).select_related("node", "config", "binding")

        if not tasks.exists():
            return JsonResponse({"success": False, "message": "未找到可回滚的任务"}, status=400)

        ok, result = _start_rollback_for_release_tasks(tasks, request.user)
        if not ok:
            return JsonResponse({"success": False, "message": result}, status=400)
        return JsonResponse(result)


class ReleaseSelectedRollbackView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """按勾选的发布任务 ID 批量回滚"""
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "请求数据格式错误"}, status=400)

        raw_ids = data.get("task_ids") or []
        task_ids = []
        for item in raw_ids:
            try:
                task_ids.append(int(item))
            except (TypeError, ValueError):
                continue

        if not task_ids:
            return JsonResponse({"success": False, "message": "请至少勾选一个任务"}, status=400)

        tasks = ReleaseTask.objects.filter(
            id__in=task_ids,
            status__in=["success", "failed"],
        ).select_related("node", "config", "binding")

        if not tasks.exists():
            return JsonResponse({"success": False, "message": "未找到可回滚的任务"}, status=400)

        ok, result = _start_rollback_for_release_tasks(tasks, request.user)
        if not ok:
            return JsonResponse({"success": False, "message": result}, status=400)
        return JsonResponse(result)
