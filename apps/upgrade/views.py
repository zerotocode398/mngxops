"""Nginx 升级模块 - 视图"""
import json
import threading

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, CreateView, TemplateView, DetailView

from .forms import NginxSourcePackageForm, NginxUpgradeTaskForm
from .models import NginxSourcePackage, NginxUpgradeTask
from .services import fetch_nginx_v_from_node, parse_nginx_v_output, compute_target_configure_opts, run_upgrade_task

from apps.users.permissions import PermissionRequiredMixin
from utils.pagination import PerPagePaginationMixin
from apps.nodes.models import Node
from apps.nodes.views import _get_node_credential


# ==================== 源码包管理 ====================

class PackageListView(LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView):
    """源码包列表"""
    model = NginxSourcePackage
    template_name = "upgrade/package_list.html"
    context_object_name = "packages"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "upgrade"
    permission_action = "read"

    def get_queryset(self):
        return super().get_queryset().select_related("uploaded_by")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        packages = self.get_queryset()
        per_page = self.get_paginate_by(None)
        paginator = Paginator(list(packages), per_page)
        page_num = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_num)

        context["packages"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        return context


class PackageUploadView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    """上传源码包"""
    model = NginxSourcePackage
    form_class = NginxSourcePackageForm
    template_name = "upgrade/package_upload.html"
    permission_resource = "upgrade"
    permission_action = "create"

    def form_valid(self, form):
        form.instance.uploaded_by = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, f"源码包 {form.instance.name} (nginx-{form.instance.version}) 上传成功")
        return response

    def get_success_url(self):
        return reverse("upgrade:package_list")


class PackageDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """删除源码包"""
    permission_resource = "upgrade"
    permission_action = "delete"

    def post(self, request, pk):
        package = get_object_or_404(NginxSourcePackage, pk=pk)
        name = str(package)
        package.package_file.delete(save=False)
        package.delete()
        messages.success(request, f"源码包 {name} 已删除")
        return redirect("upgrade:package_list")


class PackageDownloadView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """下载源码包"""
    permission_resource = "upgrade"
    permission_action = "read"

    def get(self, request, pk):
        from django.http import FileResponse
        package = get_object_or_404(NginxSourcePackage, pk=pk)
        response = FileResponse(
            package.package_file.open("rb"),
            as_attachment=True,
            filename=package.package_file.name.split("/")[-1],
        )
        return response


# ==================== 升级中心 ====================

class UpgradeCenterView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """升级中心主页面"""
    template_name = "upgrade/center.html"
    permission_resource = "upgrade"
    permission_action = "read"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["nodes"] = Node.objects.filter(is_locked=False).order_by("hostname")
        context["packages"] = NginxSourcePackage.objects.order_by("-created_at")
        context["latest_tasks"] = NginxUpgradeTask.objects.select_related("node", "operator").order_by("-created_at")[:10]
        return context


# ==================== API 接口 ====================

class NginxVApiView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """获取目标节点 nginx -V 输出 (Ajax)"""
    permission_resource = "upgrade"
    permission_action = "create"

    def post(self, request, node_id):
        node = get_object_or_404(Node, pk=node_id)
        if node.is_locked:
            return JsonResponse({"success": False, "message": "节点已锁定"}, status=400)

        success, result = fetch_nginx_v_from_node(node)
        if not success:
            return JsonResponse({"success": False, "message": result}, status=400)

        return JsonResponse({"success": True, "data": result})


class ParseConfigApiView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """解析 nginx -V 输出为结构化参数 (Ajax)"""
    permission_resource = "upgrade"
    permission_action = "create"

    def post(self, request):
        raw_output = request.POST.get("raw_output", "")
        if not raw_output:
            return JsonResponse({"success": False, "message": "缺少原始输出"}, status=400)

        parsed = parse_nginx_v_output(raw_output)
        return JsonResponse({"success": True, "data": parsed})


class ComputeConfigApiView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """计算调整后的编译参数预览 (Ajax)"""
    permission_resource = "upgrade"
    permission_action = "create"

    def post(self, request):
        try:
            current_params = json.loads(request.POST.get("current_params", "[]"))
            added_modules = json.loads(request.POST.get("added_modules", "[]"))
            removed_modules = json.loads(request.POST.get("removed_modules", "[]"))
            added_third_party = json.loads(request.POST.get("added_third_party", "[]"))
        except json.JSONDecodeError:
            return JsonResponse({"success": False, "message": "JSON 解析失败"}, status=400)

        target_opts = compute_target_configure_opts(
            current_params, added_modules, removed_modules, added_third_party
        )
        return JsonResponse({"success": True, "target_opts": target_opts})


# ==================== 升级任务 CRUD ====================

class UpgradeTaskCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """创建升级任务 (Ajax)"""
    permission_resource = "upgrade"
    permission_action = "create"

    def post(self, request):
        form = NginxUpgradeTaskForm(request.POST)
        if not form.is_valid():
            errors = {k: [str(e) for e in v] for k, v in form.errors.items()}
            return JsonResponse({"success": False, "message": "表单验证失败", "errors": errors}, status=400)

        task = form.save(commit=False)
        task.operator = request.user
        task.status = "pending"
        task.current_step = "任务已创建，等待执行"
        task.save()

        # 创建关联的任务中心记录
        from apps.releases.models import TaskCenterTask
        task_center = TaskCenterTask.objects.create(
            operation_type="nginx_upgrade",
            status="pending",
            detail=f"Nginx 升级: {task.node.hostname} → nginx-{task.target_version}",
            target_hostnames=task.node.hostname,
            target_ips=task.node.ip,
            trigger_user=request.user,
        )
        task.task_center = task_center
        task.save(update_fields=["task_center"])

        # 在线程中执行升级
        thread = threading.Thread(target=run_upgrade_task, args=(task.id,), daemon=True)
        thread.start()

        return JsonResponse({
            "success": True,
            "task_id": task.id,
            "progress_url": reverse("upgrade:task_progress", kwargs={"pk": task.id}),
        })


class UpgradeTaskProgressView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """获取升级进度 (Ajax 轮询)"""
    permission_resource = "upgrade"
    permission_action = "read"

    def get(self, request, pk):
        task = get_object_or_404(NginxUpgradeTask, pk=pk)
        return JsonResponse({
            "success": True,
            "task_id": task.id,
            "status": task.status,
            "status_display": task.get_status_display(),
            "progress": task.progress,
            "current_step": task.current_step,
            "error_message": task.error_message,
            "current_version": task.current_version,
            "target_version": task.target_version,
            "log_output": task.log_output[-50000:] if task.log_output else "",
        })


class UpgradeTaskLogView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    """查看完整升级日志"""
    model = NginxUpgradeTask
    template_name = "upgrade/task_log.html"
    context_object_name = "task"
    permission_resource = "upgrade"
    permission_action = "read"


class UpgradeTaskCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """取消升级任务"""
    permission_resource = "upgrade"
    permission_action = "update"

    def post(self, request, pk):
        task = get_object_or_404(NginxUpgradeTask, pk=pk)
        if task.status not in ("pending", "fetching_config", "uploading_package"):
            return JsonResponse({"success": False, "message": "当前状态不允许取消"}, status=400)

        task.status = "cancelled"
        task.error_message = "用户手动取消"
        task.finished_at = timezone.now()
        task.save()

        if task.task_center:
            task.task_center.status = "cancelled"
            task.task_center.result = "用户手动取消"
            task.task_center.finished_at = timezone.now()
            task.task_center.save(update_fields=["status", "result", "finished_at"])

        messages.success(request, "升级任务已取消")
        return JsonResponse({"success": True})


class UpgradeTaskRollbackView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """回滚升级任务"""
    permission_resource = "upgrade"
    permission_action = "update"

    def post(self, request, pk):
        task = get_object_or_404(NginxUpgradeTask, pk=pk)
        if task.status not in ("success", "failed"):
            return JsonResponse({"success": False, "message": "当前状态不允许回滚"}, status=400)

        # 执行回滚操作
        node = task.node
        credential = _get_node_credential(node)
        if not credential:
            return JsonResponse({"success": False, "message": "节点未配置有效的 SSH 凭证"}, status=400)

        if not task.old_binary_backup:
            return JsonResponse({"success": False, "message": "没有可用的备份文件"}, status=400)

        from utils.ssh import SSHClient
        auth_kwargs = {}
        if credential.auth_type == "password":
            auth_kwargs["password"] = credential.get_password()
        else:
            auth_kwargs["private_key"] = credential.get_private_key()

        binary_path = task.current_binary_path or task.current_configure_path.rstrip("/") + "/sbin/nginx"

        try:
            with SSHClient(node.ip, node.port, credential.username, **auth_kwargs) as ssh:
                # 恢复旧二进制
                success, output = ssh.execute_command(
                    f"cp {task.old_binary_backup} {binary_path} 2>&1"
                )
                if not success:
                    return JsonResponse({"success": False, "message": f"回滚失败: {output}"}, status=500)

                # 如果旧 master 仍在，用 HUP 唤醒旧 worker
                pid_file = (task.current_configure_path or "").rstrip("/") + "/logs/nginx.pid"
                success, output = ssh.execute_command(f"cat {pid_file}.oldbin 2>/dev/null")
                if success and output.strip():
                    old_pid = output.strip()
                    ssh.execute_command(f"kill -HUP {old_pid} 2>&1")
                    # 退出新 master
                    success, output = ssh.execute_command(f"cat {pid_file} 2>/dev/null")
                    if success and output.strip():
                        new_pid = output.strip()
                        ssh.execute_command(f"kill -QUIT {new_pid} 2>&1")

                # reload
                ssh.execute_command(f"{binary_path} -s reload 2>&1")
        except Exception as e:
            return JsonResponse({"success": False, "message": f"回滚异常: {str(e)}"}, status=500)

        task.status = "rollback"
        task.finished_at = timezone.now()
        task.error_message = "已手动回滚到旧版本"
        task.save()

        if task.task_center:
            task.task_center.status = "cancelled"
            task.task_center.result = "已回滚到旧版本"
            task.task_center.finished_at = timezone.now()
            task.task_center.save(update_fields=["status", "result", "finished_at"])

        messages.success(request, f"Nginx 已回滚到备份版本 ({task.old_binary_backup})")
        return JsonResponse({"success": True})


# ==================== 升级历史 ====================

class UpgradeHistoryView(LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView):
    """升级历史列表"""
    model = NginxUpgradeTask
    template_name = "upgrade/history.html"
    context_object_name = "tasks"
    paginate_by = None
    ordering = ["-created_at"]
    permission_resource = "upgrade"
    permission_action = "read"

    def get_queryset(self):
        queryset = super().get_queryset().select_related("node", "operator", "source_package")
        search = self.request.GET.get("search", "")
        if search:
            queryset = queryset.filter(
                Q(node__hostname__icontains=search)
                | Q(node__ip__icontains=search)
                | Q(target_version__icontains=search)
                | Q(current_version__icontains=search)
            )
        status_filter = self.request.GET.get("status", "")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tasks = self.get_queryset()
        per_page = self.get_paginate_by(None)
        paginator = Paginator(list(tasks), per_page)
        page_num = self.request.GET.get("page", 1)
        page_obj = paginator.get_page(page_num)

        context["tasks"] = page_obj.object_list
        context["page_obj"] = page_obj
        context["is_paginated"] = page_obj.has_other_pages()
        context["search"] = self.request.GET.get("search", "")
        context["status_filter"] = self.request.GET.get("status", "")
        context["status_choices"] = NginxUpgradeTask.STATUS_CHOICES
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        return context


class UpgradeTaskListView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Nginx 升级主页（兼容旧路由）"""
    template_name = "upgrade/index.html"
    permission_resource = "upgrade"
    permission_action = "read"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["packages"] = NginxSourcePackage.objects.order_by("-created_at")[:5]
        context["nodes"] = Node.objects.filter(is_locked=False).order_by("hostname")
        return context