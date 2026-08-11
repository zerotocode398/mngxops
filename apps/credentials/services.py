"""凭证启用后的关联节点批量 SSH 测试。"""
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.db import close_old_connections
from django.utils import timezone

from apps.nodes.models import Node
from apps.nodes.services import mark_node_probe_success
from apps.releases.models import TaskCenterTask
from apps.releases.task_cancel import finish_if_active
from utils.setting_service import get_setting
from utils.ssh import test_ssh_connection

from .models import Credential, CredentialEnableTask


def _update_credential_test_result(credential, fail_count):
    """根据测试失败数更新凭证的最后测试结果字段"""
    credential.last_test_time = timezone.now()
    if fail_count == 0:
        credential.last_test_result = "success"
    elif fail_count >= credential.node_set.filter(is_locked=False).count():
        credential.last_test_result = "failed"
    else:
        credential.last_test_result = "partial"
    credential.save(update_fields=["last_test_time", "last_test_result"])


def _run_credential_enable_task(task_id, credential_id):
    """后台线程执行凭证启用后的关联节点批量连接测试"""
    # Ensure thread owns a clean DB connection.
    close_old_connections()

    try:
        from apps.releases.task_result import (
            build_tree_result,
            item_failed,
            item_success,
            node_header,
        )

        task = CredentialEnableTask.objects.get(pk=task_id)
        credential = Credential.objects.get(pk=credential_id)
        center_task_id = task.task_center_id
        cred_label = credential.name or f"凭证#{credential_id}"

        task.status = "running"
        task.started_at = timezone.now()
        task.save(update_fields=["status", "started_at", "updated_at"])

        if center_task_id:
            TaskCenterTask.objects.filter(pk=center_task_id).update(
                status="running",
                started_at=timezone.now(),
                progress=0,
                detail="测试开始",
            )

        nodes = list(Node.objects.filter(credential=credential, is_locked=False).order_by("id"))
        task.total_count = len(nodes)
        task.skipped_count = Node.objects.filter(
            credential=credential, is_locked=True
        ).count()
        task.save(update_fields=["total_count", "skipped_count", "updated_at"])

        if not nodes:
            task.status = "completed"
            task.finished_at = timezone.now()
            task.message = "无可测试节点"
            task.save(update_fields=["status", "finished_at", "message", "updated_at"])
            # 更新凭证的最后测试结果
            credential.last_test_time = timezone.now()
            credential.last_test_result = "unknown"
            credential.save(update_fields=["last_test_time", "last_test_result"])
            if center_task_id:
                TaskCenterTask.objects.filter(pk=center_task_id).update(
                    status="success",
                    progress=100,
                    finished_at=timezone.now(),
                    detail="无可测试节点",
                    result=f"执行完成：成功 0，失败 0，共 0\n凭证 {cred_label} 无可测试节点",
                )
            return

        max_workers = min(int(get_setting("credential.test_max_concurrency", "10")), len(nodes))

        def _test_node(node):
            """对单个节点执行SSH连接测试，返回 (node, success, message)"""
            try:
                if credential.auth_type == "password":
                    success, message = test_ssh_connection(
                        node.ip,
                        node.port,
                        credential.username,
                        password=credential.get_password(),
                    )
                else:
                    success, message = test_ssh_connection(
                        node.ip,
                        node.port,
                        credential.username,
                        private_key=credential.get_private_key(),
                    )
                if success:
                    mark_node_probe_success(node)
                    node.save(update_fields=["status", "last_probe_at", "updated_at"])
                else:
                    node.status = "offline"
                    node.save(update_fields=["status", "updated_at"])
                return node, success, message or ("连接成功" if success else "连接失败")
            except Exception as exc:
                node.status = "offline"
                node.save(update_fields=["status", "updated_at"])
                return node, False, str(exc)

        success_count = 0
        fail_count = 0
        completed_count = 0
        node_blocks = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_test_node, n) for n in nodes]
            for future in as_completed(futures):
                completed_count += 1
                node, ok, message = future.result()
                if ok:
                    success_count += 1
                    node_blocks.append(node_header(node.ip, node.hostname))
                    node_blocks.append(item_success("SSH连接"))
                else:
                    fail_count += 1
                    node_blocks.append(node_header(node.ip, node.hostname))
                    node_blocks.append(item_failed("SSH连接", message))

                CredentialEnableTask.objects.filter(pk=task.pk).update(
                    completed_count=completed_count,
                    success_count=success_count,
                    failed_count=fail_count,
                    updated_at=timezone.now(),
                )
                if center_task_id:
                    TaskCenterTask.objects.filter(pk=center_task_id).update(
                        progress=int((completed_count / len(nodes)) * 100),
                        detail=f"成功 {success_count}，失败 {fail_count}，共 {len(nodes)}",
                        updated_at=timezone.now(),
                    )

        task.refresh_from_db()
        task.status = "completed"
        task.finished_at = timezone.now()
        skip_tip = f"，锁定跳过 {task.skipped_count}" if task.skipped_count else ""
        task.message = (
            f"凭证 {cred_label}：成功 {task.success_count}，失败 {task.failed_count}{skip_tip}"
        )
        task.save(
            update_fields=["status", "finished_at", "message", "updated_at"]
        )

        # 更新凭证的最后测试结果
        _update_credential_test_result(credential, fail_count)

        if center_task_id:
            result_text = build_tree_result(
                task.success_count, task.failed_count, len(nodes), node_blocks
            )
            if task.skipped_count:
                result_text += f"\n锁定跳过 {task.skipped_count} 台"
            finish_if_active(
                center_task_id,
                status="success" if task.failed_count == 0 else "failed",
                progress=100,
                finished_at=timezone.now(),
                result=result_text,
                detail=f"成功 {task.success_count} / 失败 {task.failed_count}",
                target_hostnames=",".join(n.hostname for n in nodes if n.hostname),
                target_ips=",".join(n.ip for n in nodes if n.ip),
                target_configs=cred_label,
            )
    except Exception as exc:
        CredentialEnableTask.objects.filter(pk=task_id).update(
            status="failed",
            finished_at=timezone.now(),
            message=f"任务失败: {exc}",
            updated_at=timezone.now(),
        )
        try:
            failed_task = CredentialEnableTask.objects.get(pk=task_id)
            if failed_task.task_center_id:
                finish_if_active(
                    failed_task.task_center_id,
                    status="failed",
                    finished_at=timezone.now(),
                    progress=100,
                    result=f"  [失败] 凭证测试 - 失败原因: {exc}",
                    detail=f"任务失败: {exc}",
                )
        except CredentialEnableTask.DoesNotExist:
            pass
    finally:
        close_old_connections()
