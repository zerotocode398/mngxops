"""OpenSSH 升级：首页、向导、历史、详情与 API"""
import json
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import DetailView, ListView, TemplateView, View

from apps.users.permissions import PermissionRequiredMixin, user_has_permission
from utils.pagination import PerPagePaginationMixin
from utils.setting_service import get_recent_tasks_limit

from .models import OpenSSHSourcePackage, OpenSSHUpgradeTask
from .services import (
    batch_max_count,
    create_batch_from_data,
    create_rollback_batch_from_data,
    default_configure_opts,
    default_reconnect_grace,
    default_test_port,
    default_work_dir,
    preview_nodes,
)

_ACTIVE_STATUSES = (
    "pending", "probing", "building", "verifying",
    "backing_up", "switching", "confirming",
)


def _timezone_now_minus_7d():
    from django.utils import timezone

    return timezone.now() - timedelta(days=7)


class OpenSSHUpgradeIndexView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """OpenSSH 升级运维台首页"""

    template_name = "openssh_upgrade/index.html"
    permission_resource = "openssh_upgrade"
    permission_action = "read"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        since = _timezone_now_minus_7d()
        context["running_count"] = OpenSSHUpgradeTask.objects.filter(
            status__in=_ACTIVE_STATUSES
        ).count()
        context["success_7d_count"] = OpenSSHUpgradeTask.objects.filter(
            status="success", created_at__gte=since
        ).count()
        context["failed_7d_count"] = OpenSSHUpgradeTask.objects.filter(
            status__in=("failed", "rolled_back"), created_at__gte=since
        ).count()
        context["total_count"] = OpenSSHUpgradeTask.objects.count()
        context["recent_tasks"] = (
            OpenSSHUpgradeTask.objects.select_related("node", "operator", "task_center")
            .order_by("-created_at")[: get_recent_tasks_limit()]
        )
        context["can_execute"] = user_has_permission(
            self.request.user, "openssh_upgrade", "create"
        )
        return context


class OpenSSHUpgradeCenterView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """OpenSSH 升级三步向导"""

    template_name = "openssh_upgrade/center.html"
    permission_resource = "openssh_upgrade"
    permission_action = "create"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["batch_max_count"] = batch_max_count()
        context["can_execute"] = user_has_permission(
            self.request.user, "openssh_upgrade", "create"
        )
        context["packages"] = list(
            OpenSSHSourcePackage.objects.select_related("uploaded_by").order_by("-created_at")
        )
        context["defaults"] = upgrade_defaults_json()
        return context


class OpenSSHUpgradeHistoryView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """OpenSSH 升级/回滚历史列表"""

    model = OpenSSHUpgradeTask
    template_name = "openssh_upgrade/history.html"
    context_object_name = "tasks"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "openssh_upgrade"
    permission_action = "read"

    def get_queryset(self):
        qs = OpenSSHUpgradeTask.objects.select_related(
            "node", "operator", "task_center", "source_package"
        )
        search = (self.request.GET.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(node__hostname__icontains=search)
                | Q(node__ip__icontains=search)
                | Q(batch_number__icontains=search)
                | Q(current_version__icontains=search)
                | Q(target_version__icontains=search)
            )
        status = (self.request.GET.get("status") or "").strip()
        if status == "running":
            qs = qs.exclude(
                status__in=["success", "failed", "rolled_back", "cancelled"]
            )
        elif status:
            qs = qs.filter(status=status)
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
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
            OpenSSHUpgradeTask.STATUS_CHOICES
        )
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        return context


class OpenSSHUpgradeTaskLogView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """OpenSSH 升级任务详情与完整执行日志页"""

    model = OpenSSHUpgradeTask
    template_name = "openssh_upgrade/task_log.html"
    context_object_name = "task"
    permission_resource = "openssh_upgrade"
    permission_action = "read"

    def get_queryset(self):
        return OpenSSHUpgradeTask.objects.select_related(
            "node", "operator", "task_center", "source_package"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["log_output_display"] = (self.object.log_output or "").strip()
        context["can_rollback"] = (
            self.object.action == "upgrade"
            and self.object.backup_dir
            and bool(self.object.backup_manifest_json)
            and self.object.backup_manifest_json != "{}"
            and self.object.status in ("success", "failed", "rolled_back")
        )
        return context


class OpenSSHUpgradeTaskLogAPIView(LoginRequiredMixin, View):
    """返回单条任务日志（供日志页轮询）"""

    def get(self, request, pk):
        if not user_has_permission(request.user, "openssh_upgrade", "read"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)
        try:
            task = OpenSSHUpgradeTask.objects.select_related("node").get(pk=pk)
        except OpenSSHUpgradeTask.DoesNotExist:
            return JsonResponse({"success": False, "message": "任务不存在"}, status=404)
        return JsonResponse({
            "success": True,
            "id": task.id,
            "action": task.action,
            "status": task.status,
            "status_display": task.get_status_display(),
            "progress": task.progress,
            "current_step": task.current_step,
            "log_output": task.log_output or "",
            "error_message": task.error_message or "",
        })


class OpenSSHUpgradePreviewAPIView(LoginRequiredMixin, View):
    """预览选中节点的 OpenSSH 升级条件"""

    def post(self, request):
        if not user_has_permission(request.user, "openssh_upgrade", "create"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "请求数据格式错误"})
        return JsonResponse(preview_nodes(data.get("node_ids") or []))


class OpenSSHUpgradeCreateAPIView(LoginRequiredMixin, View):
    """创建升级批次并异步执行"""

    def post(self, request):
        if not user_has_permission(request.user, "openssh_upgrade", "create"):
            return JsonResponse({"success": False, "message": "无权限执行升级"}, status=403)
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "请求数据格式错误"})
        return JsonResponse(create_batch_from_data(request.user, data, action="upgrade"))


class OpenSSHUpgradeRollbackAPIView(LoginRequiredMixin, View):
    """创建手动回滚批次并异步执行"""

    def post(self, request):
        if not user_has_permission(request.user, "openssh_upgrade", "update"):
            return JsonResponse({"success": False, "message": "无权限执行回滚"}, status=403)
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "请求数据格式错误"})
        return JsonResponse(create_rollback_batch_from_data(request.user, data))


class OpenSSHUpgradeBatchProgressAPIView(LoginRequiredMixin, View):
    """按批次号查询升级/回滚任务进度"""

    def get(self, request):
        if not user_has_permission(request.user, "openssh_upgrade", "read"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)

        batch = (request.GET.get("batch") or "").strip()
        if not batch:
            return JsonResponse({"success": False, "message": "缺少 batch 参数"})

        tasks = list(
            OpenSSHUpgradeTask.objects.filter(batch_number=batch)
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

        terminal = ("success", "failed", "rolled_back", "cancelled")
        for t in tasks:
            if t.task_center_id:
                task_center_id = t.task_center_id
            finished = t.status in terminal
            if finished:
                finished_count += 1
                if t.status == "success":
                    success_count += 1
                else:
                    fail_count += 1
            log_url = reverse(
                "openssh_upgrade:task_log", kwargs={"pk": t.id}
            )
            items.append({
                "id": t.id,
                "task_id": t.id,
                "action": t.action,
                "node_id": t.node_id,
                "hostname": t.node.hostname,
                "ip": t.node.ip,
                "current_version": t.current_version,
                "target_version": t.target_version,
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
        avg_progress = int(sum(t.progress for t in tasks) / len(tasks)) if tasks else 0
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


class OpenSSHPackageListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    """OpenSSH 源码包列表"""

    model = OpenSSHSourcePackage
    template_name = "openssh_upgrade/package_list.html"
    context_object_name = "packages"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "openssh_upgrade"
    permission_action = "read"

    def get_queryset(self):
        return OpenSSHSourcePackage.objects.select_related("uploaded_by").order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        packages = list(self.get_queryset())
        per_page = self.get_paginate_by(None)
        paginator = Paginator(packages, per_page)
        page_obj = paginator.get_page(self.request.GET.get("page", 1))
        context["packages"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        context["can_upload"] = user_has_permission(
            self.request.user, "openssh_upgrade", "create"
        )
        return context


class OpenSSHPackageUploadView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """OpenSSH 源码包上传页"""

    template_name = "openssh_upgrade/package_upload.html"
    permission_resource = "openssh_upgrade"
    permission_action = "create"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context["package_max_size_mb"] = max(
                1, int(__import__("utils.setting_service", fromlist=["get_setting"]).get_setting(
                    "openssh.package_max_size_mb", "20"
                ) or 20)
            )
        except (TypeError, ValueError):
            context["package_max_size_mb"] = 20
        return context


class OpenSSHPackageUploadAPIView(LoginRequiredMixin, View):
    """OpenSSH 源码包上传 API（multipart）"""

    def post(self, request):
        if not user_has_permission(request.user, "openssh_upgrade", "create"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)
        try:
            import hashlib

            from utils.setting_service import get_setting

            name = (request.POST.get("name") or "").strip()
            version = (request.POST.get("version") or "").strip()
            description = (request.POST.get("description") or "").strip()
            f = request.FILES.get("package_file")
            if not name or not version or not f:
                return JsonResponse({"success": False, "message": "请填写名称、版本并选择文件"})
            if not (f.name.endswith(".tar.gz") or f.name.endswith(".tgz")):
                return JsonResponse({"success": False, "message": "仅支持 .tar.gz / .tgz"})
            try:
                max_mb = max(1, int(get_setting("openssh.package_max_size_mb", "20") or 20))
            except (TypeError, ValueError):
                max_mb = 20
            if f.size > max_mb * 1024 * 1024:
                return JsonResponse(
                    {"success": False, "message": f"文件超过 {max_mb}MB 限制"}
                )
            if OpenSSHSourcePackage.objects.filter(
                version=version, uploaded_by=request.user
            ).exists():
                return JsonResponse({"success": False, "message": "该版本已存在，请勿重复上传"})
            pkg = OpenSSHSourcePackage(
                name=name,
                version=version,
                package_file=f,
                description=description,
                uploaded_by=request.user,
            )
            pkg.save()
            pkg.package_file.seek(0)
            pkg.file_size = pkg.package_file.size
            pkg.file_md5 = hashlib.md5(pkg.package_file.read()).hexdigest()
            pkg.save(update_fields=["file_size", "file_md5"])
            return JsonResponse({"success": True, "message": "上传成功", "id": pkg.id})
        except Exception as exc:
            return JsonResponse({"success": False, "message": f"上传失败: {exc}"})


class OpenSSHPackageDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """删除 OpenSSH 源码包"""

    permission_resource = "openssh_upgrade"
    permission_action = "create"

    def post(self, request, pk):
        pkg = get_object_or_404(OpenSSHSourcePackage, pk=pk)
        pkg.package_file.delete(save=False)
        pkg.delete()
        return redirect("openssh_upgrade:packages")


class OpenSSHPackageDeleteAPIView(LoginRequiredMixin, View):
    """删除 OpenSSH 源码包（AJAX）"""

    def post(self, request, pk):
        if not user_has_permission(request.user, "openssh_upgrade", "create"):
            return JsonResponse({"success": False, "message": "无权限"}, status=403)
        pkg = get_object_or_404(OpenSSHSourcePackage, pk=pk)
        pkg.package_file.delete(save=False)
        pkg.delete()
        return JsonResponse({"success": True, "message": "已删除"})


def upgrade_defaults_json():
    """供向导初始值使用（与 preview defaults 保持一致）"""
    return {
        "work_dir": default_work_dir(),
        "test_port": default_test_port(),
        "reconnect_grace_seconds": default_reconnect_grace(),
        "configure_opts": default_configure_opts(),
        "make_jobs": 4,
        "auto_rollback": True,
    }