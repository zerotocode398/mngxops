"""Nginx 卸载：首页、向导与 API"""
import json
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.views.generic import DetailView, ListView, TemplateView, View

from apps.users.permissions import PermissionRequiredMixin, user_has_permission
from utils.pagination import PerPagePaginationMixin
from utils.setting_service import get_recent_tasks_limit

from .models import NginxUninstallTask
from .services import (
    batch_max_count,
    create_uninstall_batch_from_data,
    preview_nodes,
)


def _parse_options_summary(options_json):
    """从 options_json 生成删除项摘要列表（供任务详情展示）"""
    try:
        opts = json.loads(options_json or "{}")
    except (TypeError, ValueError):
        opts = {}
    if not isinstance(opts, dict):
        opts = {}
    lines = []
    if opts.get("remove_backup"):
        lines.append("发布备份目录")
    if opts.get("remove_workdir"):
        lines.append("编译工作目录")
    if opts.get("remove_modules"):
        lines.append("第三方模块源码目录")
    for ep in opts.get("extra_paths") or []:
        if not isinstance(ep, dict):
            continue
        path = (ep.get("path") or "").strip()
        key = (ep.get("key") or "").strip()
        if path:
            lines.append(f"{key}: {path}" if key else path)
    if opts.get("stop_if_running"):
        lines.append("运行中则先停止")
    return lines


class NginxUninstallIndexView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Nginx 卸载运维台首页"""

    template_name = "nginx_uninstall/index.html"
    permission_resource = "nodes"
    permission_action = "read"

    def get_context_data(self, **kwargs):
        """注入统计与最近卸载任务"""
        context = super().get_context_data(**kwargs)
        since = timezone_now_minus_7d()
        running_statuses = [
            "pending", "stopping", "removing_prefix",
            "removing_backup", "removing_extra", "updating_node",
        ]
        context["running_count"] = NginxUninstallTask.objects.filter(
            status__in=running_statuses
        ).count()
        context["success_7d_count"] = NginxUninstallTask.objects.filter(
            status="success", created_at__gte=since
        ).count()
        context["failed_7d_count"] = NginxUninstallTask.objects.filter(
            status="failed", created_at__gte=since
        ).count()
        context["total_count"] = NginxUninstallTask.objects.count()
        context["recent_tasks"] = (
            NginxUninstallTask.objects.select_related("node", "operator", "task_center")
            .order_by("-created_at")[: get_recent_tasks_limit()]
        )
        context["can_execute"] = user_has_permission(self.request.user, "nodes", "update")
        return context


def timezone_now_minus_7d():
    """返回 7 天前的时间点"""
    from django.utils import timezone

    return timezone.now() - timedelta(days=7)


class NginxUninstallCenterView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Nginx 卸载三步向导"""

    template_name = "nginx_uninstall/center.html"
    permission_resource = "nodes"
    permission_action = "update"

    def get_context_data(self, **kwargs):
        """注入批量上限"""
        context = super().get_context_data(**kwargs)
        context["batch_max_count"] = batch_max_count()
        context["can_execute"] = user_has_permission(self.request.user, "nodes", "update")
        return context


class NginxUninstallHistoryView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """卸载历史列表"""

    model = NginxUninstallTask
    template_name = "nginx_uninstall/history.html"
    context_object_name = "tasks"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "nodes"
    permission_action = "read"

    def get_queryset(self):
        """按搜索词与状态筛选"""
        qs = NginxUninstallTask.objects.select_related(
            "node", "operator", "task_center"
        )
        search = (self.request.GET.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(node__hostname__icontains=search)
                | Q(node__ip__icontains=search)
                | Q(resolved_prefix__icontains=search)
                | Q(batch_number__icontains=search)
                | Q(backup_path__icontains=search)
            )
        status = (self.request.GET.get("status") or "").strip()
        if status == "running":
            qs = qs.exclude(status__in=["success", "failed", "cancelled"])
        elif status:
            qs = qs.filter(status=status)
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        """注入分页与筛选上下文"""
        context = super().get_context_data(**kwargs)
        tasks = self.get_queryset()
        per_page = self.get_paginate_by(None)
        paginator = Paginator(list(tasks), per_page)
        page_obj = paginator.get_page(self.request.GET.get("page", 1))
        context["tasks"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()
        context["search"] = (self.request.GET.get("search") or "").strip()
        context["status_filter"] = (self.request.GET.get("status") or "").strip()
        context["status_choices"] = [("running", "进行中")] + list(
            NginxUninstallTask.STATUS_CHOICES
        )
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        return context


class NginxUninstallTaskLogView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """卸载任务详情与完整执行日志页"""

    model = NginxUninstallTask
    template_name = "nginx_uninstall/task_log.html"
    context_object_name = "task"
    permission_resource = "nodes"
    permission_action = "read"

    def get_queryset(self):
        """预加载节点与操作人"""
        return NginxUninstallTask.objects.select_related(
            "node", "operator", "task_center"
        )

    def get_context_data(self, **kwargs):
        """注入日志展示与删除选项摘要"""
        context = super().get_context_data(**kwargs)
        context["log_output_display"] = (self.object.log_output or "").strip()
        context["delete_summary"] = _parse_options_summary(self.object.options_json)
        return context


class NginxUninstallTaskLogAPIView(LoginRequiredMixin, View):
    """返回单条卸载任务日志（供日志页轮询）"""

    def get(self, request, pk):
        """读取卸载任务日志与状态"""
        if not user_has_permission(request.user, "nodes", "read"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)
        try:
            task = NginxUninstallTask.objects.select_related("node").get(pk=pk)
        except NginxUninstallTask.DoesNotExist:
            return JsonResponse({"success": False, "message": "任务不存在"}, status=404)
        return JsonResponse({
            "success": True,
            "id": task.id,
            "status": task.status,
            "status_display": task.get_status_display(),
            "progress": task.progress,
            "current_step": task.current_step,
            "log_output": task.log_output or "",
            "error_message": task.error_message or "",
        })


class NginxUninstallPreviewAPIView(LoginRequiredMixin, View):
    """预览选中节点的卸载路径与运行状态"""

    def post(self, request):
        """返回每节点 prefix / 备份路径 / 是否运行中"""
        if not user_has_permission(request.user, "nodes", "update"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "请求数据格式错误"})
        return JsonResponse(preview_nodes(data.get("node_ids") or []))


class NginxUninstallCreateAPIView(LoginRequiredMixin, View):
    """创建卸载批次并异步执行"""

    def post(self, request):
        """校验后创建任务并启动后台线程"""
        if not user_has_permission(request.user, "nodes", "update"):
            return JsonResponse({"success": False, "message": "无权限执行卸载"}, status=403)
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "请求数据格式错误"})
        return JsonResponse(create_uninstall_batch_from_data(request.user, data))


class NginxUninstallBatchProgressAPIView(LoginRequiredMixin, View):
    """按批次号查询卸载任务进度"""

    def get(self, request):
        """返回批次内各卸载任务状态"""
        if not user_has_permission(request.user, "nodes", "read"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)

        batch = (request.GET.get("batch") or "").strip()
        if not batch:
            return JsonResponse({"success": False, "message": "缺少 batch 参数"})

        tasks = list(
            NginxUninstallTask.objects.filter(batch_number=batch)
            .select_related("node", "task_center")
            .order_by("id")
        )
        if not tasks:
            return JsonResponse({"success": False, "message": "批次不存在"})

        items = []
        finished_count = 0
        success_count = 0
        fail_count = 0
        task_center_id = None
        from django.urls import reverse

        for t in tasks:
            if t.task_center_id:
                task_center_id = t.task_center_id
            finished = t.status in ("success", "failed", "cancelled")
            if finished:
                finished_count += 1
                if t.status == "success":
                    success_count += 1
                else:
                    fail_count += 1
            log_url = reverse("nginx_uninstall:task_log", kwargs={"pk": t.id})
            items.append({
                "id": t.id,
                "task_id": t.id,
                "node_id": t.node_id,
                "hostname": t.node.hostname,
                "ip": t.node.ip,
                "prefix": t.resolved_prefix,
                "status": t.status,
                "status_display": t.get_status_display(),
                "progress": t.progress,
                "current_step": t.current_step,
                "error_message": t.error_message or "",
                "task_center_id": t.task_center_id,
                "log_url": log_url,
                "finished": finished,
            })

        all_finished = finished_count == len(tasks) and len(tasks) > 0
        avg_progress = 0
        if tasks:
            avg_progress = int(sum(t.progress for t in tasks) / len(tasks))
        return JsonResponse({
            "success": True,
            "batch_number": batch,
            "task_center_id": task_center_id,
            "tasks": items,
            "finished": all_finished,
            "all_done": all_finished,
            "success_count": success_count,
            "fail_count": fail_count,
            "progress": avg_progress,
        })
