import json
from datetime import datetime

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, get_object_or_404, render
from django.urls import reverse
from django.views.generic import ListView, DetailView, CreateView, View
from django.http import JsonResponse

from apps.nodes.views import _get_node_credential
from apps.nodes.models import Node
from apps.configs.models import Config, ConfigVersion
from apps.users.permissions import PermissionRequiredMixin
from utils.ssh import (
    backup_remote_file,
    upload_file_via_sftp,
    execute_nginx_test,
    execute_nginx_reload,
)

from .forms import ReleaseCreateForm
from .models import ReleaseTask, ReleaseHistory, generate_batch_number
from utils.pagination import PerPagePaginationMixin


class ReleaseExecutorMixin:
    def _execute_release(self, task, action):
        request = self.request
        node = task.node
        config = task.config
        version = task.version

        credential = _get_node_credential(node)
        if not credential:
            task.status = "failed"
            task.result = f"节点 {node.hostname} 未配置 SSH 凭证"
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, task.result

        log_lines = []

        def add_log(msg):
            log_lines.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

        task.status = "running"
        task.started_at = datetime.now()
        task.save()

        kwargs = {
            "host": node.ip,
            "port": node.port,
            "username": credential.username,
        }
        if credential.auth_type == "password":
            kwargs["password"] = credential.password
        else:
            kwargs["private_key"] = credential.private_key

        add_log(f"开始发布: {config.name} v{version.version} → {node.hostname}")
        add_log(f"目标路径: {config.file_path}")

        add_log("正在备份原配置...")
        success, backup_result = backup_remote_file(
            file_path=config.file_path,
            **kwargs,
        )
        if success:
            add_log(f"备份成功: {backup_result}")
        else:
            add_log(f"备份失败: {backup_result}")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"备份失败: {backup_result}"

        add_log("正在上传配置...")
        success, upload_result = upload_file_via_sftp(
            remote_path=config.file_path,
            content=version.content,
            **kwargs,
        )
        if success:
            add_log("上传成功")
        else:
            add_log(f"上传失败: {upload_result}")
            add_log("正在回滚备份...")
            rollback_success, rollback_result = upload_file_via_sftp(
                remote_path=config.file_path,
                content="",
                **kwargs,
            )
            if not rollback_success:
                add_log(f"回滚失败: {rollback_result}")
            else:
                add_log("回滚完成")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"上传失败: {upload_result}"

        add_log("正在执行 nginx -t ...")
        nginx_path = node.nginx_path or None
        success, test_output = execute_nginx_test(
            config_path=config.file_path,
            nginx_path=nginx_path,
            **kwargs,
        )
        add_log(test_output)
        if not success:
            add_log("nginx -t 失败，正在回滚备份...")
            upload_file_via_sftp(
                remote_path=config.file_path,
                content="",
                **kwargs,
            )
            add_log("回滚完成")
            task.status = "failed"
            task.result = "\n".join(log_lines)
            task.finished_at = datetime.now()
            task.save()
            self._record_history(task, action, task.result)
            return False, f"nginx -t 失败: {test_output}"

        add_log("nginx -t 通过，正在执行 reload...")
        success, reload_output = execute_nginx_reload(
            nginx_path=nginx_path,
            **kwargs,
        )
        add_log(reload_output)
        if success:
            add_log("发布成功!")
            task.status = "success"
        else:
            add_log("reload 失败，正在回滚备份...")
            upload_file_via_sftp(
                remote_path=config.file_path,
                content="",
                **kwargs,
            )
            add_log("回滚完成")
            task.status = "failed"

        task.result = "\n".join(log_lines)
        task.finished_at = datetime.now()
        task.save()
        self._record_history(task, action, task.result)
        return (
            True,
            f"配置 {config.name} v{version.version} 发布到 {node.hostname} 成功",
        )

    def _record_history(self, task, action, result):
        ReleaseHistory.objects.create(
            release_task=task,
            node=task.node,
            config=task.config,
            version=task.version.version,
            operator=task.operator,
            action=action,
            result=result,
        )


class ReleaseCreateView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, CreateView
):
    model = ReleaseTask
    form_class = ReleaseCreateForm
    template_name = "releases/create.html"
    permission_resource = "releases"
    permission_action = "create"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return context

    def post(self, request, *args, **kwargs):
        from apps.configs.models import Config, ConfigVersion

        node_ids = request.POST.getlist("node_ids")
        config_ids = request.POST.getlist("config_ids")

        # Parse version_ids mapping: config_id → version_id
        version_ids = {}
        version_ids_raw = request.POST.getlist("version_ids")
        for item in version_ids_raw:
            if ":" in item:
                cid, vid = item.split(":", 1)
                version_ids[int(cid)] = int(vid)

        if not node_ids or not config_ids:
            messages.error(request, "请至少选择一个节点和一个配置")
            return redirect("releases:create")

        if len(node_ids) > 5 or len(config_ids) > 5:
            messages.error(request, "单次发布最多选择 5 个节点和 5 个配置")
            return redirect("releases:create")

        batch_number = generate_batch_number()
        created_count = 0

        for node_id in node_ids:
            node = get_object_or_404(Node, id=node_id)
            for config_id in config_ids:
                config = get_object_or_404(Config, id=config_id, node_id=node_id)

                # Use specified version if available, otherwise latest
                if config_id in version_ids:
                    version = get_object_or_404(
                        ConfigVersion, id=version_ids[config_id], config_id=config_id
                    )
                else:
                    version = config.versions.order_by("-version").first()

                if not version:
                    continue

                ReleaseTask.objects.create(
                    batch_number=batch_number,
                    node=node,
                    config=config,
                    version=version,
                    operator=request.user,
                    status="pending",
                )
                created_count += 1

        if created_count == 0:
            messages.error(request, "未找到可发布的配置版本")
            return redirect("releases:create")

        messages.success(
            request,
            f"发布任务已创建，批次号: {batch_number}，共 {created_count} 个任务，请在发布中心确认后执行",
        )
        return redirect("releases:center")


class ReleaseListView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = ReleaseTask
    template_name = "releases/list.html"
    context_object_name = "tasks"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("node", "config", "version", "operator")
        )
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")

        if search:
            queryset = queryset.filter(
                Q(config__name__icontains=search)
                | Q(node__hostname__icontains=search)
                | Q(operator__username__icontains=search)
            )
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from collections import OrderedDict

        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")

        context["search"] = search
        context["status_filter"] = status_filter
        context["status_choices"] = ReleaseTask.STATUS_CHOICES

        node_tasks = OrderedDict()
        for task in context["tasks"]:
            node_tasks.setdefault(task.node, []).append(task)

        context["node_tasks"] = node_tasks
        context["has_any_filter"] = bool(search or status_filter)
        return context


class ReleaseDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = ReleaseTask
    template_name = "releases/detail.html"
    context_object_name = "task"
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("node", "config", "version", "operator")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["histories"] = self.object.history.all().select_related(
            "node", "config", "operator"
        )
        return context


class ReleaseRollbackView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "update"

    def get(self, request, pk):
        from django.core.paginator import Paginator

        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "version", "operator"),
            pk=pk,
        )
        config = task.config
        versions = (
            ConfigVersion.objects.filter(config=config)
            .select_related("created_by")
            .order_by("-version")
        )
        paginator = Paginator(versions, 15)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)

        return render(
            request,
            "releases/rollback.html",
            {
                "task": task,
                "config": config,
                "page_obj": page_obj,
            },
        )

    def post(self, request, pk):
        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "version", "operator"),
            pk=pk,
        )
        version_id = request.POST.get("version_id")
        if not version_id:
            messages.error(request, "请选择要回滚的版本")
            return redirect("releases:rollback", pk=task.pk)

        version = get_object_or_404(ConfigVersion, pk=version_id, config=task.config)

        new_task = ReleaseTask.objects.create(
            node=task.node,
            config=task.config,
            version=version,
            operator=request.user,
            status="pending",
            batch_number=generate_batch_number(),
        )

        messages.success(
            request,
            f"回滚任务已创建，批次号: {new_task.batch_number}，请在发布中心确认后执行",
        )
        return redirect("releases:center")


class ReleaseCenterView(
    LoginRequiredMixin, PermissionRequiredMixin, PerPagePaginationMixin, ListView
):
    model = ReleaseTask
    template_name = "releases/center.html"
    context_object_name = "tasks"
    paginate_by = 10
    ordering = ["-created_at"]
    permission_resource = "releases"
    permission_action = "read"

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related("node", "config", "version", "operator")
            .filter(status__in=["pending", "running"])
        )
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")

        if search:
            queryset = queryset.filter(
                Q(batch_number__icontains=search)
                | Q(config__name__icontains=search)
                | Q(node__hostname__icontains=search)
                | Q(operator__username__icontains=search)
            )
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return queryset

    def get_context_data(self, **kwargs):
        from collections import OrderedDict
        from django.core.paginator import Paginator

        context = super().get_context_data(**kwargs)
        search = self.request.GET.get("search", "")
        status_filter = self.request.GET.get("status", "")

        context["search"] = search
        context["status_filter"] = status_filter
        context["status_choices"] = ReleaseTask.STATUS_CHOICES

        batch_tasks = OrderedDict()
        for task in context["tasks"]:
            key = task.batch_number or f"task-{task.id}"
            batch_tasks.setdefault(key, []).append(task)

        context["batch_tasks"] = batch_tasks
        context["has_any_filter"] = bool(search or status_filter)

        recent_history = (
            ReleaseTask.objects.select_related("node", "config", "version", "operator")
            .exclude(status__in=["pending", "running"])
            .order_by("-created_at")
        )

        history_search = self.request.GET.get("history_search", "")
        history_status = self.request.GET.get("history_status", "")
        if history_search:
            recent_history = recent_history.filter(
                Q(config__name__icontains=history_search)
                | Q(node__hostname__icontains=history_search)
                | Q(operator__username__icontains=history_search)
            )
        if history_status:
            recent_history = recent_history.filter(status=history_status)

        paginator = Paginator(recent_history, 10)
        history_page = paginator.get_page(self.request.GET.get("history_page", 1))
        context["history_page"] = history_page
        context["history_search"] = history_search
        context["history_status"] = history_status
        context["is_history_paginated"] = history_page.has_other_pages()

        return context


class ReleaseCenterExecuteView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View
):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if ReleaseTask.objects.filter(status="running").exists():
            msg = "当前有批次正在执行中，请等待完成后再执行"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg})
            messages.error(request, msg)
            return redirect("releases:center")

        tasks = ReleaseTask.objects.filter(
            batch_number=batch_number,
            status__in=["pending"],
        ).select_related("node", "config", "version", "operator")

        if not tasks.exists():
            msg = f"批次 {batch_number} 没有可执行的任务"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg})
            messages.error(request, msg)
            return redirect("releases:center")

        action = "publish"
        success_count = 0
        fail_count = 0
        results = []

        for task in tasks:
            ok, msg = self._execute_release(task, action)
            results.append(
                {
                    "task_id": task.id,
                    "node": task.node.hostname,
                    "config": task.config.name,
                    "status": task.status,
                    "message": msg,
                }
            )
            if task.status == "success":
                success_count += 1
            else:
                fail_count += 1

        summary = f"批次 {batch_number}：成功 {success_count}，失败 {fail_count}"
        if is_ajax:
            return JsonResponse(
                {
                    "success": fail_count == 0,
                    "message": summary,
                    "results": results,
                    "success_count": success_count,
                    "fail_count": fail_count,
                }
            )

        if fail_count > 0:
            messages.error(request, summary)
        else:
            messages.success(request, summary)
        return redirect("releases:center")


class ReleaseCenterSingleExecuteView(
    LoginRequiredMixin, PermissionRequiredMixin, ReleaseExecutorMixin, View
):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, task_id):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if ReleaseTask.objects.filter(status="running").exists():
            msg = "当前有批次正在执行中，请等待完成后再执行"
            if is_ajax:
                return JsonResponse({"success": False, "message": msg})
            messages.error(request, msg)
            return redirect("releases:center")

        task = get_object_or_404(
            ReleaseTask.objects.select_related("node", "config", "version", "operator"),
            pk=task_id,
            status__in=["pending", "failed"],
        )

        action = "publish"
        ok, msg = self._execute_release(task, action)

        if is_ajax:
            return JsonResponse(
                {
                    "success": task.status == "success",
                    "message": msg,
                    "task_id": task.id,
                    "node": task.node.hostname,
                    "config": task.config.name,
                    "status": task.status,
                }
            )

        if task.status == "success":
            messages.success(request, msg)
        else:
            messages.error(request, msg)
        return redirect("releases:center")


class ReleaseCenterCancelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_resource = "releases"
    permission_action = "update"

    def post(self, request, batch_number):
        updated = ReleaseTask.objects.filter(
            batch_number=batch_number,
            status__in=["pending"],
        ).update(status="cancelled")

        if updated:
            messages.success(
                request,
                f"批次 {batch_number} 已取消（{updated} 个任务）",
            )
        else:
            messages.warning(request, f"批次 {batch_number} 没有可取消的任务")
        return redirect("releases:center")


class VersionContentAPIView(LoginRequiredMixin, View):
    def get(self, request, version_id):
        version = get_object_or_404(
            ConfigVersion.objects.select_related("config", "created_by"),
            pk=version_id,
        )
        return JsonResponse(
            {
                "success": True,
                "data": {
                    "version_id": version.id,
                    "version_number": version.version,
                    "version_label": version.version_label,
                    "config_name": version.config.name,
                    "config_path": version.config.file_path,
                    "content": version.content,
                    "content_bytes": version.content_bytes,
                    "remark": version.remark or "",
                    "created_at": version.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "created_by": version.created_by.username,
                },
            }
        )
