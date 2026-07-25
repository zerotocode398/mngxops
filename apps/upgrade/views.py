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
from datetime import timedelta
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
    """上传源码包（支持 AJAX、同版本覆盖、上传进度由前端 XHR 展示）"""
    model = NginxSourcePackage
    form_class = NginxSourcePackageForm
    template_name = "upgrade/package_upload.html"
    permission_resource = "upgrade"
    permission_action = "create"

    def get_form_kwargs(self):
        """传入当前用户供版本唯一性校验"""
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def _wants_json(self):
        """是否返回 JSON（XHR 上传）"""
        return (
            self.request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in self.request.headers.get("Accept", "")
        )

    def _has_version_exists_error(self, form):
        """判断是否为同版本冲突错误"""
        for err in form.non_field_errors().as_data():
            if getattr(err, "code", None) == "version_exists":
                return True
        return False

    def form_valid(self, form):
        """新建或覆盖已有同版本源码包"""
        user = self.request.user
        version = form.cleaned_data["version"]
        overwrite = form.cleaned_data.get("overwrite")
        existing = NginxSourcePackage.objects.filter(
            version=version,
            uploaded_by=user,
        ).first()

        if existing and overwrite:
            if existing.package_file:
                existing.package_file.delete(save=False)
            existing.name = form.cleaned_data["name"]
            existing.description = form.cleaned_data.get("description") or ""
            existing.is_official = bool(form.cleaned_data.get("is_official"))
            existing.package_file = form.cleaned_data["package_file"]
            existing.file_size = 0
            existing.file_md5 = ""
            existing.save()
            self.object = existing
            msg = f"源码包 {existing.name} (nginx-{existing.version}) 已覆盖更新"
        else:
            form.instance.uploaded_by = user
            self.object = form.save()
            msg = f"源码包 {self.object.name} (nginx-{self.object.version}) 上传成功"

        if self._wants_json():
            return JsonResponse({
                "success": True,
                "message": msg,
                "redirect": self.get_success_url(),
            })
        messages.success(self.request, msg)
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        """校验失败：AJAX 返回 JSON，含 need_overwrite 标记"""
        if self._wants_json():
            need_overwrite = self._has_version_exists_error(form)
            message = "版本已存在，是否覆盖？" if need_overwrite else "上传校验失败"
            if not need_overwrite and form.errors:
                # 取首条可读错误
                for field, errs in form.errors.items():
                    if errs:
                        message = errs[0]
                        break
            return JsonResponse({
                "success": False,
                "need_overwrite": need_overwrite,
                "message": message,
                "errors": form.errors,
            }, status=400)
        return super().form_invalid(form)

    def get_success_url(self):
        return reverse("upgrade:package_list")


class PackageVersionCheckView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """检查当前用户是否已上传同版本源码包（轻量预检，避免大文件重复上传）"""
    permission_resource = "upgrade"
    permission_action = "create"

    def get(self, request):
        """按 version 查询是否已存在"""
        version = (request.GET.get("version") or "").strip()
        if not version:
            return JsonResponse({"exists": False, "version": version})
        exists = NginxSourcePackage.objects.filter(
            version=version,
            uploaded_by=request.user,
        ).exists()
        return JsonResponse({"exists": exists, "version": version})


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
        if status_filter == "running":
            # 进行中 = 非终态（与首页统计一致）
            queryset = queryset.exclude(
                status__in=("success", "failed", "rollback", "cancelled")
            )
        elif status_filter:
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
        # 下拉增加「进行中」虚拟选项
        context["status_choices"] = [("running", "进行中")] + list(
            NginxUpgradeTask.STATUS_CHOICES
        )
        context["per_page"] = per_page
        context["per_page_options"] = self.per_page_options
        return context


class UpgradeTaskListView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    """Nginx 升级主页：运维操作台（统计 + 最近任务）"""
    template_name = "upgrade/index.html"
    permission_resource = "upgrade"
    permission_action = "read"

    # 进行中：非终态
    _TERMINAL_STATUSES = ("success", "failed", "rollback", "cancelled")

    def get_context_data(self, **kwargs):
        """组装首页统计与最近任务列表"""
        context = super().get_context_data(**kwargs)
        since_7d = timezone.now() - timedelta(days=7)
        context["package_count"] = NginxSourcePackage.objects.count()
        context["running_count"] = NginxUpgradeTask.objects.exclude(
            status__in=self._TERMINAL_STATUSES
        ).count()
        context["failed_7d_count"] = NginxUpgradeTask.objects.filter(
            status="failed",
            created_at__gte=since_7d,
        ).count()
        context["recent_tasks"] = (
            NginxUpgradeTask.objects
            .select_related("node", "operator", "source_package")
            .order_by("-created_at")[:10]
        )
        return context