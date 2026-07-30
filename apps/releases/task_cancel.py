"""任务中心协作式取消：状态判断、SSH 登记、终态守卫"""
import logging
import threading

from django.utils import timezone

from apps.releases.models import TaskCenterTask

logger = logging.getLogger(__name__)

# 仍可被执行器推进的状态
ACTIVE_STATUSES = ("pending", "running")

# task_center_id -> set of SSH 客户端（paramiko.SSHClient 或带 .client/.close 的包装）
_SSH_REGISTRY = {}
_SSH_LOCK = threading.Lock()


def is_cancelled(task_center_id):
    """判断任务中心任务是否已标记为取消"""
    if not task_center_id:
        return False
    return TaskCenterTask.objects.filter(pk=task_center_id, status="cancelled").exists()


def register_ssh(task_center_id, client):
    """登记任务当前活跃的 SSH 连接，供取消时关闭"""
    if not task_center_id or client is None:
        return
    with _SSH_LOCK:
        _SSH_REGISTRY.setdefault(task_center_id, set()).add(client)


def unregister_ssh(task_center_id, client):
    """取消登记 SSH 连接"""
    if not task_center_id or client is None:
        return
    with _SSH_LOCK:
        clients = _SSH_REGISTRY.get(task_center_id)
        if not clients:
            return
        clients.discard(client)
        if not clients:
            _SSH_REGISTRY.pop(task_center_id, None)


def close_registered_ssh(task_center_id):
    """关闭该任务已登记的全部 SSH，打断本端阻塞等待"""
    if not task_center_id:
        return 0
    with _SSH_LOCK:
        clients = list(_SSH_REGISTRY.pop(task_center_id, set()))
    closed = 0
    for client in clients:
        try:
            # SSHClient 包装或裸 paramiko
            if hasattr(client, "close"):
                client.close()
            elif hasattr(client, "client") and client.client is not None:
                client.client.close()
            closed += 1
        except Exception as exc:
            logger.warning("关闭任务 %s 的 SSH 失败: %s", task_center_id, exc)
    return closed


def update_if_active(task_center_id, **fields):
    """仅当任务仍为 pending/running 时更新进度等字段；返回更新行数"""
    if not task_center_id:
        return 0
    fields.setdefault("updated_at", timezone.now())
    return TaskCenterTask.objects.filter(
        pk=task_center_id, status__in=ACTIVE_STATUSES
    ).update(**fields)


def finish_if_active(task_center_id, **fields):
    """仅当任务仍活跃时写入终态（success/failed 等）；已取消则跳过"""
    if not task_center_id:
        return 0
    fields.setdefault("updated_at", timezone.now())
    return TaskCenterTask.objects.filter(
        pk=task_center_id, status__in=ACTIVE_STATUSES
    ).update(**fields)


def mark_cancelled(task_center_id, detail="用户手动取消", result=""):
    """将任务标为已取消（取消 API 使用）；返回是否更新成功"""
    if not task_center_id:
        return False
    now = timezone.now()
    updated = TaskCenterTask.objects.filter(
        pk=task_center_id, status__in=ACTIVE_STATUSES
    ).update(
        status="cancelled",
        progress=100,
        detail=detail,
        result=result or detail,
        finished_at=now,
        updated_at=now,
    )
    return updated > 0
