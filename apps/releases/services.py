"""发布执行与回滚启动服务（自 views 迁出，业务逻辑不变）。"""
import logging
import threading
from datetime import datetime

from django.utils import timezone

from apps.nodes.services import _get_node_credential
from utils.setting_service import get_setting
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
from .task_cancel import (
    finish_if_active,
    is_cancelled,
    register_ssh,
    unregister_ssh,
    update_if_active,
)
from .task_progress import (
    _append_task_center_log,
    _clear_release_progress_state,
    _live_tree_set_done,
    _live_tree_set_running,
    _release_step_label,
    _set_current_step,
)

logger = logging.getLogger(__name__)


def _release_max_workers():
    """读取跨节点并行上限（复用批量操作最大节点数）"""
    try:
        return max(1, int(get_setting("node.batch_max_count", "3") or 3))
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
        if task_center_id and is_cancelled(task_center_id):
            return _fail("任务已取消")

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
        if task_center_id and is_cancelled(task_center_id):
            return _fail("任务已取消")
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
            "nginx -t 通过，等待本节点统一 reload（未运行则 start）",
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
            "本节点统一 reload/start", extra=f"{len(pending_items)} 个配置",
        )
        # 以首个 pending 写里程碑，并广播到各任务日志
        lead = pending_items[0]
        lead["add_log"](
            "本节点全部配置已通过 nginx -t，正在统一 reload（未运行则 start）...",
            milestone=True,
            step=reload_step,
        )
        for item in pending_items[1:]:
            item["add_log"]("本节点全部配置已通过 nginx -t，正在统一 reload（未运行则 start）...")

        success, reload_output = execute_nginx_reload(
            nginx_path=nginx_path, start_if_stopped=True, **step_kwargs,
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

        # 前置：锁定 / 在线 / 凭证
        if node.is_locked:
            msg = f"节点 {node.hostname} 已锁定，无法执行发布"
            for task in tasks:
                self._fail_task_early(task, action, msg)
                if on_task_done:
                    on_task_done(task, False, msg)
                results.append((task, False))
            return results

        from apps.nodes.services import nginx_ops_gate_message

        gate_msg = nginx_ops_gate_message(node)
        if gate_msg:
            for task in tasks:
                self._fail_task_early(task, action, gate_msg)
                if on_task_done:
                    on_task_done(task, False, gate_msg)
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
            # 登记 SSH，取消时可关闭打断阻塞
            if task_center_id:
                register_ssh(task_center_id, ssh)
            # 将建连日志并入 first 的 result，供后续 deploy 继续追加
            first.result = "\n".join(first_logs)
            first.save(update_fields=["result"])
            step_kwargs = {**kwargs, "client": ssh}

            abort_reason = None
            for idx, task in enumerate(tasks):
                # 协作取消：停止本节点后续配置
                if task_center_id and is_cancelled(task_center_id):
                    cancel_msg = "任务已取消，跳过后续配置"
                    for rest in tasks[idx:]:
                        if rest.status not in ("success", "failed", "cancelled"):
                            rest.status = "cancelled"
                            rest.result = cancel_msg
                            rest.finished_at = datetime.now()
                            rest.save(update_fields=["status", "result", "finished_at"])
                        if on_task_done:
                            on_task_done(rest, False, cancel_msg)
                        results.append((rest, False))
                    return results

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
                if task_center_id and is_cancelled(task_center_id):
                    cancel_msg = "任务已取消，跳过统一 reload"
                    self._rollback_pending_items(
                        pending_items, step_kwargs, "任务已取消",
                    )
                    for item in pending_items:
                        t = item["task"]
                        if t.status not in ("cancelled",):
                            t.status = "cancelled"
                            t.result = (t.result or "") + f"\n{cancel_msg}"
                            t.finished_at = datetime.now()
                            t.save(update_fields=["status", "result", "finished_at"])
                        if on_task_done:
                            on_task_done(t, False, cancel_msg)
                        results.append((t, False))
                    return results
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
            if task_center_id and ssh is not None:
                unregister_ssh(task_center_id, ssh)
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
        if task_center_id and is_cancelled(task_center_id):
            cancel_msg = "任务已取消，跳过后续节点"
            for task in tasks:
                if task.status not in ("success", "failed", "cancelled"):
                    task.status = "cancelled"
                    task.result = cancel_msg
                    task.finished_at = datetime.now()
                    task.save(update_fields=["status", "result", "finished_at"])
                failed += 1
                detail_lines.append(f"  [失败] {task.config.name} - {cancel_msg}")
            node_results[node_key] = {"success": 0, "failed": len(tasks)}
            continue

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
                update_if_active(
                    task_center_id,
                    progress=int(done * 100 / total) if total else 100,
                    detail=f"执行中：成功 {success}，失败 {failed}，共 {total}",
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
        if is_cancelled(task_center_id):
            _clear_release_progress_state(task_center_id)
            return
        status = "success" if failed == 0 else "failed"
        result_lines = [f"执行完成：成功 {success}，失败 {failed}，共 {total}"]
        for nk, nr in node_results.items():
            result_lines.append(f"[节点摘要] {nk}: 成功 {nr['success']}, 失败 {nr['failed']}")
        result_lines.extend(detail_lines)

        finish_if_active(
            task_center_id,
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
        if task_center_id and is_cancelled(task_center_id):
            cancel_msg = "任务已取消，跳过本节点"
            for t in tasks:
                if t.status not in ("success", "failed", "cancelled"):
                    t.status = "cancelled"
                    t.result = cancel_msg
                    t.finished_at = datetime.now()
                    t.save(update_fields=["status", "result", "finished_at"])
                with state_lock:
                    state["failed"] += 1
            return node_key, 0, len(tasks), [f"[节点] {node_key}", f"  [失败] {cancel_msg}"]

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
                    update_if_active(
                        task_center_id,
                        progress=int(done * 100 / total) if total else 100,
                        detail=(
                            f"并行执行中：成功 {state['success']}，"
                            f"失败 {state['failed']}，共 {total}"
                        ),
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
            if task_center_id and is_cancelled(task_center_id):
                break
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
        if is_cancelled(task_center_id):
            _clear_release_progress_state(task_center_id)
            return
        status = "success" if failed == 0 else "failed"
        result_lines = [f"执行完成（并行模式）：成功 {success}，失败 {failed}，共 {total}"]
        for nk, nr in node_results.items():
            result_lines.append(f"[节点摘要] {nk}: 成功 {nr['success']}, 失败 {nr['failed']}")
        result_lines.extend(detail_lines)

        finish_if_active(
            task_center_id,
            status=status, progress=100, finished_at=timezone.now(),
            result="\n".join(result_lines),
            detail=f"执行完成：成功 {success}，失败 {failed}，共 {total}",
        )
        _clear_release_progress_state(task_center_id)


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
        from apps.nodes.services import nginx_ops_gate_message

        if nginx_ops_gate_message(task.node):
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
