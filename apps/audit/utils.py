# -*- coding: utf-8 -*-
"""审计操作日志公共写入工具。"""
from .middleware import get_current_request, get_current_user
from .models import AuditLog


def _resolve_client_ip(ip=None):
    """解析客户端 IP：优先显式传入，其次当前请求。"""
    if ip:
        return ip
    req = get_current_request()
    if req is None:
        return "0.0.0.0"
    xff = req.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return req.META.get("REMOTE_ADDR", "0.0.0.0")


def log_async_task(
    user,
    module,
    action,
    detail,
    task_center_id,
    source_batch="",
    result="success",
    ip=None,
):
    """异步任务创建时写入操作日志（含任务中心超链字段）。"""
    if user is None:
        user = get_current_user()
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    return AuditLog.objects.create(
        user=user,
        module=module,
        action=action,
        ip=_resolve_client_ip(ip),
        result=result,
        detail=detail or "",
        task_center_id=task_center_id,
        source_batch=source_batch or "",
    )


# 任务类型 → (模块名, 动作名)
# 说明：配置发现/同步统一创建 config_batch_sync；下列 discover/drift/glob 仅兼容历史任务展示
OPERATION_AUDIT_MAP = {
    "release_publish": ("发布管理", "发布配置"),
    "release_rollback": ("发布管理", "回滚配置"),
    "credential_enable_test": ("凭证管理", "凭证启用测试"),
    "node_ssh_test": ("节点管理", "节点SSH测试"),
    "node_batch_test": ("节点管理", "节点批量测试"),
    "node_system_info": ("节点管理", "节点系统信息采集"),
    "node_nginx_version": ("节点管理", "Nginx版本检测"),
    "config_batch_sync": ("配置管理", "配置批量同步"),
    "config_discover": ("配置管理", "配置发现扫描"),
    "config_drift_check": ("配置管理", "配置漂移检测"),
    "config_glob_preview": ("配置管理", "配置Glob预览"),
    "nginx_upgrade": ("Nginx升级", "Nginx编译升级"),
    "nginx_rollback": ("Nginx升级", "Nginx升级回滚"),
    "nginx_service_control": ("运维工具", "Nginx服务启停"),
    "nginx_install": ("运维工具", "Nginx全新安装"),
    "nginx_uninstall": ("运维工具", "Nginx卸载"),
    "other": ("任务中心", "其他任务"),
}


def log_task_center_created(task, user=None, detail=None):
    """根据 TaskCenterTask 写入操作日志（统一埋点入口）。"""
    op = getattr(task, "operation_type", "other") or "other"
    module, action = OPERATION_AUDIT_MAP.get(op, ("任务中心", op))
    if detail is None:
        detail = getattr(task, "detail", "") or ""
        hosts = (getattr(task, "target_hostnames", "") or "").strip()
        if hosts and hosts not in detail:
            detail = f"{detail} | 主机: {hosts}".strip(" |")
    return log_async_task(
        user=user or getattr(task, "trigger_user", None),
        module=module,
        action=action,
        detail=detail,
        task_center_id=task.id,
        source_batch=getattr(task, "source_batch", "") or "",
        result="success",
    )
