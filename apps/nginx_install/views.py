"""Nginx 全新安装：首页、向导与创建 API"""
import json
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView, ListView, TemplateView, View

from apps.upgrade.builtin_modules import BUILTIN_ADD_MODULES
from apps.upgrade.models import NginxSourcePackage, NginxThirdPartyModulePackage
from apps.users.permissions import PermissionRequiredMixin, user_has_permission
from utils.pagination import PerPagePaginationMixin
from utils.search import split_search_tags
from utils.setting_service import get_recent_tasks_limit, get_setting

from .models import NginxInstallTask
from .services import (
    DEFAULT_INSTALL_MODULES,
    batch_max_count,
    create_install_batch_from_data,
)

class NginxInstallIndexView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Nginx 安装运维台首页"""

    template_name = "nginx_install/index.html"
    permission_resource = "nginx_install"
    permission_action = "read"

    def get_context_data(self, **kwargs):
        """注入统计与最近任务"""
        context = super().get_context_data(**kwargs)
        since = timezone.now() - timedelta(days=7)
        running_statuses = [
            "pending", "uploading_package", "downloading_modules",
            "configuring", "compiling", "installing", "starting",
            "syncing_config", "verifying",
        ]
        context["package_count"] = NginxSourcePackage.objects.count()
        context["module_package_count"] = NginxThirdPartyModulePackage.objects.count()
        context["running_count"] = NginxInstallTask.objects.filter(
            status__in=running_statuses
        ).count()
        context["failed_7d_count"] = NginxInstallTask.objects.filter(
            status="failed", created_at__gte=since
        ).count()
        context["recent_tasks"] = (
            NginxInstallTask.objects.select_related("node", "operator", "source_package")
            .order_by("-created_at")[: get_recent_tasks_limit()]
        )
        context["can_create"] = user_has_permission(self.request.user, "nginx_install", "create")
        return context


class NginxInstallCenterView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Nginx 全新安装向导"""

    template_name = "nginx_install/center.html"
    permission_resource = "nginx_install"
    permission_action = "create"

    def get_context_data(self, **kwargs):
        """注入源码包、默认参数与模块列表"""
        context = super().get_context_data(**kwargs)
        context["packages"] = NginxSourcePackage.objects.order_by("-created_at")
        context["module_packages"] = NginxThirdPartyModulePackage.objects.order_by("-created_at")
        context["default_work_dir"] = get_setting(
            "upgrade.default_work_dir", "/tmp/nginx-upgrade"
        ) or "/tmp/nginx-upgrade"
        context["default_make_jobs"] = get_setting("upgrade.make_jobs_default", "4") or "4"
        context["default_prefix"] = (
            get_setting("install.default_prefix", "/opt/app") or "/opt/app"
        )
        context["default_user"] = get_setting("install.default_user", "root") or "root"
        context["default_group"] = get_setting("install.default_group", "root") or "root"
        try:
            context["default_listen_port"] = int(
                get_setting("install.default_listen_port", "80") or 80
            )
        except (TypeError, ValueError):
            context["default_listen_port"] = 80
        if (
            context["default_listen_port"] < 1
            or context["default_listen_port"] > 65535
        ):
            context["default_listen_port"] = 80
        context["default_modules"] = DEFAULT_INSTALL_MODULES
        context["builtin_modules"] = BUILTIN_ADD_MODULES
        context["builtin_modules_json"] = json.dumps(BUILTIN_ADD_MODULES, ensure_ascii=False)
        context["default_modules_json"] = json.dumps(DEFAULT_INSTALL_MODULES, ensure_ascii=False)
        context["default_prefix_json"] = json.dumps(
            context["default_prefix"], ensure_ascii=False
        )
        context["default_listen_port_json"] = json.dumps(
            context["default_listen_port"], ensure_ascii=False
        )
        pkgs = list(context["module_packages"])
        context["module_packages_json"] = json.dumps(
            [
                {
                    "id": p.id,
                    "name": p.name,
                    "version": p.version,
                    "label": f"{p.name} ({p.version})" if p.version else p.name,
                }
                for p in pkgs
            ],
            ensure_ascii=False,
        )
        context["batch_max_count"] = batch_max_count()
        return context


class NginxInstallHistoryView(LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView):
    """安装历史列表（对齐升级历史：搜索/状态/每页条数）"""

    model = NginxInstallTask
    template_name = "nginx_install/history.html"
    context_object_name = "tasks"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "nginx_install"
    permission_action = "read"

    def get_queryset(self):
        """按搜索词与状态筛选安装任务"""
        qs = NginxInstallTask.objects.select_related(
            "node", "operator", "source_package", "task_center"
        )
        search = (self.request.GET.get("search") or "").strip()
        if search:
            for term in split_search_tags(search):
                qs = qs.filter(
                    Q(node__hostname__icontains=term)
                    | Q(node__ip__icontains=term)
                    | Q(target_version__icontains=term)
                    | Q(target_prefix__icontains=term)
                    | Q(batch_number__icontains=term)
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
            NginxInstallTask.STATUS_CHOICES
        )
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        return context


class NginxInstallTaskCreateAPIView(LoginRequiredMixin, View):
    """创建全新安装批次并后台并行执行"""

    def post(self, request):
        """校验后创建 NginxInstallTask + TaskCenter 并启动线程"""
        if not user_has_permission(request.user, "nginx_install", "create"):
            return JsonResponse({"success": False, "message": "无权限创建安装任务"}, status=403)

        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "请求数据格式错误"})

        return JsonResponse(create_install_batch_from_data(request.user, data))


class NginxInstallBatchProgressAPIView(LoginRequiredMixin, View):
    """按批次号查询安装任务进度"""

    def get(self, request):
        """返回批次内各安装任务状态"""
        if not (
            user_has_permission(request.user, "nginx_install", "read")
            or user_has_permission(request.user, "releases", "read")
        ):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)

        batch = (request.GET.get("batch") or "").strip()
        if not batch:
            return JsonResponse({"success": False, "message": "缺少 batch 参数"})

        tasks = list(
            NginxInstallTask.objects.filter(batch_number=batch)
            .select_related("node", "task_center")
            .order_by("id")
        )
        if not tasks:
            return JsonResponse({"success": False, "message": "批次不存在"})

        items = []
        finished_count = 0
        success_count = 0
        fail_count = 0
        for t in tasks:
            finished = t.status in ("success", "failed", "cancelled")
            if finished:
                finished_count += 1
                if t.status == "success":
                    success_count += 1
                else:
                    fail_count += 1
            items.append({
                "id": t.id,
                "task_id": t.id,
                "node_id": t.node_id,
                "hostname": t.node.hostname,
                "ip": t.node.ip,
                "status": t.status,
                "status_display": t.get_status_display(),
                "progress": t.progress,
                "current_step": t.current_step,
                "error_message": t.error_message or "",
                "sync_ok": t.sync_ok,
                "sync_detail": t.sync_detail,
                "task_center_id": t.task_center_id,
                "log_url": reverse("nginx_install:task_log", kwargs={"pk": t.id}),
                "finished": finished,
            })

        all_finished = finished_count == len(tasks) and len(tasks) > 0
        avg_progress = 0
        if tasks:
            avg_progress = int(sum(t.progress for t in tasks) / len(tasks))
        return JsonResponse({
            "success": True,
            "batch_number": batch,
            "tasks": items,
            "finished": all_finished,
            "all_done": all_finished,
            "all_success": all_finished and fail_count == 0 and success_count == len(tasks),
            "success_count": success_count,
            "fail_count": fail_count,
            "total": len(tasks),
            "progress": avg_progress,
        })


class NginxInstallTaskLogView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """安装任务详情与完整执行日志页"""

    model = NginxInstallTask
    template_name = "nginx_install/task_log.html"
    context_object_name = "task"
    permission_resource = "nginx_install"
    permission_action = "read"

    def get_queryset(self):
        """预加载节点与源码包"""
        return NginxInstallTask.objects.select_related(
            "node", "operator", "source_package", "task_center"
        )

    def get_context_data(self, **kwargs):
        """注入日志展示文本"""
        context = super().get_context_data(**kwargs)
        context["log_output_display"] = (self.object.log_output or "").strip()
        return context


class NginxInstallTaskLogAPIView(LoginRequiredMixin, View):
    """返回单条安装任务日志（供日志页轮询）"""

    def get(self, request, pk):
        """读取安装任务日志与状态"""
        if not user_has_permission(request.user, "nginx_install", "read"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)
        try:
            task = NginxInstallTask.objects.select_related("node").get(pk=pk)
        except NginxInstallTask.DoesNotExist:
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
            "sync_ok": task.sync_ok,
            "sync_detail": task.sync_detail,
        })
