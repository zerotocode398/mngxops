"""提供统一任务中心使用的筛选和显示格式。"""

import re
from typing import List

from sqlalchemy import or_

from ngxops.tasks.models import Task


OPERATION_LABELS = {
    "release_publish": "发布配置",
    "release_rollback": "回滚配置",
    "credential_enable_test": "凭证启用测试",
    "node_ssh_test": "节点SSH测试",
    "node_batch_test": "节点批量测试",
    "node_system_info": "节点系统信息采集",
    "node_nginx_version": "Nginx 版本检测",
    "config_batch_sync": "配置批量同步",
    "config_discover": "配置发现扫描",
    "config_drift_check": "配置漂移检测",
    "config_glob_preview": "配置Glob预览",
    "nginx_upgrade": "Nginx 编译升级",
    "nginx_rollback": "Nginx 升级回滚",
    "nginx_service_control": "Nginx 服务启停",
    "nginx_install": "Nginx 全新安装",
    "nginx_uninstall": "Nginx 卸载",
    "other": "其他任务",
}

STATUS_LABELS = {
    "pending": "等待中",
    "running": "执行中",
    "success": "成功",
    "failed": "失败",
    "cancelled": "已取消",
}

RESULT_FIELD_LABELS = {
    "action": "操作",
    "backup_path": "备份路径",
    "binding_id": "绑定 ID",
    "bindings": "配置项",
    "config_name": "配置",
    "created": "新建",
    "created_at": "创建时间",
    "deleted": "删除",
    "detail": "详情",
    "errors": "错误",
    "failed": "失败",
    "files": "文件",
    "hostname": "主机名",
    "install_origin": "安装来源",
    "ip": "IP 地址",
    "message": "说明",
    "method": "认证方式",
    "name": "名称",
    "nodes": "节点",
    "package_manager": "包管理器",
    "package_name": "软件包",
    "path": "路径",
    "pending": "等待中",
    "progress": "进度",
    "remote_path": "远程路径",
    "removed_paths": "删除路径",
    "running": "执行中",
    "skipped": "跳过",
    "ssh_success": "SSH 状态",
    "status": "状态",
    "success": "成功",
    "summary": "汇总",
    "total": "总数",
    "updated": "更新",
    "version": "版本",
}


def split_search_terms(search: str) -> List[str]:
    """解析任务搜索框中的逗号分隔关键词。"""
    return [
        term.strip()
        for term in (search or "").replace("，", ",").split(",")
        if term.strip()
    ]


def task_search_filters(search: str) -> List[object]:
    """为每个搜索词生成批次、主机名或 IP 的 OR 条件。"""
    filters = []
    for term in split_search_terms(search):
        pattern = "%{}%".format(term)
        filters.append(
            or_(
                Task.source_batch.ilike(pattern),
                Task.target_hostnames.ilike(pattern),
                Task.target_ips.ilike(pattern),
            )
        )
    return filters


_SUCCESS_FAILURE = re.compile(r"成功\s*(\d+).*?失败\s*(\d+)", re.DOTALL)


def format_task_summary(task: Task) -> tuple:
    """生成列表中显示的目标和执行结果两行摘要。"""
    operation_type = task.operation_type or ""
    detail = (task.detail or "").strip()
    hosts = (task.target_hostnames or "").strip()
    batch = (task.source_batch or "").strip()

    if operation_type == "credential_enable_test":
        primary = (task.target_configs or "").split(",", 1)[0].strip()
    elif operation_type in (
        "release_publish",
        "release_rollback",
        "nginx_upgrade",
        "nginx_rollback",
        "nginx_install",
        "nginx_service_control",
        "nginx_uninstall",
    ):
        primary = batch or _short_host_summary(hosts)
    else:
        primary = _short_host_summary(hosts) or batch
        if not primary:
            primary = (task.target_configs or "").split(",", 1)[0].strip()

    match = _SUCCESS_FAILURE.search(detail)
    secondary = (
        "成功 {} / 失败 {}".format(match.group(1), match.group(2))
        if match
        else detail
    )
    if not secondary and task.result_tree_json:
        secondary = "执行结果已生成"
    return primary or "-", secondary


def _short_host_summary(hosts: str, limit: int = 3) -> str:
    """将长主机列表压缩为最多三台主机名。"""
    names = [name.strip() for name in hosts.split(",") if name.strip()]
    if len(names) <= limit:
        return ",".join(names)
    return "{} 等{}台".format(",".join(names[:limit]), len(names))


def format_execution_duration(started_at, finished_at) -> str:
    """把任务开始和完成时间转换为简短耗时。"""
    if not started_at or not finished_at:
        return ""
    seconds = max(0.0, (finished_at - started_at).total_seconds())
    if seconds >= 60:
        return "{:.1f} 分钟".format(seconds / 60)
    return "{:.1f} 秒".format(seconds)
