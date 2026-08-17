"""Web 进程启动时清理遗留 pending/running 任务，避免重启后门禁卡住。"""
import logging

from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

from apps.releases.task_cancel import ACTIVE_STATUSES

logger = logging.getLogger(__name__)

RESTART_DETAIL = "进程重启，任务中断"


def _fail_task_center():
    """将任务中心仍活跃的记录标为失败。"""
    from apps.releases.models import TaskCenterTask

    now = timezone.now()
    return TaskCenterTask.objects.filter(status__in=ACTIVE_STATUSES).update(
        status="failed",
        progress=100,
        detail=RESTART_DETAIL,
        result=RESTART_DETAIL,
        finished_at=now,
        updated_at=now,
    )


def _fail_release_tasks():
    """将发布明细仍 pending/running 标为失败，解开 Q93 门禁。"""
    from apps.releases.models import ReleaseTask

    now = timezone.now()
    return ReleaseTask.objects.filter(status__in=("pending", "running")).update(
        status="failed",
        result=RESTART_DETAIL,
        finished_at=now,
    )


def _fail_upgrade_tasks():
    """将升级任务非终态标为失败。"""
    from apps.upgrade.models import NginxUpgradeTask

    now = timezone.now()
    terminal = ("success", "failed", "rollback", "cancelled")
    return NginxUpgradeTask.objects.exclude(status__in=terminal).update(
        status="failed",
        error_message=RESTART_DETAIL,
        finished_at=now,
    )


def _fail_install_tasks():
    """将安装任务非终态标为失败。"""
    from apps.nginx_install.models import NginxInstallTask

    now = timezone.now()
    terminal = ("success", "failed", "cancelled")
    return NginxInstallTask.objects.exclude(status__in=terminal).update(
        status="failed",
        error_message=RESTART_DETAIL,
        finished_at=now,
    )


def _fail_uninstall_tasks():
    """将卸载任务非终态标为失败。"""
    from apps.nginx_uninstall.models import NginxUninstallTask

    now = timezone.now()
    terminal = ("success", "failed", "cancelled")
    return NginxUninstallTask.objects.exclude(status__in=terminal).update(
        status="failed",
        error_message=RESTART_DETAIL,
        finished_at=now,
    )


def cleanup_stale_running_tasks():
    """Web 启动时调用：中断库内遗留执行中任务，不触达远端。"""
    try:
        tc = _fail_task_center()
        rel = _fail_release_tasks()
        upg = _fail_upgrade_tasks()
        inst = _fail_install_tasks()
        un = _fail_uninstall_tasks()
    except (OperationalError, ProgrammingError):
        logger.info("启动清理跳过：数据表尚未就绪")
        return 0
    total = tc + rel + upg + inst + un
    if total:
        logger.warning(
            "进程重启清理遗留任务: task_center=%s release=%s upgrade=%s install=%s uninstall=%s",
            tc, rel, upg, inst, un,
        )
    return total
